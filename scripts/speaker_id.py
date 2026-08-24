"""Lightweight per-utterance speaker labeling (S1, S2, ...).

Not full diarization: each finalized VAD segment gets one speaker embedding
(CAM++ zh-en, 28MB) and is assigned to the nearest running centroid by
cosine similarity, or opens a new speaker when nothing is close enough.
Good for turn-taking conversations; overlapping speech stays one label.
"""
import os

import numpy as np
import sherpa_onnx

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
EMBED_MODEL = os.path.join(MODELS_DIR, "campplus_sv.onnx")

SIM_THRESHOLD = 0.45  # cosine similarity to join an existing speaker
MAX_EMBED_SECONDS = 6.0  # embeddings saturate; cap input length


class SpeakerLabeler:
    def __init__(self, threads: int = 2, threshold: float = SIM_THRESHOLD):
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=EMBED_MODEL, num_threads=threads
        )
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self._threshold = threshold
        self._centroids: list[np.ndarray] = []  # running mean per speaker
        self._counts: list[int] = []

    def label(self, samples: np.ndarray, sample_rate: int) -> str:
        max_len = int(MAX_EMBED_SECONDS * sample_rate)
        if len(samples) > max_len:
            samples = samples[:max_len]
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate, samples)
        stream.input_finished()
        emb = np.asarray(self._extractor.compute(stream), dtype=np.float32)
        emb /= np.linalg.norm(emb) + 1e-9

        best, best_sim = -1, -1.0
        for i, c in enumerate(self._centroids):
            sim = float(np.dot(emb, c) / (np.linalg.norm(c) + 1e-9))
            if sim > best_sim:
                best, best_sim = i, sim

        if best >= 0 and best_sim >= self._threshold:
            # fold into the running mean so the centroid tracks the speaker
            n = self._counts[best]
            self._centroids[best] = (self._centroids[best] * n + emb) / (n + 1)
            self._counts[best] = n + 1
            return f"S{best + 1}"

        self._centroids.append(emb)
        self._counts.append(1)
        return f"S{len(self._centroids)}"
