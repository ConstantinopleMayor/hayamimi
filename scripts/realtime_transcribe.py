"""Real-time (or simulated real-time) multilingual transcription pipeline.

Audio -> Silero VAD (sherpa-onnx) -> RoutedASR (asr_engine.RoutedASR) -> print.

Usage:
    python scripts/realtime_transcribe.py --wav testdata/ja_test.wav --no-realtime
    python scripts/realtime_transcribe.py --wav testdata/ja_test.wav       # paced with sleeps
    python scripts/realtime_transcribe.py                                 # live microphone
    python scripts/realtime_transcribe.py --input speaker                 # PC audio output (WASAPI loopback, Windows)
    python scripts/realtime_transcribe.py --input mix                     # mic + PC audio output, summed
"""
import argparse
import itertools
import os
import queue
import sys
import threading
import time
import wave
from collections import deque
from contextlib import contextmanager
from typing import NamedTuple

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import sherpa_onnx

from asr_engine import RoutedASR
from audio_utils import resample_linear

SAMPLE_RATE = 16000
# Monotonic utterance id so async translation events can be attached to
# the exact finalized line they translate (front-ends key on this "seq").
_UTT_SEQ = itertools.count(1)
_UTT_SEQ_LOCK = threading.Lock()

WINDOW_SIZE = 512  # samples per VAD chunk, ~32ms @ 16kHz
MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
VAD_MODEL = os.path.join(MODELS_DIR, "silero_vad.onnx")


def read_wave(path: str, target_rate: int = SAMPLE_RATE):
    with wave.open(path, "rb") as f:
        assert f.getsampwidth() == 2, f"{path}: expected 16-bit PCM"
        num_channels = f.getnchannels()
        sample_rate = f.getframerate()
        data = f.readframes(f.getnframes())
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if num_channels > 1:
        samples = samples.reshape(-1, num_channels).mean(axis=1)
    if sample_rate != target_rate:
        samples = resample_linear(samples, sample_rate, target_rate)
        sample_rate = target_rate
    return samples, sample_rate


def build_vad(min_silence: float = 0.35,
              max_speech: float = 12.0) -> sherpa_onnx.VoiceActivityDetector:
    # 0.35s endpointing measured CER-neutral vs 0.5s on real broadcast ja
    # (docs/BENCHMARKS.md iteration 9) and finalizes 150ms sooner. max_speech
    # force-splits breathless monologues (radio/game commentary hit 21s
    # segments) so finals stay timely; the refine pass re-merges the group.
    cfg = sherpa_onnx.VadModelConfig(
        silero_vad=sherpa_onnx.SileroVadModelConfig(
            model=VAD_MODEL,
            min_silence_duration=min_silence,
            min_speech_duration=0.25,
            window_size=WINDOW_SIZE,
            max_speech_duration=max_speech,
        ),
        sample_rate=SAMPLE_RATE,
        num_threads=1,
    )
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)


def wav_chunks(samples: np.ndarray, sample_rate: int, realtime: bool):
    pos = 0
    n = len(samples)
    start = time.perf_counter()
    while pos < n:
        chunk = samples[pos:pos + WINDOW_SIZE]
        if len(chunk) < WINDOW_SIZE:
            chunk = np.pad(chunk, (0, WINDOW_SIZE - len(chunk)))
        if realtime:
            # absolute-deadline pacing: naive per-chunk sleeps accumulate
            # ~15% drift on Windows (15.6ms timer granularity)
            delay = start + pos / sample_rate - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
        yield chunk
        pos += WINDOW_SIZE


MIC_QUEUE_TIMEOUT_S = 0.1  # how often the generator wakes up to check stop_event
LOOPBACK_BLOCK_SIZE = WINDOW_SIZE * 4  # SoundCard recommends > record(numframes)
MIX_QUEUE_MAXLEN = 64  # ~2.0s, bounded symmetrically for both hardware clocks
CAPTURE_CHUNK_NS = int(WINDOW_SIZE / SAMPLE_RATE * 1_000_000_000)
MIX_SYNC_TOLERANCE_NS = int(1.5 * CAPTURE_CHUNK_NS)


class _CapturedChunk(NamedTuple):
    """A frame plus the monotonic time at which capture completed."""

    end_ns: int
    samples: np.ndarray


def _next_capture_end_ns(previous_end_ns: "int | None") -> int:
    """Timestamp fixed audio windows without collapsing buffered bursts.

    SoundCard can satisfy a call from its internal pending buffer almost
    immediately after the preceding call.  Wall-clocking both completions
    would assign consecutive 32ms audio windows nearly the same time.  Keep
    at least one window between them while still jumping forward after a real
    capture pause.
    """
    now_ns = time.perf_counter_ns()
    if previous_end_ns is None:
        return now_ns
    return max(now_ns, previous_end_ns + CAPTURE_CHUNK_NS)


def _resolve_mic_device(name: "str | None" = None):
    """Pick a sounddevice input device index for mic_chunks().

    PortAudio's own "default input device" can be unset (-1, none at all)
    -- e.g. a Remote Desktop session with microphone redirection turned
    off has no default across MME/DirectSound/WASAPI; the remote machine's
    own physical mic still enumerates under the WDM-KS host API but can't
    actually be opened cross-session ("Invalid device"). So: honor an
    explicit name/substring first, else PortAudio's default if it has one,
    else just the first input-capable device of any kind -- mic_chunks()
    wraps the actual open call to turn a bad guess here into an actionable
    error rather than a raw PortAudioError.
    """
    import sounddevice as sd

    devices = sd.query_devices()
    if name:
        matches = [i for i, d in enumerate(devices)
                   if name.lower() in d["name"].lower() and d["max_input_channels"] > 0]
        if not matches:
            avail = ", ".join(f"[{i}] {d['name']}" for i, d in enumerate(devices)
                              if d["max_input_channels"] > 0) or "(none)"
            raise RuntimeError(f"no input device matching {name!r} -- available: {avail}")
        return matches[0]

    default_in = sd.default.device[0]
    if isinstance(default_in, int) and default_in >= 0:
        return default_in
    for i, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            return i
    raise RuntimeError("no microphone input device found at all -- see --list-audio-devices")


def mic_chunks(stop_event: "threading.Event | None" = None,
               device_name: "str | None" = None,
               _timestamped: bool = False):
    """Yield mic input frames until `stop_event` is set.

    `q.get()` alone blocks forever with no chunk to hand back control to the
    caller, so an embedding app has no way to break out short of
    KeyboardInterrupt. Polling with a bounded timeout lets the generator
    notice stop_event between callbacks instead.
    """
    import sounddevice as sd

    device = _resolve_mic_device(device_name)
    q: "queue.Queue[np.ndarray | _CapturedChunk]" = queue.Queue()

    last_end_ns = None

    def callback(indata, frames, time_info, status):
        nonlocal last_end_ns
        samples = indata[:, 0].copy()
        # Timestamp in PortAudio's callback, not later in mix's drain
        # thread: if that thread is briefly descheduled, queued old chunks
        # must retain their real acquisition times.
        if _timestamped:
            last_end_ns = _next_capture_end_ns(last_end_ns)
            item = _CapturedChunk(last_end_ns, samples)
        else:
            item = samples
        q.put(item)

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                             blocksize=WINDOW_SIZE, device=device, callback=callback):
            while stop_event is None or not stop_event.is_set():
                try:
                    yield q.get(timeout=MIC_QUEUE_TIMEOUT_S)
                except queue.Empty:
                    continue
    except sd.PortAudioError as exc:
        raise RuntimeError(
            f"couldn't open microphone [{device}] {sd.query_devices(device)['name']!r} "
            f"({exc}). Over Remote Desktop, Windows blocks opening the remote machine's "
            f"own physical mic across the session -- enable microphone redirection instead "
            f"(mstsc: Show Options > Local Resources > Remote audio > Recording > Record "
            f"from this computer). See --list-audio-devices, or pass --mic-device NAME to "
            f"pick a different device.") from exc


def _resolve_loopback_speaker(name: "str | None" = None):
    """Pick a soundcard Speaker to WASAPI-loopback-capture for speaker_chunks().

    WASAPI loopback taps the render endpoint directly -- unlike the classic
    "Stereo Mix" recording device, it needs no opt-in in Windows Sound
    settings and works even when the sound driver doesn't expose Stereo
    Mix at all.
    """
    if sys.platform != "win32":
        raise RuntimeError("--input speaker/mix needs Windows (WASAPI loopback via the "
                            "soundcard package) -- --input mic/wav/ws work on any platform")
    import soundcard as sc

    if name:
        matches = [s for s in sc.all_speakers() if name.lower() in s.name.lower()]
        if not matches:
            avail = ", ".join(repr(s.name) for s in sc.all_speakers()) or "(none)"
            raise RuntimeError(f"no output device matching {name!r} -- available: {avail}")
        return matches[0]
    try:
        return sc.default_speaker()
    except Exception as exc:
        raise RuntimeError(
            f"no default output device to loop back from ({exc}). Over Remote Desktop this "
            f"is usually the redirected 'Remote Audio' channel, not the PC's own speakers -- "
            f"see --list-audio-devices, or pass --speaker-device NAME.") from exc


def _import_soundcard():
    """Import SoundCard on the long-lived caller that owns its global COM."""
    import soundcard as sc
    return sc


