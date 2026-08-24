"""Offline (non-streaming) ASR benchmark for CPU.

Measures RTF (real-time factor) and wall-clock latency for a set of wav files.
Currently supports sherpa-onnx transducer models (Parakeet TDT v3 etc.).

Usage:
    python scripts/bench_offline.py --model-dir models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 [--wav path ...] [--threads 6] [--runs 3]

If no --wav is given, uses the test_wavs bundled with the model.
"""
import argparse
import glob
import os
import sys
import time
import wave

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np


def read_wave(path: str):
    with wave.open(path, "rb") as f:
        assert f.getsampwidth() == 2, f"{path}: expected 16-bit PCM"
        num_channels = f.getnchannels()
        sample_rate = f.getframerate()
        data = f.readframes(f.getnframes())
    samples = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if num_channels > 1:
        samples = samples.reshape(-1, num_channels).mean(axis=1)
    return samples, sample_rate


def build_recognizer(model_dir: str, threads: int, language: str = ""):
    import sherpa_onnx

    def find(pattern):
        hits = glob.glob(os.path.join(model_dir, pattern))
        return hits[0] if hits else ""

    tokens = os.path.join(model_dir, "tokens.txt")
    encoder = find("encoder*.onnx")
    decoder = find("decoder*.onnx")
    joiner = find("joiner*.onnx")
    if encoder and decoder and joiner:
        return sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            tokens=tokens,
            num_threads=threads,
            model_type="nemo_transducer",
        )

    single = find("model*.onnx")
    assert single, f"no onnx model found in {model_dir}"
    if "sense-voice" in os.path.basename(os.path.normpath(model_dir)):
        return sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=single,
            tokens=tokens,
            num_threads=threads,
            use_itn=True,
            language=language,  # "" = auto; SenseVoice misroutes ja->zh on auto
        )
    return sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
        model=single,
        tokens=tokens,
        num_threads=threads,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--wav", nargs="*", default=None)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--language", default="", help="language hint for sense-voice models (e.g. ja, en)")
    args = ap.parse_args()

    wavs = args.wav or sorted(glob.glob(os.path.join(args.model_dir, "test_wavs", "*.wav")))
    assert wavs, "no wav files found"

    t0 = time.perf_counter()
    rec = build_recognizer(args.model_dir, args.threads, args.language)
    load_s = time.perf_counter() - t0
    print(f"model load: {load_s:.2f}s  (threads={args.threads})")

    total_audio = 0.0
    total_proc = 0.0
    for wav_path in wavs:
        samples, sr = read_wave(wav_path)
        audio_s = len(samples) / sr

        best = None
        text = ""
        for _ in range(args.runs):
            stream = rec.create_stream()
            stream.accept_waveform(sr, samples)
            t0 = time.perf_counter()
            rec.decode_stream(stream)
            dt = time.perf_counter() - t0
            best = dt if best is None else min(best, dt)
            text = stream.result.text

        rtf = best / audio_s
        total_audio += audio_s
        total_proc += best
        print(f"\n{os.path.basename(wav_path)}  audio={audio_s:.2f}s  decode={best:.3f}s  RTF={rtf:.4f}  ({1/rtf:.0f}x realtime)")
        print(f"  text: {text}")

    print(f"\n=== total: audio={total_audio:.2f}s  proc={total_proc:.3f}s  RTF={total_proc/total_audio:.4f}  ({total_audio/total_proc:.0f}x realtime) ===")


if __name__ == "__main__":
    main()
