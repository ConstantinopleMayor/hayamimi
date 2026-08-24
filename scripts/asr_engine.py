"""Routed multilingual ASR engine on top of sherpa-onnx.

Piper-style tiered catalog: a whisper-tiny spoken-language identifier routes
each audio segment to the best model for that language.

  tier 0  ja/en                -> ReazonSpeech k2 zipformer (best real-speech ja, fastest)
  tier 1  zh/ko/yue            -> SenseVoice small
  tier 2  25 European langs    -> Parakeet TDT v3 (transducer)
  tier 3  everything else      -> Omnilingual ASR 300M CTC (1600+ languages)

Models are loaded lazily on first use, so memory stays proportional to the
languages actually spoken in the session.
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

# ReazonSpeech ja-en zipformer: on real broadcast Japanese it beats even
# whisper-turbo (CER 8.6% vs 13.8%) at RTF 0.02. See docs/EVAL_REAL.md.
RZ_LANGS = {"ja", "en"}

# SenseVoice small coverage (built-in ITN and punctuation).
SV_LANGS = {"zh", "ko", "yue"}

# Languages covered by the Parakeet-TDT-0.6B-v3 multilingual model.
V3_LANGS = {
    "bg", "hr", "cs", "da", "nl", "et", "fi", "fr", "de", "el", "hu",
    "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
}

LID_MAX_SECONDS = 4.0  # only feed the first N seconds of a segment to the LID model


def _find(model_dir: str, pattern: str) -> str:
    hits = glob.glob(os.path.join(model_dir, pattern))
    return hits[0] if hits else ""


def _build_sense_voice(threads: int):
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=_find(SV_MODEL_DIR, "model*.onnx"),
        tokens=os.path.join(SV_MODEL_DIR, "tokens.txt"),
        num_threads=threads,
        use_itn=True,
        language="",  # auto: SenseVoice has its own internal LID for its 5 langs
    )


def _build_reazon(threads: int):
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=_find(RZ_MODEL_DIR, "encoder-*.int8.onnx"),
        decoder=_find(RZ_MODEL_DIR, "decoder-*.int8.onnx"),
        joiner=_find(RZ_MODEL_DIR, "joiner-*.int8.onnx"),
        tokens=os.path.join(RZ_MODEL_DIR, "tokens.txt"),
        num_threads=threads,
        model_type="zipformer",
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


class RoutedASR:
    """Lazily loads catalog models and routes each segment by detected language."""

    def __init__(self, threads: int = 4, warmup: bool = True, preload: bool = True):
        self._threads = threads
        self._rz = None
        self._sv = None
        self._v3 = None
        self._omni = None
        self._load_lock = threading.Lock()
        self.last_lang = None  # sticky language from the most recent final
        self.lid = _build_lid(threads)
        if warmup:
            # LID + tier-1 model pay their one-time kernel/allocation costs here
            # so the first real segment isn't penalized.
            silence = np.zeros(16000, dtype=np.float32)
            self._identify_lang(silence, 16000)
            self._decode(self.reazon, silence, 16000)
        if preload:
            # pull tier-2/3 in on a daemon thread so the first non-tier-1
            # utterance doesn't pay the ~2s model-load cost.
            threading.Thread(target=self._preload_rest, daemon=True).start()

    def _preload_rest(self):
        silence = np.zeros(16000, dtype=np.float32)
        for rec in (self.sense_voice, self.v3, self.omni):
            self._decode(rec, silence, 16000)

    @property
    def reazon(self):
        if self._rz is None:
            with self._load_lock:
                if self._rz is None:
                    self._rz = _build_reazon(self._threads)
        return self._rz

    @property
    def sense_voice(self):
        if self._sv is None:
            with self._load_lock:
                if self._sv is None:
                    self._sv = _build_sense_voice(self._threads)
        return self._sv

    @property
    def v3(self):
        if self._v3 is None:
            with self._load_lock:
                if self._v3 is None:
                    self._v3 = _build_v3_recognizer(self._threads)
        return self._v3

    @property
    def omni(self):
        if self._omni is None:
            with self._load_lock:
                if self._omni is None:
                    self._omni = _build_omnilingual(self._threads)
        return self._omni

    def _identify_lang(self, samples: np.ndarray, sample_rate: int) -> str:
        clip = samples
        max_len = int(LID_MAX_SECONDS * sample_rate)
        if len(clip) > max_len:
            clip = clip[:max_len]
        stream = self.lid.create_stream()
        stream.accept_waveform(sample_rate, clip)
        return self.lid.compute(stream)

    @staticmethod
    def _decode(rec, samples: np.ndarray, sample_rate: int) -> str:
        stream = rec.create_stream()
        stream.accept_waveform(sample_rate, samples)
        rec.decode_stream(stream)
        text = stream.result.text
        # ReazonSpeech models emit TV-subtitle annotation brackets around
        # boundary words; they carry no speech content.
        return text.replace("［", "").replace("］", "")

    def _route(self, lang: str):
        if lang in RZ_LANGS:
            return self.reazon, "rz"
        if lang in SV_LANGS:
            return self.sense_voice, "sv"
        if lang in V3_LANGS:
            return self.v3, "v3"
        return self.omni, "omni"

    def partial(self, samples: np.ndarray, sample_rate: int) -> str:
        """Fast draft transcription of an in-progress utterance.

        Routes by the last finalized language of the session (sticky), so a
        German speaker gets German drafts from the second utterance on.
        Before any final exists, drafts use tier-1 SenseVoice (its built-in
        LID covers ja/zh/ko/yue/en).
        """
        if self.last_lang is not None:
            rec, _ = self._route(self.last_lang)
        else:
            rec = self.reazon
        return self._decode(rec, samples, sample_rate)

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> dict:
        t0 = time.perf_counter()
        lang = self._identify_lang(samples, sample_rate)
        lid_ms = (time.perf_counter() - t0) * 1000

        rec, tier = self._route(lang)
        t0 = time.perf_counter()
        text = self._decode(rec, samples, sample_rate)
        if not text.strip() and tier != "omni":
            # safety net: the specialist came back empty (likely LID mistake);
            # the 1600-language generalist gets the last word.
            text = self._decode(self.omni, samples, sample_rate)
            tier = "omni"
        decode_ms = (time.perf_counter() - t0) * 1000

        self.last_lang = lang
        return {"text": text, "lang": lang, "tier": tier, "lid_ms": lid_ms, "decode_ms": decode_ms}