@contextmanager
def _windows_com_initialized(ole32=None):
    """Initialize COM for the calling capture thread, then balance it.

    SoundCard 0.4.6 initializes COM only in the thread that first imports
    its Windows backend.  speaker_chunks() deliberately performs WASAPI
    work on another thread, and COM apartments are per-thread, so that
    worker needs its own CoInitializeEx/CoUninitialize pair.

    ``ole32`` is injectable so the HRESULT behavior can be tested on CI
    without a Windows audio endpoint.
    """
    if ole32 is None:
        import ctypes
        ole32 = ctypes.WinDLL("ole32", use_last_error=True)
        ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        ole32.CoInitializeEx.restype = ctypes.c_long
        ole32.CoUninitialize.argtypes = []
        ole32.CoUninitialize.restype = None

    hr = int(ole32.CoInitializeEx(None, 0))  # COINIT_MULTITHREADED
    code = hr & 0xFFFFFFFF
    rpc_e_changed_mode = 0x80010106
    initialized_here = code < 0x80000000
    if not initialized_here and code != rpc_e_changed_mode:
        raise RuntimeError(f"CoInitializeEx failed (HRESULT 0x{code:08X})")
    try:
        yield
    finally:
        # S_OK and S_FALSE both require a matching CoUninitialize.  A
        # changed-mode result means another owner initialized this thread;
        # using that existing apartment is valid, but it is not ours to undo.
        if initialized_here:
            ole32.CoUninitialize()


def _downmix_loopback(block) -> np.ndarray:
    """Validate a SoundCard frame matrix and average every native channel."""
    frames = np.asarray(block, dtype=np.float32)
    if frames.ndim != 2 or frames.shape[1] < 2:
        raise RuntimeError(
            "WASAPI loopback returned invalid single-channel data; "
            f"expected frames x >=2 channels, got shape {frames.shape}")
    if frames.shape[0] != WINDOW_SIZE:
        raise RuntimeError(
            f"WASAPI loopback returned {frames.shape[0]} frames; expected {WINDOW_SIZE}")
    return np.ascontiguousarray(frames.mean(axis=1, dtype=np.float32))


def speaker_chunks(stop_event: "threading.Event | None" = None,
                    device_name: "str | None" = None,
                    _timestamped: bool = False):
    """Yield mono 16kHz frames captured from the PC's audio output (WASAPI
    loopback via the `soundcard` package) -- whatever is playing through
    the speakers/headphones (a call partner's voice, a video), transcribed
    the same way mic audio is. `soundcard` resamples to 16kHz; native output
    channels are captured together and NumPy-downmixed here because
    SoundCard 0.4.6's Windows single-channel recording path is known to
    return corrupted data.

    The actual soundcard.recorder().record() calls run on a dedicated
    background thread, polled through a queue exactly like mic_chunks()
    polls sounddevice's callback queue -- NOT called inline in this
    generator. soundcard.record() is a blocking pull, not a callback: if it
    were called directly from run_stream()'s loop, a slow VAD/ASR decode
    downstream (seen up to ~2s during two-pass refine) would stall the
    *next* record() call long enough for WASAPI's own capture buffer to
    overrun in the meantime, which soundcard surfaces as
    "SoundcardRuntimeWarning: data discontinuity in recording" -- silently
    dropped audio, confirmed by ear against a live TTS test before this was
    threaded off. mic_chunks() never had this problem because
    sounddevice's InputStream callback already runs on PortAudio's own
    thread, independent of whatever the consumer is doing.
    """
    if stop_event is not None and stop_event.is_set():
        return
    if sys.platform != "win32":
        raise RuntimeError("--input speaker/mix needs Windows (WASAPI loopback via the "
                           "soundcard package) -- --input mic/wav/ws work on any platform")

    # Import before initializing the worker's apartment.  SoundCard 0.4.6's
    # module-global COM helper incorrectly treats S_FALSE (already initialized)
    # as an error during its first import; the worker gets a separate, explicit
    # COM scope below before it creates or touches any device wrapper.
    sc = _import_soundcard()

    capture_stop = threading.Event()
    q: "queue.Queue[np.ndarray | _CapturedChunk]" = queue.Queue()
    fatal: "queue.Queue[tuple[str, str]]" = queue.Queue()

    def _capture():
        label = device_name or "default output device"
        last_end_ns = None
        try:
            with _windows_com_initialized():
                # Resolve and use every COM-backed object in this apartment;
                # only plain arrays/strings/exceptions cross the thread edge.
                speaker = _resolve_loopback_speaker(device_name)
                label = speaker.name
                loopback_mic = sc.get_microphone(id=speaker.id, include_loopback=True)
                record_channels = max(2, int(loopback_mic.channels))
                with loopback_mic.recorder(
                        samplerate=SAMPLE_RATE,
                        channels=record_channels,
                        blocksize=LOOPBACK_BLOCK_SIZE) as rec:
                    while not capture_stop.is_set():
                        samples = _downmix_loopback(
                            rec.record(numframes=WINDOW_SIZE))
                        if _timestamped:
                            last_end_ns = _next_capture_end_ns(last_end_ns)
                            item = _CapturedChunk(last_end_ns, samples)
                        else:
                            item = samples
                        q.put(item)
        except Exception as exc:
            # Do not transport the exception/traceback: its frames can retain
            # COM-backed recorder objects and later finalize them on the
            # consumer thread after this apartment has been uninitialized.
            fatal.put((label, str(exc)))

    thread = threading.Thread(target=_capture, name="speaker-loopback-capture",
                              daemon=True)
    thread.start()
    try:
        while stop_event is None or not stop_event.is_set():
            try:
                yield q.get(timeout=MIC_QUEUE_TIMEOUT_S)
            except queue.Empty:
                if not fatal.empty():
                    label, message = fatal.get()
                    raise RuntimeError(
                        f"speaker loopback capture of {label!r} failed: {message}") from None
                continue
    finally:
        capture_stop.set()
        thread.join(timeout=2.0)


def _put_latest(q, item) -> None:
    """Put without blocking, dropping the oldest item when a queue is full."""
    while True:
        try:
            q.put_nowait(item)
            return
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                # The consumer won the race between Full and get_nowait().
                continue


def _drain_available(q, pending) -> None:
    """Move every currently queued packet into the chronological buffer."""
    while True:
        try:
            pending.append(q.get_nowait())
        except queue.Empty:
            return


def _pop_aligned_speaker(pending, target_ns: int,
                         tolerance_ns: int = MIX_SYNC_TOLERANCE_NS):
    """Pop the closest speaker packet in the target window, at most once.

    Stale packets are discarded, packets too far in the future are retained,
    and choosing one packet also discards older unchosen candidates.  The
    deque is chronological because its single producer timestamps at capture.
    """
    lower = target_ns - tolerance_ns
    upper = target_ns + tolerance_ns
    while pending:
        packet = pending[0]
        if packet.end_ns < lower:
            pending.popleft()
            continue
        break
    if not pending or pending[0].end_ns > upper:
        return None

    best_index = 0
    best_distance = abs(pending[0].end_ns - target_ns)
    for index, packet in enumerate(pending):
        if packet.end_ns > upper:
            break
        distance = abs(packet.end_ns - target_ns)
        if distance < best_distance:
            best_index = index
            best_distance = distance
    for _ in range(best_index):
        pending.popleft()
    return pending.popleft()


def _wait_for_aligned_speaker(spk_q, pending, spk_done,
                              target_ns: int):
    """Collect available speaker packets, briefly waiting for the closest."""
    _drain_available(spk_q, pending)
    upper_ns = target_ns + MIX_SYNC_TOLERANCE_NS
    while not spk_done.is_set():
        has_nonpast_candidate = any(
            target_ns <= packet.end_ns <= upper_ns
            for packet in pending)
        if has_nonpast_candidate:
            break
        if any(packet.end_ns > upper_ns for packet in pending):
            # Timestamps are monotonic; once a newer packet
            # arrived, no missing in-window packet can arrive behind it.
            break
        remaining_ns = upper_ns - time.perf_counter_ns()
        if remaining_ns <= 0:
            break
        try:
            pending.append(spk_q.get(timeout=remaining_ns / 1_000_000_000))
        except queue.Empty:
            break
        _drain_available(spk_q, pending)
    # The producer can enqueue its final packet and set spk_done in the
    # narrow interval after the first drain but before the loop condition.
    _drain_available(spk_q, pending)
    return _pop_aligned_speaker(
        pending, target_ns)


