"""Routed multilingual ASR engine on top of sherpa-onnx.

Piper-style tiered catalog: a whisper-tiny spoken-language identifier routes
each audio segment to the best model for that language.

  tier 0  ja                   -> ReazonSpeech k2 zipformer (best real-speech ja, fastest)
  tier 1  zh                   -> Paraformer-zh (best real-speech zh)
  tier 1  ko/yue               -> SenseVoice small
  tier 2  en + 24 EU langs     -> Parakeet TDT v3 (casing + punctuation)
  tier 3  everything else      -> Omnilingual ASR 300M CTC (1600+ languages)

Models are loaded lazily on first use and, when `max_resident` is set, the
least-recently-used ones are unloaded so memory stays bounded no matter how
many languages a session wanders through.
"""
import glob
import os
import threading
import time

import numpy as np
import sherpa_onnx

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
V3_MODEL_DIR = os.path.join(MODELS_DIR, "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
SV_MODEL_DIR = os.path.join(MODELS_DIR, "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17")
OMNI_MODEL_DIR = os.path.join(MODELS_DIR, "omnilingual-300m-ctc-int8")
WHISPER_TINY_DIR = os.path.join(MODELS_DIR, "sherpa-onnx-whisper-tiny")
RZ_MODEL_DIR = os.path.join(MODELS_DIR, "sherpa-onnx-zipformer-ja-en-reazonspeech-2025-01-17")
PARA_ZH_DIR = os.path.join(MODELS_DIR, "sherpa-onnx-paraformer-zh-int8-2025-10-07")

# ReazonSpeech ja-en zipformer: on real broadcast Japanese it beats even
# whisper-turbo (CER 8.6% vs 13.8%) at RTF 0.02. See docs/EVAL_REAL.md.
# English goes to v3 instead: rz outputs unpunctuated ALL-CAPS English
# (WER 1.6% vs v3's 2.5%, but v3's casing/punctuation reads far better).
RZ_LANGS = {"ja"}

# Paraformer-zh beats SenseVoice on real Chinese (CER 5.6% vs 7.5%); the
# dedicated Korean zipformer is worse (30%), so ko stays on SenseVoice.
# See docs/EVAL_REAL_ZHKO.md.
PARA_LANGS = {"zh"}

# SenseVoice small coverage (built-in ITN and punctuation).
SV_LANGS = {"ko", "yue"}

# Languages covered by the Parakeet-TDT-0.6B-v3 multilingual model.
V3_LANGS = {
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu",
    "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
}

LID_MAX_SECONDS = 4.0  # only feed the first N seconds of a segment to the LID model


def _find(model_dir: str, pattern: str) -> str:
    hits = glob.glob(os.path.join(model_dir, pattern))
    return hits[0] if hits else ""


def _build_reazon(threads: int, hotwords_file: str = "", hotwords_score: float = 2.0):
    # modified_beam_search: CER 8.6% -> 5.8% on real broadcast ja for +25%
    # decode time (still 37x realtime). v3/en showed no gain and stays greedy.
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=_find(RZ_MODEL_DIR, "encoder-*.int8.onnx"),
        decoder=_find(RZ_MODEL_DIR, "decoder-*.int8.onnx"),
        joiner=_find(RZ_MODEL_DIR, "joiner-*.int8.onnx"),
        tokens=os.path.join(RZ_MODEL_DIR, "tokens.txt"),
        num_threads=threads,
        model_type="zipformer",
        decoding_method="modified_beam_search",
        hotwords_file=hotwords_file,
        hotwords_score=hotwords_score,
        modeling_unit="cjkchar",
    )


def _build_paraformer_zh(threads: int):
    return sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=_find(PARA_ZH_DIR, "model*.onnx"),
        tokens=os.path.join(PARA_ZH_DIR, "tokens.txt"),
        num_threads=threads,
    )


def _build_sense_voice(threads: int):
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=_find(SV_MODEL_DIR, "model*.onnx"),
        tokens=os.path.join(SV_MODEL_DIR, "tokens.txt"),
        num_threads=threads,
        use_itn=True,
        language="",  # auto: SenseVoice has its own internal LID for its 5 langs
    )


