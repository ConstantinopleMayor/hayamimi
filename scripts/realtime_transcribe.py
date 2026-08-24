"""Real-time (or simulated real-time) multilingual transcription pipeline.

Audio -> Silero VAD (sherpa-onnx) -> RoutedASR (asr_engine.RoutedASR) -> print.

Usage:
    python scripts/realtime_transcribe.py --wav testdata/ja_test.wav --no-realtime
    python scripts/realtime_transcribe.py --wav testdata/ja_test.wav       # paced with sleeps
    python scripts/realtime_transcribe.py                                 # live microphone
"""
import argparse
import os
import queue
import sys
import time
import wave

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import sherpa_onnx

from asr_engine import RoutedASR

SAMPLE_RATE = 16000
WINDOW_SIZE = 512  # samples per VAD chunk, ~32ms @ 16kHz
MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
VAD_MODEL = os.path.join(MODELS_DIR, "silero_vad.onnx")


def resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return samples
    duration = len(samples) / src_rate
    dst_n = int(round(duration * dst_rate))
    src_x = np.arange(len(samples)) / src_rate
    dst_x = np.arange(dst_n) / dst_rate
    return np.interp(dst_x, src_x, samples).astype(np.float32)


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


def build_vad() -> sherpa_onnx.VoiceActivityDetector:
    cfg = sherpa_onnx.VadModelConfig(
        silero_vad=sherpa_onnx.SileroVadModelConfig(
            model=VAD_MODEL,
            min_silence_duration=0.5,
            min_speech_duration=0.25,
            window_size=WINDOW_SIZE,
        ),
        sample_rate=SAMPLE_RATE,
        num_threads=1,
    )
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=30)


def wav_chunks(samples: np.ndarray, sample_rate: int, realtime: bool):
    pos = 0
    n = len(samples)
    while pos < n:
        chunk = samples[pos:pos + WINDOW_SIZE]
        if len(chunk) < WINDOW_SIZE:
            chunk = np.pad(chunk, (0, WINDOW_SIZE - len(chunk)))
        if realtime:
            time.sleep(WINDOW_SIZE / sample_rate)
        yield chunk
        pos += WINDOW_SIZE


def mic_chunks():
    import sounddevice as sd

    q: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, frames, time_info, status):
        q.put(indata[:, 0].copy())

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                         blocksize=WINDOW_SIZE, callback=callback):
        while True:
            yield q.get()


PARTIAL_EVERY_S = 0.5   # decode a draft this often (in audio time) during speech
PARTIAL_WINDOW_S = 8.0  # cap draft decoding to the last N seconds of the utterance


class PartialPrinter:
    """Shows in-progress drafts; overwrites in place on a tty, one line otherwise."""

    def __init__(self, enabled: bool):
        self.enabled = enabled
        self._tty = sys.stdout.isatty()
        self._last_len = 0

    def show(self, text: str):
        if not self.enabled or not text:
            return
        if self._tty:
            pad = max(self._last_len - len(text), 0)
            print("\r~ " + text + " " * pad, end="", flush=True)
            self._last_len = len(text)
        else:
            print(f"~ {text}")

    def clear(self):
        if self.enabled and self._tty and self._last_len:
            print("\r" + " " * (self._last_len + 2) + "\r", end="", flush=True)
            self._last_len = 0


class SessionStats:
    def __init__(self):
        self.total_audio_s = 0.0
        self.segments = 0
        self.latencies_ms: list[float] = []

    def summary(self) -> str:
        if not self.latencies_ms:
            return f"total_audio={self.total_audio_s:.1f}s segments=0"
        mean = sum(self.latencies_ms) / len(self.latencies_ms)
        return (f"total_audio={self.total_audio_s:.1f}s segments={self.segments} "
                f"mean_latency={mean:.0f}ms max_latency={max(self.latencies_ms):.0f}ms")


def drain_segments(vad, sample_rate: int, asr: RoutedASR, stats: SessionStats,
                   printer: PartialPrinter):
    while not vad.empty():
        segment = vad.front
        seg_end_time = time.perf_counter()  # segment-end reference point for latency
        samples = np.asarray(segment.samples, dtype=np.float32)
        vad.pop()

        seg_s = len(samples) / sample_rate
        result = asr.transcribe(samples, sample_rate)
        latency_ms = (time.perf_counter() - seg_end_time) * 1000

        stats.segments += 1
        stats.latencies_ms.append(latency_ms)
        printer.clear()
        print(f"[{result['lang']}/{result.get('tier', '?')}] {result['text']}  "
              f"(seg={seg_s:.1f}s, lid={result['lid_ms']:.0f}ms, "
              f"decode={result['decode_ms']:.0f}ms, latency={latency_ms:.0f}ms)")


def run_stream(chunks, vad, sample_rate: int, asr: RoutedASR, stats: SessionStats,
               printer: PartialPrinter):
    audio_pos = 0.0
    last_partial = 0.0
    for chunk in chunks:
        vad.accept_waveform(chunk)
        audio_pos += len(chunk) / sample_rate
        stats.total_audio_s += len(chunk) / sample_rate

        if printer.enabled and vad.is_speech_detected() and audio_pos - last_partial >= PARTIAL_EVERY_S:
            last_partial = audio_pos
            cur = np.asarray(vad.current_segment.samples, dtype=np.float32)
            if len(cur) > int(PARTIAL_WINDOW_S * sample_rate):
                cur = cur[-int(PARTIAL_WINDOW_S * sample_rate):]
            if len(cur) >= sample_rate // 2:  # need ~0.5s before a draft is useful
                printer.show(asr.partial(cur, sample_rate))

        drain_segments(vad, sample_rate, asr, stats, printer)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", help="wav file to simulate streaming from (16kHz mono s16)")
    ap.add_argument("--no-realtime", action="store_true", help="don't sleep between chunks in --wav mode")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--no-partial", action="store_true", help="disable in-progress draft subtitles")
    args = ap.parse_args()

    print("loading models...", file=sys.stderr)
    asr = RoutedASR(threads=args.threads)
    vad = build_vad()
    stats = SessionStats()
    printer = PartialPrinter(enabled=not args.no_partial)

    try:
        if args.wav:
            samples, sr = read_wave(args.wav)  # resampled to SAMPLE_RATE if needed
            run_stream(wav_chunks(samples, sr, realtime=not args.no_realtime),
                       vad, sr, asr, stats, printer)
            vad.flush()
            drain_segments(vad, sr, asr, stats, printer)
        else:
            run_stream(mic_chunks(), vad, SAMPLE_RATE, asr, stats, printer)
    except KeyboardInterrupt:
        vad.flush()
        drain_segments(vad, SAMPLE_RATE, asr, stats, printer)
    finally:
        print(f"\n=== session summary: {stats.summary()} ===")


if __name__ == "__main__":
    main()