def mix_chunks(stop_event: "threading.Event | None" = None,
               mic_device_name: "str | None" = None,
               speaker_device_name: "str | None" = None):
    """Yield mic audio summed with speaker-loopback audio, one mono stream.

    Runs mic_chunks() and speaker_chunks() concurrently on background
    threads (each paced by its own hardware clock, ~32ms/chunk) and adds
    only capture-timestamp-aligned windows so one pipeline transcribes both
    what the user says into the mic and whatever else is playing on the PC
    (a call partner, a video) -- e.g. for meeting transcription. The mic
    clock paces output. Both queues use the same bounded, drop-oldest policy,
    so a slow ASR consumer resumes near live time without pairing different
    moments. A speaker chunk outside a small synchronization window is
    silence. A dead mic is fatal; a dead speaker side falls back to mic-only.
    """
    if stop_event is not None and stop_event.is_set():
        return

    soundcard_preload_error = None
    if sys.platform == "win32":
        # Ensure SoundCard's module-global COM helper belongs to this
        # long-lived caller, not the short-lived speaker drain thread.  The
        # actual device objects still live exclusively in speaker_chunks()'
        # explicitly initialized capture apartment.
        try:
            _import_soundcard()
        except Exception as exc:
            # SoundCard 0.4.6 rejects COM's legitimate S_FALSE result when an
            # embedding host already initialized this apartment.  Do not
            # retry the half-failed import on the short-lived drain thread;
            # that would attach its module-global COM owner to the wrong
            # lifetime.  The speaker side reports this once and mix remains
            # usable as mic-only.
            soundcard_preload_error = RuntimeError(
                f"soundcard initialization on the mix caller failed: {exc}")

    capture_stop = threading.Event()
    mic_q: "queue.Queue[_CapturedChunk]" = queue.Queue(maxsize=MIX_QUEUE_MAXLEN)
    spk_q: "queue.Queue[_CapturedChunk]" = queue.Queue(maxsize=MIX_QUEUE_MAXLEN)
    mic_fatal: "queue.Queue[Exception]" = queue.Queue()
    spk_fatal: "queue.Queue[Exception]" = queue.Queue()
    mic_done = threading.Event()
    spk_done = threading.Event()

    def _drain_mic():
        try:
            kwargs = {"stop_event": capture_stop, "device_name": mic_device_name,
                      "_timestamped": True}
            for packet in mic_chunks(**kwargs):
                _put_latest(mic_q, packet)
        except Exception as exc:
            mic_fatal.put(exc)
            capture_stop.set()
        finally:
            mic_done.set()

    def _drain_speaker():
        try:
            if soundcard_preload_error is not None:
                raise soundcard_preload_error
            kwargs = {"stop_event": capture_stop, "device_name": speaker_device_name,
                      "_timestamped": True}
            for packet in speaker_chunks(**kwargs):
                _put_latest(spk_q, packet)
        except Exception as exc:
            spk_fatal.put(exc)
        finally:
            spk_done.set()

    mic_thread = threading.Thread(target=_drain_mic, name="mix-mic-capture", daemon=True)
    spk_thread = threading.Thread(target=_drain_speaker, name="mix-speaker-capture",
                                  daemon=True)
    mic_thread.start()
    spk_thread.start()
    pending_speaker = deque(maxlen=MIX_QUEUE_MAXLEN)
    speaker_error_reported = False

    def _raise_mic_failure() -> None:
        try:
            exc = mic_fatal.get_nowait()
        except queue.Empty:
            return
        raise exc

    def _report_speaker_failure() -> None:
        nonlocal speaker_error_reported
        if speaker_error_reported:
            return
        try:
            exc = spk_fatal.get_nowait()
        except queue.Empty:
            return
        speaker_error_reported = True
        print(f"[mix] speaker loopback capture stopped: {exc}", file=sys.stderr)

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                return
            _raise_mic_failure()
            _report_speaker_failure()
            try:
                mic_packet = mic_q.get(timeout=MIC_QUEUE_TIMEOUT_S)
            except queue.Empty:
                _raise_mic_failure()
                _report_speaker_failure()
                if mic_done.is_set():
                    return
                continue

            # At live speed an older in-window speaker frame may already be
            # queued while the exact matching one completes a few milliseconds
            # later.  Do not consume the older candidate immediately: wait for
            # a same-time/future candidate until this target's finite deadline,
            # then choose the closest packet available.  For queued/stale mic
            # audio the deadline is already past, so this never compounds ASR
            # latency.
            target_ns = mic_packet.end_ns
            spk_packet = _wait_for_aligned_speaker(
                spk_q, pending_speaker, spk_done, target_ns)

            _report_speaker_failure()
            mic_chunk = mic_packet.samples
            if (spk_packet is not None and
                    len(spk_packet.samples) == len(mic_chunk)):
                mixed = np.clip(mic_chunk + spk_packet.samples, -1.0, 1.0)
            else:
                mixed = mic_chunk
            yield mixed
    finally:
        capture_stop.set()
        mic_thread.join(timeout=2.0)
        spk_thread.join(timeout=2.0)


def ws_chunks(ingest):
    """Re-chunk a ws_ingest.IngestServer's variable-sized network reads into
    fixed WINDOW_SIZE frames, the shape the VAD expects.

    Blocks on ingest.audio_q, same as mic_chunks() blocks on sounddevice's
    queue -- if the client disconnects, this just idles until the next one
    connects and starts pushing audio again; the pipeline stays alive.

    ingest.audio_q can also carry a non-ndarray "flush" sentinel (see
    ws_ingest.FLUSH), pushed when a streaming client disconnects. It is
    passed straight through to run_stream(), which treats any non-ndarray
    item as "flush the in-progress VAD segment now" -- otherwise a segment
    left open when the client vanishes would sit unfinalized forever.
    """
    leftover = np.zeros(0, dtype=np.float32)
    while True:
        item = ingest.audio_q.get()
        if not isinstance(item, np.ndarray):
            if len(leftover):
                yield np.pad(leftover, (0, WINDOW_SIZE - len(leftover)))
                leftover = np.zeros(0, dtype=np.float32)
            yield item
            continue
        leftover = np.concatenate([leftover, item])
        while len(leftover) >= WINDOW_SIZE:
            yield leftover[:WINDOW_SIZE]
            leftover = leftover[WINDOW_SIZE:]


PARTIAL_EVERY_S = 0.5   # decode a draft this often (in audio time) during speech
# Partial (in-progress) drafts decode only the last PARTIAL_WINDOW_S seconds
# of the utterance. It must be >= the VAD's max_speech_duration
# (default --max-speech 12.0 s) or the leading part of a long sentence
# silently vanishes from the draft while the speaker is still talking
# ("前面的内容消失", user report: --max-speech 14 s still produced a
# scrolling/truncated draft because the VAD tail-silence extends the
# segment past the window). 60 s covers a 60 s max-speech segment in full;
# decode cost is unchanged -- the window only caps what is cut from the
# SEGMENT's tail, it never extends the audio below the VAD's own length.
PARTIAL_WINDOW_S = 60.0


class PartialPrinter:
    """Shows in-progress drafts; overwrites in place on a tty, one line otherwise."""

    def __init__(self, enabled: bool, server=None):
        self.enabled = enabled
        self.server = server
        self._tty = sys.stdout.isatty()
        self._last_len = 0

    def show(self, text: str):
        if not self.enabled or not text:
            return
        if self.server is not None:
            self.server.partial(text)
        if self._tty:
            pad = max(self._last_len - len(text), 0)
            print("\r~ " + text + " " * pad, end="", flush=True)
            self._last_len = len(text)
        else:
            print(f"~ {text}", flush=True)

    def clear(self):
        if self.enabled and self._tty and self._last_len:
            print("\r" + " " * (self._last_len + 2) + "\r", end="", flush=True)
            self._last_len = 0


class SessionStats:
    def __init__(self):
        self.total_audio_s = 0.0
        self.segments = 0
        self.latencies_ms: list[float] = []
        self.refine_lang_corrections = 0  # times the refine pass overruled the fast-path language

    def summary(self) -> str:
        if not self.latencies_ms:
            return f"total_audio={self.total_audio_s:.1f}s segments=0"
        mean = sum(self.latencies_ms) / len(self.latencies_ms)
        return (f"total_audio={self.total_audio_s:.1f}s segments={self.segments} "
                f"mean_latency={mean:.0f}ms max_latency={max(self.latencies_ms):.0f}ms "
                f"refine_lang_corrections={self.refine_lang_corrections}")


PREROLL_S = 1.0  # audio to prepend before the VAD's detected speech onset


class AudioHistory:
    """Rolling buffer of recent audio so finals can include pre-onset context."""

    def __init__(self, sample_rate: int, keep_s: float = 30.0):
        self.sr = sample_rate
        self.keep = int(keep_s * sample_rate)
        self.buf = np.zeros(0, dtype=np.float32)
        self.offset = 0  # absolute sample index of buf[0]
        self.last_seg_end = 0  # don't let preroll bleed into the previous utterance

    def push(self, chunk: np.ndarray):
        self.buf = np.concatenate([self.buf, chunk])
        if len(self.buf) > self.keep:
            drop = len(self.buf) - self.keep
            self.buf = self.buf[drop:]
            self.offset += drop

    def with_preroll(self, seg_start: int, seg_samples: np.ndarray) -> np.ndarray:
        want = max(seg_start - int(PREROLL_S * self.sr), self.last_seg_end, self.offset)
        pre = self.buf[want - self.offset:seg_start - self.offset]
        self.last_seg_end = seg_start + len(seg_samples)
        if len(pre) == 0:
            return seg_samples
        return np.concatenate([pre, seg_samples])