def _build_v3_recognizer(threads: int):
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=_find(V3_MODEL_DIR, "encoder*.onnx"),
        decoder=_find(V3_MODEL_DIR, "decoder*.onnx"),
        joiner=_find(V3_MODEL_DIR, "joiner*.onnx"),
        tokens=os.path.join(V3_MODEL_DIR, "tokens.txt"),
        num_threads=threads,
        model_type="nemo_transducer",
    )


def _build_omnilingual(threads: int):
    return sherpa_onnx.OfflineRecognizer.from_omnilingual_asr_ctc(
        model=_find(OMNI_MODEL_DIR, "model*.onnx"),
        tokens=os.path.join(OMNI_MODEL_DIR, "tokens.txt"),
        num_threads=threads,
    )


def _build_lid(threads: int):
    whisper_cfg = sherpa_onnx.SpokenLanguageIdentificationWhisperConfig(
        encoder=_find(WHISPER_TINY_DIR, "tiny-encoder.int8.onnx"),
        decoder=_find(WHISPER_TINY_DIR, "tiny-decoder.int8.onnx"),
    )
    cfg = sherpa_onnx.SpokenLanguageIdentificationConfig(whisper=whisper_cfg, num_threads=threads)
    return sherpa_onnx.SpokenLanguageIdentification(cfg)


