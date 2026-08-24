"""Routed multilingual ASR engine on top of sherpa-onnx.

Loads a Japanese NeMo CTC model and a multilingual (25 European langs) NeMo
transducer model (Parakeet TDT v3), plus a whisper-tiny based spoken language
identifier used to route each audio segment to the right recognizer.
"""
import glob
import os
import time
from dataclasses import dataclass

import numpy as np
import sherpa_onnx

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
V3_MODEL_DIR = os.path.join(MODELS_DIR, "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8")
JA_MODEL_DIR = os.path.join(MODELS_DIR, "sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8")
WHISPER_TINY_DIR = os.path.join(MODELS_DIR, "sherpa-onnx-whisper-tiny")

# Languages covered by the Parakeet-TDT-0.6B-v3 multilingual model.
V3_LANGS = {
    "bg", "hr", "cs", "da", "nl", "en", "et", "fi", "fr", "de", "el", "hu",
    "it", "lv", "lt", "mt", "pl", "pt", "ro", "sk", "sl", "es", "sv", "ru", "uk",
}

LID_MAX_SECONDS = 4.0  # only feed the first N seconds of a segment to the LID model


def _find(model_dir: str, pattern: str) -> str:
    hits = glob.glob(os.path.join(model_dir, pattern))
    return hits[0] if hits else ""


def _build_v3_recognizer(threads: int):
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=_find(V3_MODEL_DIR, "encoder*.onnx"),
        decoder=_find(V3_MODEL_DIR, "decoder*.onnx"),
        joiner=_find(V3_MODEL_DIR, "joiner*.onnx"),
        tokens=os.path.join(V3_MODEL_DIR, "tokens.txt"),
        num_threads=threads,
        model_type="nemo_transducer",
    )


def _build_ja_recognizer(threads: int):
    return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=_find(JA_MODEL_DIR, "model*.onnx"),
        tokens=os.path.join(JA_MODEL_DIR, "tokens.txt"),
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
    """Loads all models once and routes each segment to the right recognizer."""

    def __init__(self, threads: int = 4, warmup: bool = True):
        self.v3 = _build_v3_recognizer(threads)
        self.ja = _build_ja_recognizer(threads)
        self.lid = _build_lid(threads)
        if warmup:
            # first inference pays one-time kernel/allocation costs; pay it here
            # with 1s of silence so the first real segment isn't penalized.
            silence = np.zeros(16000, dtype=np.float32)
            self._identify_lang(silence, 16000)
            self._decode(self.v3, silence, 16000)
            self._decode(self.ja, silence, 16000)

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
        return stream.result.text

    def transcribe(self, samples: np.ndarray, sample_rate: int) -> dict:
        t0 = time.perf_counter()
        lang = self._identify_lang(samples, sample_rate)
        lid_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        if lang == "ja":
            text = self._decode(self.ja, samples, sample_rate)
        else:
            text = self._decode(self.v3, samples, sample_rate)
            if not text.strip() and lang not in V3_LANGS:
                # cheap safety net: v3 came back empty and LID guessed something
                # it doesn't cover (often CJK misdetection) -> retry with ja model.
                text = self._decode(self.ja, samples, sample_rate)
                lang = "ja"
        decode_ms = (time.perf_counter() - t0) * 1000

        return {"text": text, "lang": lang, "lid_ms": lid_ms, "decode_ms": decode_ms}