def drain_segments(vad, sample_rate: int, asr: RoutedASR, stats: SessionStats,
                   printer: PartialPrinter, history: AudioHistory | None = None,
                   known_lang: str | None = None, refiner: "Refiner | None" = None,
                   translator_worker: "TranslationWorker | None" = None,
                   speaker_labeler=None) -> int:
    drained = 0
    while not vad.empty():
        segment = vad.front
        seg_end_time = time.perf_counter()  # segment-end reference point for latency
        samples = np.asarray(segment.samples, dtype=np.float32)
        seg_start, seg_end = segment.start, segment.start + len(samples)
        if history is not None:
            samples = history.with_preroll(seg_start, samples)
        vad.pop()
        drained += 1

        seg_s = len(samples) / sample_rate
        raw_speech_s = (seg_end - seg_start) / sample_rate  # without preroll
        # the early LID belongs to the utterance in progress; only the first
        # drained segment can safely claim it
        result = asr.transcribe(samples, sample_rate,
                                known_lang=known_lang if drained == 1 else None,
                                speech_s=raw_speech_s)
        latency_ms = (time.perf_counter() - seg_end_time) * 1000

        if not result["text"].strip():
            continue  # non-speech (jingle/SFX): no line, no speaker, no span

        speaker = ""
        if speaker_labeler is not None:
            speaker = speaker_labeler.label(samples, sample_rate) + "|"

        stats.segments += 1
        stats.latencies_ms.append(latency_ms)
        printer.clear()
        seq = next(_UTT_SEQ)  # utterance id for translation attachment
        if printer.server is not None:
            printer.server.final(result["text"], result["lang"], speaker.rstrip("|"),
                                 latency_ms, result.get("tier", ""), seq=seq)
        probe_part = f", probe={result['probe_ms']:.0f}ms" if result.get("probe_ms") else ""
        print(f"[{speaker}{result['lang']}/{result.get('tier', '?')}] {result['text']}  "
              f"(seg={seg_s:.1f}s, lid={result['lid_ms']:.0f}ms{probe_part}, "
              f"decode={result['decode_ms']:.0f}ms, latency={latency_ms:.0f}ms)", flush=True)
        if translator_worker is not None and result["text"].strip():
            # Every finalized line goes to the worker regardless of its detected
            # language. The worker hands it to API translators for ANY source
            # language, and to the local ja-fixed MT translators only when the
            # source is ja (see TranslationWorker._run).
            translator_worker.submit(result["text"], result["lang"], seq)
        if refiner is not None:
            refiner.add_span(seg_start, seg_end, result["lang"], result["text"],
                             speaker.rstrip("|"))
    return drained


import re as _re


# Set in main() from --api-config (or auto-detected openai_translate.json in
# the project root). Non-None means the API translation route is available.
_api_cfg = None


def digits_consistent(src: str, out: str) -> bool:
    """Every digit run in the source must survive into the translation.

    Guards against the MT models' number errors (500万円 -> "5万英镑"): a
    wrong number in a subtitle is worse than no translation. Kanji numerals
    carry no ASCII digits, so those lines pass through unguarded.
    """
    src_runs = _re.findall(r"\d+", src)
    if not src_runs:
        return True
    out_runs = set(_re.findall(r"\d+", out))
    return all(run in out_runs for run in src_runs)


def safe_translate(translator, text: str, src_lang: str = "ja",
                   record: bool = True) -> str:
    """Translate one line; fall back to the source when numbers got mangled.

    src_lang is the detected language of `text` -- the API translator needs it
    to build its prompt; the local MT translators ignore it (they are ja-fixed).

    record=False marks an in-progress partial draft: the API translator keeps
    it out of the rolling conversation context (drafts are not finished text).
    """
    if getattr(translator, "IS_API", False):
        out = translator.translate(text, src_lang, record=record)
    else:
        out = translator.translate(text)
    if out != text and not digits_consistent(text, out):
        return text
    return out


def translate_by_sentence(translator, text: str, src_lang: str = "ja") -> str:
    """The MT models are trained on single sentences; feed one at a time."""
    sentences = [s for s in _re.split(r"(?<=[。！？!?])\s*", text) if s.strip()]
    out = []
    for s in sentences:
        en = safe_translate(translator, s, src_lang)
        if en != s:
            out.append(en)
    return " ".join(out)


def build_translators(langs: str) -> dict:
    """"en,zh,ko,es,..." -> {lang: translator}.

    Syntax supports two kinds of targets, mixed freely:
      - `api:zh`, `api:en,zh,ko` or bare `api`  -> OpenAI-compatible API
        translation of ANY detected source language -> target(s). Only works
        when openai_translate.json is configured (see translate_api.py).
      - plain codes (`en`, `zh`, `ko`, ...)      -> the local models as
        before: en uses FuguMT (ja->en); any other target is accepted if
        M2M-100's vocabulary has a token for it (see
        translate_m2m.is_supported_target()). Only a subset have measured
        quality (translate_m2m.VALIDATED_TARGETS) -- constructing one outside
        that set prints a note to stderr but still works.
    """
    out = {}
    for lang in [x.strip() for x in langs.split(",") if x.strip()]:
        if lang == "api" or lang.startswith("api:"):
            if _api_cfg is None:
                print("api: target requested but openai_translate.json is not "
                      "configured (missing base_url/model)", file=sys.stderr)
                continue
            from translate_api import TranslatorApi, api_targets

            available = sorted(api_targets(_api_cfg))
            if not available:
                print("api: openai_translate.json has no usable targets",
                      file=sys.stderr)
                continue
            wanted = lang[4:].strip() if lang.startswith("api:") else ""
            if wanted:
                if wanted not in available:
                    print(f"api: unsupported target {wanted!r} "
                          f"(config targets: {available})", file=sys.stderr)
                    continue
                targets = [wanted]
            else:
                targets = available
            for t in targets:
                out[t] = TranslatorApi(_api_cfg, t)
            continue
        if lang == "en":
            from translate_ja_en import TranslatorJaEn

            out["en"] = TranslatorJaEn()
        else:
            from translate_m2m import TranslatorM2M, is_supported_target

            if not is_supported_target(lang):
                print(f"unsupported translation target: {lang}", file=sys.stderr)
                continue
            out[lang] = TranslatorM2M(lang)
    return out


def apply_translation_spec(spec: str, worker: "TranslationWorker",
                           get_refiner=None) -> None:
    """"translate en,zh,ko" | "" / "off" -> hot-switch the worker's active
    translators. Shared by the stdin command thread and by the HTTP
    POST /api/translate endpoint (called from the desktop subtitle window),
    so both paths can switch translation at runtime without a restart.

    Also rebinds Refiner.translators (via get_refiner, resolved at call
    time because the Refiner is constructed after the stdin thread and the
    server callback are wired up, and may not exist at all with
    --no-refine): the Refiner holds its own dict reference captured at
    construction, so without this the refine pass keeps translating exactly
    as configured when it STARTED -- turn translation on via the subtitle
    window after booting with none, and refined (clean-book) paragraphs
    would gain no translation line; turn it off and they would keep getting
    one. Attribute assignment is one pointer store (atomic under the GIL)
    and refine re-reads self.translators per call, so the swap takes
    effect from the next group onward.
    """
    spec = (spec or "").strip().lower()

    def _publish(trs: dict) -> None:
        worker.set_langs(trs)
        refiner = get_refiner() if get_refiner is not None else None
        if refiner is not None:
            refiner.translators = trs

    if spec in ("", "off", "none", "0"):
        _publish({})
        print("[cmd] translation off", file=sys.stderr, flush=True)
        return
    print(f"[cmd] loading translators ({spec})...", file=sys.stderr, flush=True)
    try:
        tr = build_translators(spec)
    except Exception as exc:
        print(f"[cmd] failed: {exc}", file=sys.stderr, flush=True)
        return
    if tr:
        _publish(tr)
    else:
        print("[cmd] no supported targets", file=sys.stderr, flush=True)


# Queue sentinel for the "daemon thread(s) draining a Queue forever" workers
# below (TranslationWorker, Refiner). They used to loop with no way out,
# which meant the thread's frame held its owner -- and through the Refiner,
# the RoutedASR and its multi-GB sherpa-onnx recognizers -- alive for the
# life of the process. Nothing could be garbage collected. Enqueuing this
# object tells the loop to return; because the queues are FIFO (the partial
# draft queue is polled, newest-wins), everything submitted before the
# sentinel still runs first.
_WORKER_STOP = object()