def script_corrected_lang(tagged: str, text: str) -> str:
    """Correct an LID tag that contradicts the script of the decoded text."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return tagged
    cjk = sum(1 for c in letters if "぀" <= c <= "ヿ" or "一" <= c <= "鿿")
    frac = cjk / len(letters)
    if frac > 0.3 and tagged not in ("ja", "zh", "yue", "ko"):
        return "ja"
    if frac < 0.05 and tagged == "ja" and len(letters) >= 8:
        return "en"
    return tagged


def _load_replacements(path: str) -> list[tuple[str, str]]:
    """User dictionary: one "wrong=right" (or tab/arrow-separated) pair per line."""
    if not path:
        return []
    pairs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("=", "	", "→"):
                if sep in line:
                    wrong, right = line.split(sep, 1)
                    pairs.append((wrong.strip(), right.strip()))
                    break
    return pairs


_BUILDERS = {
    "rz": _build_reazon,
    "pz": _build_paraformer_zh,
    "sv": _build_sense_voice,
    "v3": _build_v3_recognizer,
    "omni": _build_omnilingual,
}

# preload priority when a residency cap is in effect
_PRELOAD_ORDER = ("pz", "sv", "v3", "omni")


class RoutedASR:
    """Lazily loads catalog models and routes each segment by detected language.

    max_resident bounds how many recognizers besides tier-0 ("rz", always kept:
    it is the ja/en primary and the default draft model) stay in memory; the
    least recently used one is dropped when the cap would be exceeded.
    """

    def __init__(self, threads: int = 4, warmup: bool = True, preload: bool = True,
                 max_resident: int | None = None, punctuate: bool = True,
                 hotwords_file: str = "", replace_file: str = ""):
        self._threads = threads
        self._models: dict[str, object] = {}
        self._last_used: dict[str, float] = {}
        self._max_resident = max_resident
        self._punctuate = punctuate
        self._punct = None
        self._ko_spacer = None
        self._ko_spacer_ok = True
        self._hotwords_file = hotwords_file
        self._replacements = _load_replacements(replace_file)
        self._load_lock = threading.Lock()  # punct + registry bookkeeping
        self._model_locks = {name: threading.Lock() for name in _BUILDERS}
        self.last_lang = None  # sticky language from the most recent final
        self._pending_lang = None   # candidate language seen on short utterances
        self._pending_count = 0
        self.lid = _build_lid(threads)
        if warmup:
            # LID + tier-0 pay their one-time kernel/allocation costs here
            # so the first real segment isn't penalized.
            silence = np.zeros(16000, dtype=np.float32)
            self._identify_lang(silence, 16000)
            self._decode(self._get("rz"), silence, 16000)
        if preload:
            # pull the other tiers in on a daemon thread so the first
            # non-tier-0 utterance doesn't pay the ~2s model-load cost.
            threading.Thread(target=self._preload_rest, daemon=True).start()

    def _preload_rest(self):
        if self._punctuate:
            self.punct  # first: ja finals need this almost immediately
        self.ko_spacer  # cheap (~1s); fixes SenseVoice's over-split Korean
        silence = np.zeros(16000, dtype=np.float32)
        budget = None if self._max_resident is None else self._max_resident
        for name in _PRELOAD_ORDER:
            if budget is not None:
                if budget <= 0:
                    break
                budget -= 1
            self._decode(self._get(name), silence, 16000)

    @property
    def punct(self):
        """Japanese punctuation restorer (BERT ONNX); None if unavailable."""
        if self._punct is None and self._punctuate:
            with self._load_lock:
                if self._punct is None:
                    try:
                        from punct_ja import PunctuatorJa

                        self._punct = PunctuatorJa()
                    except Exception:
                        self._punctuate = False  # missing model/deps: degrade quietly
        return self._punct

    @property
    def ko_spacer(self):
        """Kiwi morphological analyzer used to re-space Korean output."""
        if self._ko_spacer is None and self._ko_spacer_ok:
            with self._load_lock:
                if self._ko_spacer is None and self._ko_spacer_ok:
                    try:
                        from kiwipiepy import Kiwi

                        spacer = Kiwi()
                        spacer.space("한 국", reset_whitespace=True)  # warmup
                        self._ko_spacer = spacer
                    except Exception:
                        self._ko_spacer_ok = False  # missing dep: degrade quietly
        return self._ko_spacer

    def _get(self, name: str):
        rec = self._models.get(name)
        if rec is None:
            # per-model lock: loading v3 must not block a prefetch of omni
            with self._model_locks[name]:
                rec = self._models.get(name)
                if rec is None:
                    if name == "rz":
                        rec = _build_reazon(self._threads, self._hotwords_file)
                    else:
                        rec = _BUILDERS[name](self._threads)
                    with self._load_lock:
                        self._evict_if_needed(incoming=name)
                        self._models[name] = rec
        self._last_used[name] = time.monotonic()
        return rec

    def _evict_if_needed(self, incoming: str):
        if self._max_resident is None or incoming == "rz":
            return
        resident = [n for n in self._models if n != "rz"]
        if len(resident) < self._max_resident:
            return
        victim = min(resident, key=lambda n: self._last_used.get(n, 0.0))
        del self._models[victim]

    @property
    def resident_models(self) -> list[str]:
        return sorted(self._models)

    def _identify_lang(self, samples: np.ndarray, sample_rate: int) -> str:
        clip = samples
        max_len = int(LID_MAX_SECONDS * sample_rate)
        if len(clip) > max_len:
            clip = clip[:max_len]
        stream = self.lid.create_stream()
        stream.accept_waveform(sample_rate, clip)
        return self.lid.compute(stream)

    @staticmethod
    def _decode_full(rec, samples: np.ndarray, sample_rate: int) -> tuple[str, str]:
        stream = rec.create_stream()
        stream.accept_waveform(sample_rate, samples)
        rec.decode_stream(stream)
        text = stream.result.text
        # ReazonSpeech models emit TV-subtitle annotation brackets around
        # boundary words; they carry no speech content.
        for junk in ("［", "］", "〈", "〉"):
            text = text.replace(junk, "")
        return text, getattr(stream.result, "lang", "") or ""

    @classmethod
    def _decode(cls, rec, samples: np.ndarray, sample_rate: int) -> str:
        return cls._decode_full(rec, samples, sample_rate)[0]

    def _route(self, lang: str) -> tuple[object, str]:
        if lang in RZ_LANGS:
            return self._get("rz"), "rz"
        if lang in PARA_LANGS:
            return self._get("pz"), "pz"
        if lang in SV_LANGS:
            return self._get("sv"), "sv"
        if lang in V3_LANGS:
            return self._get("v3"), "v3"
        return self._get("omni"), "omni"

    def partial(self, samples: np.ndarray, sample_rate: int) -> str:
        """Fast draft transcription of an in-progress utterance.

        Routes by the last finalized language of the session (sticky), so a
        German speaker gets German drafts from the second utterance on.
        Before any final exists, drafts use the tier-0 ja/en model.
        """
        if self.last_lang is not None:
            rec, _ = self._route(self.last_lang)
        else:
            rec = self._get("rz")
        return self._replace(self._decode(rec, samples, sample_rate))

    def _replace(self, text: str) -> str:
        for wrong, right in self._replacements:
            text = text.replace(wrong, right)
        return text

    def identify(self, samples: np.ndarray, sample_rate: int) -> str:
        """Public LID hook so callers can identify the language mid-utterance.

        Also kicks off a background prefetch of that language's model, so by
        the time the utterance finalizes the recognizer is already resident.
        """
        lang = self._identify_lang(samples, sample_rate)
        threading.Thread(target=self._route, args=(lang,), daemon=True).start()
        return lang

    min_switch_s = 2.0  # a shorter utterance can't establish a new language

    def transcribe(self, samples: np.ndarray, sample_rate: int,
                   known_lang: str | None = None, speech_s: float | None = None,
                   live: bool = True) -> dict:
        """live=False (e.g. the refine pass re-decoding past audio) must not
        touch the sticky/pending language state of the live stream."""
        if known_lang is not None:
            lang, lid_ms = known_lang, 0.0
        else:
            t0 = time.perf_counter()
            lang = self._identify_lang(samples, sample_rate)
            lid_ms = (time.perf_counter() - t0) * 1000

        suppress_fallback = False
        if not live:
            pass  # out-of-band decode: leave the live language state alone
        elif (speech_s is not None and speech_s < self.min_switch_s
                and self.last_lang is not None and lang != self.last_lang):
            # A sub-2s utterance in a brand-new language is usually a
            # jingle/SFX misdetection (docs/VIDEO_TEST.md) -- but a speaker
            # genuinely switching in short phrases repeats the SAME language,
            # while noise lands on random ones. Accept the switch on the
            # second consecutive detection of one language.
            if lang == self._pending_lang:
                self._pending_count += 1
            else:
                self._pending_lang, self._pending_count = lang, 1
            if self._pending_count < 2:
                # first sighting: hold the session language; if that decode
                # comes back empty it was non-speech and the omni fallback
                # must not resurrect it.
                lang = self.last_lang
                suppress_fallback = True
            else:
                self._pending_lang, self._pending_count = None, 0
        else:
            self._pending_lang, self._pending_count = None, 0

        t0 = time.perf_counter()
        if lang == "zh":
            # whisper-tiny LID labels Cantonese as "zh" (measured 0/12 correct
            # on FLEURS yue), so let SenseVoice's internal LID arbitrate: keep
            # its transcript for yue, re-decode with Paraformer for true zh.
            text, sv_lang = self._decode_full(self._get("sv"), samples, sample_rate)
            if "yue" in sv_lang:
                lang, tier = "yue", "sv"
            else:
                text = self._decode(self._get("pz"), samples, sample_rate)
                tier = "pz"
        else:
            rec, tier = self._route(lang)
            text = self._decode(rec, samples, sample_rate)
        if not text.strip() and tier != "omni" and not suppress_fallback:
            # safety net: the specialist came back empty (likely LID mistake);
            # the 1600-language generalist gets the last word.
            text = self._decode(self._get("omni"), samples, sample_rate)
            tier = "omni"
        corrected = script_corrected_lang(lang, text)
        if live and text.strip() and corrected != lang:
            # the decoded script contradicts the LID tag (romaji-mangled
            # English under a ja tag, kanji under an en tag): one re-decode
            # with the right model fixes the final before anyone sees it
            rec2, tier2 = self._route(corrected)
            text2 = self._decode(rec2, samples, sample_rate)
            if text2.strip():
                lang, tier, text = corrected, tier2, text2

        text = self._replace(text)
        if lang == "ko" and text.strip() and self.ko_spacer is not None:
            try:
                # SenseVoice emits a space between every token; Kiwi restores
                # real Korean word spacing (docs/BENCHMARKS.md iteration 20)
                text = self.ko_spacer.space(text, reset_whitespace=True)
            except Exception:
                pass
        if lang == "ja" and text.strip() and self.punct is not None:
            try:
                text = self.punct.restore(text)
            except Exception:
                pass  # a punctuation failure must never lose the transcription
        decode_ms = (time.perf_counter() - t0) * 1000

        if live and text.strip():  # empty results must not poison the sticky language
            self.last_lang = lang
        return {"text": text, "lang": lang, "tier": tier, "lid_ms": lid_ms, "decode_ms": decode_ms}