class TranslationWorker:
    """Async source->target translation of finalized lines (console display).

    Supports hot enabling/disabling at runtime: set_langs() swaps the active
    translator set from a live stdin command (e.g. "translate en,zh" or
    "translate off") without restarting the server.

    Source-language handling:
    - The API translator (translate_api.TranslatorApi, IS_API=True) accepts
      ANY detected source language; it is prompted with the src_lang name.
    - The local MT translators (FuguMT / M2M-100) are ja-fixed, so for a
      source other than ja their target is silently skipped.

    In-progress (partial) drafts can also be translated while the speaker
    is still talking -- submit_partial() throttles them (text must have
    grown meaningfully and >= PARTIAL_MIN_GAP_S since the last submission)
    and drops them into a separate capacity-1 queue (newest wins). Two
    worker threads run instead of one:
      - _final_loop blocks on the finalized-line queue and translates
        confirmed lines immediately; a draft's slow API call can never
        delay or sit ahead of it.
      - _partial_loop translates whatever draft is pending whenever the
        draft queue is non-empty, so an utterance's translation starts
        DURING speech instead of waiting for the VAD confirm.
    Draft results publish as type=partial_translation (no seq -- a draft
    has no finalized id yet); the front-end renders them as a dim preview
    under the subtitle and replaces them when the real translation of the
    finalized line arrives.
    """

    PARTIAL_MIN_GAP_S = 1.0  # don't re-submit a draft more often than this
    PARTIAL_MIN_DELTA = 5    # unless the draft gained this many new characters

    def __init__(self, translators: dict, server=None):
        self._lock = threading.Lock()
        self._translators = dict(translators)  # copy: never mutate in place
        self._server = server
        # finalized lines (priority) -- one FIFO
        self._q: "queue.Queue" = queue.Queue()
        # partial drafts (best-effort) -- capacity 1, newest wins
        self._partial_q: "queue.Queue" = queue.Queue(maxsize=1)
        self._last_partial_at = 0.0
        self._last_partial_text = ""
        # Draft staleness guard: every finalized line bumps this; a partial
        # draft records the epoch it was submitted under and is DROPPED (not
        # published) if a final arrived while its translation was in flight.
        # Without this, a late API draft result would overwrite the just-
        # finalized translation ("final translation on screen, partial still
        # lingered") -- the front-end can't tell a stale draft from a new one.
        self._final_epoch = 0
        self._closed = False
        self._final_thread = threading.Thread(target=self._final_loop, daemon=True)
        self._partial_thread = threading.Thread(target=self._partial_loop, daemon=True)
        self._final_thread.start()
        self._partial_thread.start()

    def set_langs(self, translators: dict):
        with self._lock:
            self._translators = dict(translators)  # atomic swap, no stale iteration
        print(f"[translate] active langs: {list(n for n in self._translators) or ['off']}",
              flush=True)

    def submit(self, text: str, src_lang: str = "ja", seq: int | None = None):
        self.submit_final_locked(text, src_lang, seq)

    def close(self) -> None:
        """Stop both worker threads once everything already queued has been
        translated. Idempotent.

        Same reason Refiner.close() exists: these threads' frames hold
        `self`, and through it the translator models, so an unterminated
        worker keeps them alive for the life of the process.
        """
        if self._closed:
            return
        self._closed = True
        self._q.put(_WORKER_STOP)
        self._partial_q.put(_WORKER_STOP)  # polled within ~50ms; never blocks long
        self._final_thread.join()
        self._partial_thread.join()

    def __enter__(self) -> "TranslationWorker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def submit_final_locked(self, text: str, src_lang: str = "ja",
                            seq: int | None = None):
        """Queue a confirmed line AND invalidate in-flight drafts.

        The utterance is now final: anything a draft of it is still saying
        is stale, whether it is sitting in _partial_q or already being
        translated by the partial thread. Bumping _final_epoch marks all
        drafts submitted before this point as outdated (the partial thread
        checks the epoch before publishing), and draining _partial_q frees
        any draft that has not even started yet -- so an API round-trip is
        never wasted on text a confirmed line has already replaced.

        A no-op after close(): the workers are gone, so anything queued now
        would sit unread forever. Unlike Refiner.maybe_refine() (which
        raises, because losing refine text loses transcript content), a
        dropped console translation of a line that was already printed in
        its original language costs the user nothing at shutdown.
        """
        if self._closed:
            return
        with self._lock:
            self._final_epoch += 1
        # drop drafts that haven't started translating yet -- they belong to
        # the now-finalized utterance
        while True:
            try:
                self._partial_q.get_nowait()
            except queue.Empty:
                break
        self._q.put((text, src_lang or "ja", seq))

    def submit_partial(self, text: str, src_lang: str = "ja") -> bool:
        """Offer an in-progress draft for translation (throttled).

        Only forwarded when the draft has genuinely moved on since the last
        submission: it gained >= PARTIAL_MIN_DELTA new characters, or it got
        shorter (re-recognition), and at least PARTIAL_MIN_GAP_S elapsed. The
        capacity-1 queue keeps exactly the latest pending draft, so the
        worker thread picks up the most recent state of the utterance, never
        an outdated prefix. Returns True when accepted.
        """
        now = time.monotonic()
        text = (text or "").strip()
        prev = self._last_partial_text
        # A brand-new draft (nothing submitted for this utterance yet) is
        # always accepted; afterwards the draft must have moved on: gained
        # >= PARTIAL_MIN_DELTA new characters, or got shorter (re-recognition).
        if prev == "":
            changed = True
        else:
            grown = len(text) - len(prev) >= self.PARTIAL_MIN_DELTA
            changed = text != prev and (grown or len(text) < len(prev))
        if not changed or (now - self._last_partial_at) < self.PARTIAL_MIN_GAP_S:
            return False
        # newest-wins: drop whatever draft is still queued
        while True:
            try:
                self._partial_q.get_nowait()
            except queue.Empty:
                break
        # Snapshot the final-epoch at submission time: if a final is queued
        # before this draft's translation finishes, the draft is stale and its
        # result must not be published (a late partial_translation would
        # overwrite the just-shown confirmed translation).
        if self._closed:
            return False  # see submit_final_locked: drafts are best-effort
        with self._lock:
            epoch = self._final_epoch
        self._partial_q.put((text, src_lang or "ja", epoch))
        self._last_partial_at = now
        self._last_partial_text = text
        return True

    def _final_loop(self):
        """Confirmed lines are translated by their own worker thread that
        BLOCKS on the finalized queue: a draft's slow API round-trip can
        never delay or sit ahead of a confirmed line's translation."""
        while True:
            item = self._q.get()
            if item is _WORKER_STOP:
                return
            text, src_lang, seq = item
            self._translate(text, src_lang, seq, partial=False)

    def _partial_loop(self):
        """Draft translations run in a SEPARATE thread that never blocks on
        finals: as soon as the ingestion loop offers a draft (mid-speech),
        it is translated immediately -- the translation starts WHILE the
        speaker is still talking, not after the VAD confirms the line."""
        while True:
            try:
                item = self._partial_q.get_nowait()
            except queue.Empty:
                time.sleep(0.05)
                continue
            if item is _WORKER_STOP:
                return
            ptext, plang, epoch = item
            self._translate(ptext, plang, None, partial=True,
                            draft_epoch=epoch)

    def _translate(self, text: str, src_lang: str, seq: int | None,
                   partial: bool, draft_epoch: int | None = None):
        with self._lock:
            langs = list(self._translators.items())
        if not langs:
            return
        ev_type = "partial_translation" if partial else "translation"
        for lang, tr in langs:
            # local ja-fixed MT: only ja source makes sense
            if src_lang != "ja" and not getattr(tr, "IS_API", False):
                continue
            # Translating a line into its own language is a no-op (zh ->
            # zh) and a wasted API round-trip, but the viewer still wants
            # to SEE the translation line populated -- so for a
            # same-language target we publish the SOURCE text itself as
            # the "translation" (no API call). The front-end thus always
            # has something under the subtitle for the active language.
            if lang == (src_lang or "ja"):
                self._maybe_publish(ev_type, lang, text, seq, partial,
                                    draft_epoch)
                continue
            out = safe_translate(tr, text, src_lang, record=not partial)
            if out != text:  # fallback returns the source: nothing worth showing
                if not partial:
                    print(f"[→{lang}] {out}", flush=True)
                self._maybe_publish(ev_type, lang, out, seq, partial,
                                    draft_epoch)

    def _maybe_publish(self, ev_type: str, lang: str, text: str,
                       seq: int | None, partial: bool,
                       draft_epoch: int | None):
        """Publish a translation event, unless it is a STALE draft.

        The epoch check happens HERE -- at the actual publish moment -- not at
        the start of _translate. An API round-trip takes 1-5s: a final may be
        queued (bumping _final_epoch) while the blocking safe_translate() is
        still running, and the check must see that bump AFTER the API returns,
        otherwise a late partial_translation overwrites the just-shown
        confirmed translation ("final 翻译都出来了, partial 还在")."""
        if partial:
            with self._lock:
                if draft_epoch != self._final_epoch:
                    return  # a final arrived while this draft was in flight
        if self._server is not None:
            self._server.publish({"type": ev_type, "lang": lang,
                                  "text": text, "seq": seq})


from asr_engine import (  # shared with the engine's live correction / refine dual-LID confirm
    ModelUnavailable, REFINE_MIN_REGROUP_S, SV_LANGS, resolve_refine_lang, script_corrected_lang,
    sv_lid_tag,
)

GROUP_GAP_S = 2.0   # this much true silence closes an utterance group
GROUP_MAX_S = 25.0  # refine early rather than outgrow the audio history


class Refiner:
    """Second pass: re-decode a whole utterance group once the speaker pauses.

    Fast finals stay untouched; the refined text (measured ~23% relative CER
    better on real broadcast ja) goes to the console, the SSE stream, and the
    transcript file when one is requested.
    """

    def __init__(self, asr: RoutedASR, history: AudioHistory, sample_rate: int,
                 printer: PartialPrinter, transcript_path: str | None = None,
                 translators: dict | None = None, stats: "SessionStats | None" = None):
        self.asr = asr
        self.history = history
        self.sr = sample_rate
        self.printer = printer
        self.translators = translators or {}  # ja->target, synchronous per refine
        self.stats = stats
        self.spans: list[tuple[int, int, str, str, str]] = []
        self._transcript = open(transcript_path, "a", encoding="utf-8") if transcript_path else None
        # A single FIFO worker (not "spawn a thread per refine") is what makes
        # output order safe: maybe_refine() is only ever called from the
        # single-threaded ingestion path (run_stream / add_span's language-
        # boundary flush), so the order tasks are *queued* in is always the
        # chronological order of the audio groups. Threading.Thread-per-call
        # loses that guarantee -- start() order is not lock-acquisition
        # order, so two nearly-simultaneous refines (a language-boundary
        # flush racing the next group's silence-triggered flush) could grab
        # the old _worker_lock in either order and print [refine/...] lines
        # out of chronological sequence. A single consumer thread draining a
        # Queue processes strictly in enqueue order, so this can't happen.
        self._task_queue: "queue.Queue" = queue.Queue()
        self._closed = False
        self._close_lock = threading.Lock()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def close(self) -> None:
        """Finish the queued refine work, stop the worker, close the
        transcript file. Idempotent; safe to call from any thread.

        This is the counterpart to the `_task_queue.join()` main()'s
        finish() already relies on, and it drains exactly the same way:
        the stop sentinel goes in at the BACK of the FIFO, so every group
        queued before it is still decoded, printed, published and written
        to the transcript before the worker returns. close() then waits
        for the thread to actually end.

        Why it has to exist at all: _worker_loop's frame holds `self`,
        `self` holds the RoutedASR, and the RoutedASR holds several GB of
        sherpa-onnx recognizers. A Refiner whose worker never returns is
        therefore uncollectable no matter what its owner drops -- which is
        how building one pipeline per clip in a test loop grew pytest's RSS
        into the tens of GB upstream. Pair this with RoutedASR.close();
        main() does, in that order.
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        self._task_queue.put(_WORKER_STOP)
        self._worker_thread.join()
        if self._transcript is not None:
            self._transcript.close()
            self._transcript = None

    def __enter__(self) -> "Refiner":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _worker_loop(self):
        while True:
            task = self._task_queue.get()
            if task is _WORKER_STOP:
                self._task_queue.task_done()
                return
            try:
                task()
            finally:
                self._task_queue.task_done()

    def add_span(self, seg_start: int, seg_end: int, lang: str, text: str, speaker: str):
        """Append one finalized segment to the pending refine group.

        Splits the group first if this segment's (script-corrected)
        language differs from the group's current language. Without this,
        a group kept accumulating across a language change until the next
        silence gap or GROUP_MAX_S -- the "mixed" guard in maybe_refine
        already protected the DECODE from being corrupted (it skips
        re-decoding and falls back to the joined fast-path text), but the
        group still printed as a single refine line under one language tag,
        visually swallowing an en segment sandwiched between two ja ones
        into a "[refine/ja] ..." line. Refine groups now never cross a
        language boundary.

        Raises RuntimeError after close() -- see maybe_refine().
        """
        self._check_open()
        corrected = script_corrected_lang(lang, text)
        if self.spans:
            group_lang = script_corrected_lang(self.spans[-1][2], self.spans[-1][3])
            if corrected != group_lang:
                # flush the previous group off the hot path; it belongs to
                # a different language and must not accumulate this span
                self.maybe_refine(seg_start, force=True, force_sync=False)
        self.spans.append((seg_start, seg_end, lang, text, speaker))

    def _check_open(self) -> None:
        """Refuse work on a closed Refiner, loudly.

        A no-op would be the friendlier-looking choice, but every caller
        here (run_stream/drain_segments' ingestion path, main()'s finish())
        is submitting audio that is supposed to end up in the
        console/SSE/transcript output -- silently dropping it would lose
        transcript content with nothing to show for it. Worse, a
        `force_sync=True` refine enqueued after close() would block its
        caller forever on an Event no worker is left to set, so this turns
        a deadlock into an immediate, traceable error.
        """
        if self._closed:
            raise RuntimeError(
                "this Refiner is closed (close() stopped its worker thread); "
                "build a new Refiner for a new session")

    def maybe_refine(self, now_sample: int, force: bool = False, force_sync: bool | None = None):
        self._check_open()
        if not self.spans:
            return
        first_start = self.spans[0][0]
        last_end = self.spans[-1][1]
        due = (force
               or now_sample - last_end >= int(GROUP_GAP_S * self.sr)
               or last_end - first_start >= int(GROUP_MAX_S * self.sr))
        if not due:
            return
        # force=True normally means "run synchronously" (the shutdown/flush
        # path wants the transcript finished before the process exits);
        # force_sync=False overrides that for a forced-but-not-urgent flush
        # (a language-boundary split mid-stream) so it still goes through
        # the background thread and doesn't stall the hot path.
        run_sync = force if force_sync is None else force_sync
        lo = max(first_start - int(PREROLL_S * self.sr), self.history.offset)
        buf = self.history.buf[lo - self.history.offset:last_end - self.history.offset].copy()
        # LID tags lie under BGM; trust the script of the decoded text over
        # the tag (an "en" span full of kanji was misdetected Japanese, an
        # ALL-CAPS "ja" span was misdetected English).
        langs = [script_corrected_lang(lang, text)
                 for _, _, lang, text, _ in self.spans]
        lang = max(set(langs), key=langs.count)
        # a genuinely mixed-language group must not be re-decoded in one
        # language: the per-segment finals already used the right model per
        # language, and a majority-language re-decode mangles the minority
        # (docs/BENCHMARKS.md iteration 25). Keep the merge, skip the decode.
        mixed = len(set(langs)) > 1 and min(langs.count(l) for l in set(langs)) / len(langs) >= 0.25
        speakers = [sp for _, _, _, _, sp in self.spans if sp]
        speaker = max(set(speakers), key=speakers.count) if speakers else ""
        fast_joined = " ".join(t for _, _, _, t, _ in self.spans if t.strip())
        self.spans = []
        if len(buf) < self.sr // 2:
            return

        def work():
            # off the hot path: a refine of a 25s group takes ~0.5-1s and must
            # not delay the next utterance's instant final (soak test showed
            # 2.6s latency spikes when run inline). Runs on the single
            # _worker_thread, which serializes it against every other queued
            # refine in enqueue (== chronological) order -- see the comment
            # on _task_queue in __init__.
            refine_lang = lang
            if mixed:
                text = fast_joined
            else:
                # re-run LID on the merged (longer, higher-confidence)
                # audio: the fast path's per-segment majority vote used
                # only 2-4s clips, which docs/LID.md measured well below
                # whisper-tiny's high-confidence length for several
                # languages. But "longer" only holds when the group
                # really is a multi-segment utterance -- a group can be
                # a single short segment sitting alone between silence
                # gaps, and a lone whisper-tiny re-judgment on that is
                # no more reliable than the live path's single LID call
                # (real-mic incident: this flipped a correctly
                # dual-confirmed live "ko" back to a collapsed "ru").
                # So the re-judgment goes through the SAME dual-LID
                # confirmation as the live path: SenseVoice must agree,
                # and resolve_refine_lang additionally gates on the
                # group's total duration (see REFINE_MIN_REGROUP_S).
                group_duration_s = len(buf) / self.sr
                detected = self.asr._identify_lang(buf, self.sr)
                sv_lang = ""
                probe_text = None
                if detected != lang and group_duration_s >= REFINE_MIN_REGROUP_S:
                    try:
                        sv_rec = self.asr._get("sv")
                        probe_text, sv_tag = self.asr._decode_full(sv_rec, buf, self.sr)
                        sv_lang = sv_lid_tag(sv_tag)
                    except ModelUnavailable:
                        sv_lang = ""  # minimal install: no probe possible, no override
                resolved, changed = resolve_refine_lang(lang, detected, sv_lang, group_duration_s)
                if changed:
                    if resolved in SV_LANGS and probe_text is not None:
                        # resolve_refine_lang only sets changed=True when
                        # sv_lang == whisper_lang == resolved, so the SenseVoice
                        # probe above already decoded this exact buffer through
                        # the same route transcribe() would take for `resolved`
                        # (ko/yue both route to SenseVoice) -- reuse its text
                        # instead of a second SV pass, same reuse pattern as
                        # the live path's dual-LID confirmation probe. Apply
                        # the same post-processing transcribe() would (text
                        # replacements, and Kiwi ko word-spacing) so the
                        # result matches what a fresh transcribe() call would
                        # have produced.
                        text = self.asr._replace(probe_text)
                        if resolved == "ko" and text.strip() and self.asr.ko_spacer is not None:
                            try:
                                text = self.asr.ko_spacer.space(text, reset_whitespace=True)
                            except Exception:
                                pass
                    else:
                        text = self.asr.transcribe(buf, self.sr, known_lang=resolved,
                                                    live=False)["text"]
                    if text.strip():
                        refine_lang = resolved
                    else:
                        text = self.asr.transcribe(buf, self.sr, known_lang=lang,
                                                    live=False)["text"]
                else:
                    text = self.asr.transcribe(buf, self.sr, known_lang=lang,
                                                live=False)["text"]
            # a merged re-decode must never LOSE content; if it comes back
            # much shorter than the fast finals combined, trust those
            if len(text.strip()) < 0.7 * len(fast_joined):
                text = fast_joined
                refine_lang = lang
            if not text.strip():
                return
            if refine_lang != lang and self.stats is not None:
                self.stats.refine_lang_corrections += 1
                print(f"[refine] language corrected {lang}->{refine_lang}", flush=True)
            tag = f"{speaker}|{refine_lang}" if speaker else refine_lang
            print(f"[refine/{tag}] {text}", flush=True)
            if self.printer.server is not None:
                self.printer.server.publish({"type": "refine", "text": text, "lang": refine_lang,
                                             "speaker": speaker})
            outs = []
            if self.translators:
                # synchronous here (we're already off the hot path) so the
                # transcript keeps source and translations adjacent, in
                # order. The MT models degrade on multi-sentence input,
                # so translate sentence by sentence.
                # API translators accept any refined source language; the
                # local ja-fixed MT translators only run for ja (unchanged
                # behavior).
                for tlang, tr in self.translators.items():
                    if refine_lang != "ja" and not getattr(tr, "IS_API", False):
                        continue
                    # target == refined source: no transform is meaningful, but the
                    # transcript should still pair the line with its own
                    # language -- record the source text as the output.
                    if tlang == (refine_lang or "ja"):
                        if text.strip():
                            print(f"[refine→{tlang}] {text}", flush=True)
                            outs.append((tlang, text))
                        continue
                    out = translate_by_sentence(tr, text, refine_lang)
                    if out and out != text:
                        print(f"[refine→{tlang}] {out}", flush=True)
                        outs.append((tlang, out))
            if self._transcript is not None:
                prefix = f"{speaker}: " if speaker else ""
                self._transcript.write(prefix + text + "\n")
                for tlang, out in outs:
                    self._transcript.write(f"  →{tlang} {out}\n")
                self._transcript.flush()

        # Both branches enqueue onto the same FIFO worker so cross-call
        # ordering is preserved even when a sync flush (shutdown, or a
        # language-boundary split queued via maybe_refine(..., force_sync=False)
        # just above it) lands close to an async one. force_sync=True still
        # blocks the caller until this task has actually run (the shutdown
        # path needs the transcript finished before the process exits) --
        # it just does so by waiting on the task rather than by running the
        # work inline, so it can't jump the queue ahead of an
        # already-queued-but-not-yet-run older group.
        if run_sync:
            done = threading.Event()

            def sync_work(work=work, done=done):
                try:
                    work()
                finally:
                    done.set()

            self._task_queue.put(sync_work)
            done.wait()
        else:
            self._task_queue.put(work)


def run_stream(chunks, vad, sample_rate: int, asr: RoutedASR, stats: SessionStats,
               printer: PartialPrinter, refiner: "Refiner | None" = None,
               history: AudioHistory | None = None,
               translator_worker: "TranslationWorker | None" = None,
               speaker_labeler=None):
    audio_pos = 0.0
    last_partial = 0.0
    early_lang = None  # LID result computed mid-utterance so finals skip it
    if history is None:
        history = AudioHistory(sample_rate)
    for chunk in chunks:
        if not isinstance(chunk, np.ndarray):
            # non-ndarray = a "flush now" signal (ws_ingest.FLUSH on client
            # disconnect): finalize whatever the VAD has in progress instead
            # of waiting for silence that may never arrive.
            vad.flush()
            if drain_segments(vad, sample_rate, asr, stats, printer, history, early_lang,
                              refiner=refiner,
                              translator_worker=translator_worker,
                              speaker_labeler=speaker_labeler):
                early_lang = None
            if refiner is not None:
                refiner.maybe_refine(int(audio_pos * sample_rate), force=True)
            continue

        vad.accept_waveform(chunk)
        history.push(chunk)
        audio_pos += len(chunk) / sample_rate
        stats.total_audio_s += len(chunk) / sample_rate

        if vad.is_speech_detected() and audio_pos - last_partial >= PARTIAL_EVERY_S:
            last_partial = audio_pos
            cur = np.asarray(vad.current_segment.samples, dtype=np.float32)
            if len(cur) > int(PARTIAL_WINDOW_S * sample_rate):
                cur = cur[-int(PARTIAL_WINDOW_S * sample_rate):]
            # --mode single (asr.forced_lang set): every segment is forced to
            # one language, so the early-LID probe (whisper-tiny plus a
            # background model prefetch) would just be wasted work -- asr.partial()
            # already routes straight to forced_lang without needing a hint.
            if asr.forced_lang is None and early_lang is None and len(cur) >= int(2.0 * sample_rate):
                early_lang = asr.identify(cur, sample_rate)
            if printer.enabled and len(cur) >= sample_rate // 2:
                partial_text = asr.partial(cur, sample_rate, lang_hint=early_lang)
                printer.show(partial_text)
                # Kick off a draft translation WHILE the speaker is still
                # talking, so the confirmed line's translation isn't the
                # first thing the viewer sees (API round-trips take 1-5s).
                # The worker throttles submissions itself (growth-based +
                # 1s cadence, see TranslationWorker.submit_partial).
                if translator_worker is not None and partial_text.strip():
                    translator_worker.submit_partial(
                        partial_text, early_lang or getattr(asr, "last_lang", None) or "ja")

        if drain_segments(vad, sample_rate, asr, stats, printer, history, early_lang,
                          refiner=refiner,
                          translator_worker=translator_worker,
                          speaker_labeler=speaker_labeler):
            early_lang = None
        if refiner is not None and not vad.is_speech_detected():
            refiner.maybe_refine(int(audio_pos * sample_rate))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", help="wav file to simulate streaming from (16kHz mono s16)")
    ap.add_argument("--no-realtime", action="store_true", help="don't sleep between chunks in --wav mode")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--no-partial", action="store_true", help="disable in-progress draft subtitles")
    ap.add_argument("--min-silence", type=float, default=0.35,
                    help="silence (s) that ends an utterance; lower = snappier finals, more splits")
    ap.add_argument("--max-speech", type=float, default=12.0,
                    help="force-finalize an utterance after this many seconds of continuous speech")
    ap.add_argument("--max-resident", type=int, default=3,
                    help="max non-tier0 models kept in memory (LRU eviction); 0 or less = unlimited")
    ap.add_argument("--serve", type=int, nargs="?", const=8833, default=None, metavar="PORT",
                    help="serve an OBS browser-source overlay at http://localhost:PORT (default 8833)")
    ap.add_argument("--no-refine", action="store_true",
                    help="disable the second-pass re-decode of utterance groups")
    ap.add_argument("--transcript", metavar="PATH",
                    help="append refined transcript lines to this file")
    ap.add_argument("--hotwords", metavar="PATH", default="",
                    help="hotword list (one per line) to bias Japanese decoding")
    ap.add_argument("--replace", metavar="PATH", default="",
                    help="user dictionary: 'wrong=right' per line, applied to all output")
    ap.add_argument("--mode", choices=["single", "balanced", "fast"], default="balanced",
                    help="language-switch policy preset (default balanced). single: fixed to "
                         "--lang, no LID/switching at all. balanced: dual-LID switch "
                         "confirmation for ja/en/zh/ko/yue via SenseVoice, length+repeat-count "
                         "hysteresis for other languages (docs/LID.md). fast: switch "
                         "immediately on any whisper-tiny detection, no confirmation "
                         "(equivalent to --lid-switch-confirm 1 --lang-switch-guard 0). "
                         "The individual --lang-switch-guard/--lid-switch-confirm flags below "
                         "still override the preset's values when passed explicitly.")
    ap.add_argument("--lang", metavar="CODE", default=None,
                    help="required by --mode single: force every segment to this language "
                         "code, skipping LID and switch logic entirely")
    ap.add_argument("--lang-switch-guard", type=float, default=None, metavar="SEC",
                    help="treat a new-language detection shorter than SEC as noise: it never "
                         "counts toward confirming a switch (see --lid-switch-confirm) and it "
                         "suppresses the omnilingual fallback on an empty decode "
                         "(0 disables; raise for single-language streams). Only used as the "
                         "fallback policy for languages SenseVoice can't confirm (see --mode). "
                         "Default depends on --mode (2.0 for balanced, 0 for fast).")
    ap.add_argument("--lid-switch-confirm", type=int, default=None, metavar="N",
                    help="consecutive same-language detections (each >= --lang-switch-guard "
                         "long) required before the session switches to a new language; "
                         "raise for stickier single-language sessions. Only used as the "
                         "fallback policy for languages SenseVoice can't confirm (see --mode). "
                         "Default depends on --mode (2 for balanced, 1 for fast).")
    ap.add_argument("--speakers", action="store_true",
                    help="label utterances with speaker ids (S1, S2, ...)")
    ap.add_argument("--translate", nargs="?", const="en", default=None, metavar="LANGS",
                    help="translate lines to these languages, comma-separated "
                         "(default en). Two kinds of targets, mixable: "
                         "plain codes (en=FuguMT; any other M2M-100 target code such as "
                         "zh, ko, es, fr, de ... is accepted if the model's vocabulary "
                         "supports it) translate Japanese lines; prefixed targets "
                         "(api:zh, api:en,ko or bare api) use the OpenAI-compatible API "
                         "configured in openai_translate.json (or the --api-config file) "
                         "and translate the DETECTED language of every line. Only zh/ko "
                         "have measured local-model quality so far -- other local targets "
                         "print an 'unvalidated' note to stderr, see docs/TRANSLATE_M2M.md")
    ap.add_argument("--api-config", metavar="PATH", default=None,
                    help="path to an openai_translate.json config file. If omitted, "
                         "scripts/../openai_translate.json in the project root is "
                         "detected automatically (when present). The api: translate "
                         "route only works when a usable config exists.")
    ap.add_argument("--input", choices=["mic", "wav", "ws", "speaker", "mix"], default=None,
                    help="audio source; default is mic, or wav if --wav is given. "
                         "'speaker' transcribes whatever is playing on the PC's audio "
                         "output (WASAPI loopback, Windows only -- no Stereo Mix setup "
                         "needed); 'mix' transcribes mic + speaker summed into one "
                         "stream (e.g. meeting transcription: your voice and the call "
                         "audio together)")
    ap.add_argument("--mic-device", default=None, metavar="NAME",
                    help="substring match for the input device --input mic/mix captures "
                         "from (default: system default input device). See "
                         "--list-audio-devices.")
    ap.add_argument("--speaker-device", default=None, metavar="NAME",
                    help="substring match for the output device --input speaker/mix "
                         "loops back from (default: system default output device). See "
                         "--list-audio-devices.")
    ap.add_argument("--list-audio-devices", action="store_true",
                    help="print available audio input/output devices and exit -- use to "
                         "find a name for --mic-device/--speaker-device")
    ap.add_argument("--ws-host", default="0.0.0.0", metavar="HOST",
                    help="bind host for --input ws (default 0.0.0.0, so an ESP32/phone "
                         "on the LAN can reach it; use 127.0.0.1 to restrict to localhost)")
    ap.add_argument("--ws-port", type=int, default=8766, metavar="PORT",
                    help="port for the --input ws /ingest endpoint (default 8766)")
    args = ap.parse_args()

    if args.list_audio_devices:
        import sounddevice as sd

        print(sd.query_devices())
        if sys.platform == "win32":
            try:
                import soundcard as sc

                default_id = sc.default_speaker().id
                print("\nWASAPI output devices (for --speaker-device):")
                for s in sc.all_speakers():
                    print(f"  {s.name}" + ("  (default)" if s.id == default_id else ""))
            except Exception as exc:
                print(f"\n(soundcard loopback devices unavailable: {exc})", file=sys.stderr)
        return

    if args.mode == "single" and not args.lang:
        ap.error("--mode single requires --lang CODE")
    if args.input in ("speaker", "mix") and sys.platform != "win32":
        ap.error("--input speaker/mix needs Windows (WASAPI loopback)")
    # --mode bundles defaults for the two hysteresis knobs; an explicitly
    # passed --lang-switch-guard/--lid-switch-confirm still wins. "single"
    # has no entry here: forced_lang (set below) bypasses all switch/
    # hysteresis logic in asr_engine.RoutedASR.transcribe(), so these two
    # knobs are never read for that mode.
    mode_defaults = {"balanced": (2.0, 2), "fast": (0.0, 1)}
    default_guard, default_confirm = mode_defaults.get(args.mode, (2.0, 2))
    if args.lang_switch_guard is None:
        args.lang_switch_guard = default_guard
    if args.lid_switch_confirm is None:
        args.lid_switch_confirm = default_confirm

    # API translation config: --api-config path wins; otherwise auto-detect
    # the project-root openai_translate.json. A usable config enables the
    # "api:..." translation route (see build_translators).
    global _api_cfg
    from translate_api import api_targets as _api_targets
    from translate_api import load_config as _load_api_config

    if args.api_config:
        _api_cfg = _load_api_config(args.api_config)
        if _api_cfg is None:
            print(f"[api] config {args.api_config} is missing or unusable "
                  f"(empty base_url/model) -- api: route disabled",
                  file=sys.stderr)
    else:
        from translate_api import CONFIG_PATH as _API_CONFIG_PATH
        from translate_api import load_config as _load_api_config

        _api_cfg = _load_api_config(_API_CONFIG_PATH)
    if _api_cfg is not None:
        print(f"[api] api translate configured: base_url={_api_cfg['base_url']} "
              f"model={_api_cfg['model']} targets={sorted(_api_targets(_api_cfg))}",
              file=sys.stderr)

    server = None
    if args.serve:
        from subtitle_server import SubtitleServer

        server = SubtitleServer(port=args.serve).start()
        print(f"subtitle overlay: http://localhost:{args.serve}/  (OBS browser source)",
              file=sys.stderr)

    print("loading models...", file=sys.stderr)
    asr = RoutedASR(threads=args.threads,
                    max_resident=args.max_resident if args.max_resident > 0 else None,
                    hotwords_file=args.hotwords, replace_file=args.replace,
                    lid_switch_confirm=max(args.lid_switch_confirm, 1),
                    dual_confirm=(args.mode != "fast"),
                    forced_lang=args.lang if args.mode == "single" else None)
    asr.min_switch_s = max(args.lang_switch_guard, 0.0)
    vad = build_vad(args.min_silence, args.max_speech)
    stats = SessionStats()
    printer = PartialPrinter(enabled=not args.no_partial, server=server)

    speaker_labeler = None
    if args.speakers:
        from speaker_id import SpeakerLabeler

        speaker_labeler = SpeakerLabeler()

    # Translation worker always exists (even with no --translate) so it can be
    # hot-activated later via the stdin command thread below -- no restart needed.
    translators = {}
    translator_worker = TranslationWorker(translators, server=server)
    if args.translate:
        print(f"loading translators ({args.translate})...", file=sys.stderr)
        translators = build_translators(args.translate)
        if translators:
            translator_worker.set_langs(translators)


    # Bound below (with --no-refine it stays None); the stdin thread and the
    # POST /api/translate callback close over this name and may fire before
    # the Refiner exists, so the cell must never be unbound.
    refiner = None

    def hot_commands(worker: TranslationWorker):
        """Live stdin commands (run-time translation switch, no restart):
            translate en,zh,ko   -> build + activate translators
            translate off        -> deactivate translation
            quit                 -> exit the process
        Feedback goes to stderr so the tty partial-printer isn't clobbered.
        """
        for line in sys.stdin:
            line = line.strip().lower()
            if not line:
                continue
            if line.startswith("translate "):
                apply_translation_spec(line[len("translate "):], worker,
                                       lambda: refiner)
            elif line in ("quit", "exit"):
                print("[cmd] bye", file=sys.stderr, flush=True)
                raise SystemExit(0)

    threading.Thread(target=hot_commands, args=(translator_worker,), daemon=True).start()

    # Register the shared switcher with the HTTP server so the desktop
    # subtitle window's language button can hot-toggle translation too
    # (POST /api/translate). Both entry points use the same apply function.
    if server is not None:
        server.set_translate_callback(
            lambda langs: apply_translation_spec(langs, translator_worker,
                                                 lambda: refiner))

    history = AudioHistory(SAMPLE_RATE)
    refiner = None if args.no_refine else Refiner(asr, history, SAMPLE_RATE, printer,
                                                  transcript_path=args.transcript,
                                                  translators=translators, stats=stats)

    def finish(sr):
        vad.flush()
        drain_segments(vad, sr, asr, stats, printer, history,
                       refiner=refiner,
                       translator_worker=translator_worker,
                       speaker_labeler=speaker_labeler)
        if refiner is not None:
            refiner.maybe_refine(0, force=True)

    input_mode = args.input or ("wav" if args.wav else "mic")

    try:
        if server is not None:
            server.publish({"type": "session_start"})
        if input_mode == "wav":
            if not args.wav:
                ap.error("--input wav requires --wav PATH")
            samples, sr = read_wave(args.wav)  # resampled to SAMPLE_RATE if needed
            run_stream(wav_chunks(samples, sr, realtime=not args.no_realtime),
                       vad, sr, asr, stats, printer, refiner, history, translator_worker,
                       speaker_labeler)
            finish(sr)
        elif input_mode == "ws":
            from ws_ingest import INGEST_PATH, IngestServer

            ingest = IngestServer(args.ws_host, args.ws_port, sample_rate=SAMPLE_RATE,
                                  subtitle_server=server).start()
            print(f"ws ingest: ws://{args.ws_host}:{args.ws_port}{INGEST_PATH}  "
                  f"(JSON handshake, then binary pcm_s16le frames)", file=sys.stderr)
            run_stream(ws_chunks(ingest), vad, SAMPLE_RATE, asr, stats, printer, refiner,
                       history, translator_worker, speaker_labeler)
        elif input_mode == "speaker":
            run_stream(speaker_chunks(device_name=args.speaker_device),
                       vad, SAMPLE_RATE, asr, stats, printer, refiner, history,
                       translator_worker, speaker_labeler)
        elif input_mode == "mix":
            run_stream(mix_chunks(mic_device_name=args.mic_device,
                                  speaker_device_name=args.speaker_device),
                       vad, SAMPLE_RATE, asr, stats, printer, refiner, history,
                       translator_worker, speaker_labeler)
        else:
            run_stream(mic_chunks(device_name=args.mic_device), vad, SAMPLE_RATE, asr,
                       stats, printer, refiner, history,
                       translator_worker, speaker_labeler)
    except KeyboardInterrupt:
        finish(SAMPLE_RATE)
    finally:
        print(f"\n=== session summary: {stats.summary()} ===")
        # Release the pipeline explicitly, AFTER the summary print above so
        # both report identical numbers -- Refiner.close() drains whatever
        # refine work is still queued (the same drain finish() does) and
        # asr.close() then frees the recognizers, so a caller that imports
        # this module and calls main() in-process gets its memory back
        # instead of holding it until the interpreter exits. Order matters:
        # the refiner decodes through the engine, so it stops first.
        if refiner is not None:
            refiner.close()
        translator_worker.close()
        asr.close()


if __name__ == "__main__":
    main()
