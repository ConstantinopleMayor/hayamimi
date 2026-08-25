"""Build noise-augmented copies of the real-speech eval sets.

Takes every clip from testdata/eval_real, testdata/eval_real_zhko and
testdata/eval_real_yue and re-renders it under 12 noisy conditions
(3 noise types x 4 SNR levels), so scripts/eval_noise.py can measure how much
the production RoutedASR path degrades under noise.

Noise types:
  - white:  white Gaussian noise (flat spectrum)
  - pink:   pink noise (1/f spectrum, via Voss-McCartney-style filtering of
            white noise in the frequency domain)
  - babble: 2-3 clips drawn at random from OTHER languages in the combined
            pool, concatenated/looped to cover the target's duration and
            summed together, imitating background talkers. Same-language
            clips are never used as a babble source for a given target.

SNR is defined relative to the target speech's RMS power:
    10*log10(P_speech / P_noise) == target_snr_db
i.e. the noise waveform is scaled so the mix hits that ratio exactly.

Output layout:
    <root>/testdata/eval_noisy/<noise>_snr<db>/<wav files> + manifest.json
(manifest.json carries over every field from the source entry unchanged,
except "wav" which points at the new filename in this condition's folder.)

Random seed is fixed at SEED = 42 for full reproducibility -- both the babble
source selection and the white/pink noise generation are seeded per-clip from
a deterministic RNG derived from (noise, snr, wav filename), so re-running
this script regenerates byte-identical output.

Usage:
    python scripts/make_noisyset.py --root H:\\Programming\\hayamimi
"""
import argparse
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import numpy as np
import soundfile as sf

SEED = 42

SOURCE_DIRS = ["testdata/eval_real", "testdata/eval_real_zhko", "testdata/eval_real_yue"]
NOISE_TYPES = ["white", "pink", "babble"]
SNR_LEVELS_DB = [20, 10, 5, 0]


def rng_for(*parts) -> np.random.Generator:
    """Deterministic per-clip RNG seeded from SEED + a string key, so output
    is reproducible regardless of iteration order."""
    key = "|".join(str(p) for p in parts)
    h = hashlib.sha256(f"{SEED}|{key}".encode("utf-8")).digest()
    seed32 = int.from_bytes(h[:4], "big")
    return np.random.default_rng(seed32)


def white_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.standard_normal(n).astype(np.float32)


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """1/f noise via FFT filtering of white noise."""
    white = rng.standard_normal(n)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n)
    freqs[0] = freqs[1] if n > 1 else 1.0  # avoid div-by-zero at DC
    scale = 1.0 / np.sqrt(freqs)
    scale[0] = scale[1]
    pink = np.fft.irfft(spectrum * scale, n)
    pink = pink / (np.std(pink) + 1e-12)
    return pink.astype(np.float32)


def load_mono(path: str):
    samples, sr = sf.read(path, dtype="float32", always_2d=False)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    return samples, sr


def babble_noise(n: int, sr: int, target_lang: str, pool: list, rng: np.random.Generator) -> np.ndarray:
    """Sum 2-3 randomly picked clips from a different language, looped/
    trimmed to length n, at the target sample rate."""
    candidates = [c for c in pool if c["lang"] != target_lang]
    if not candidates:
        # degenerate case: fall back to white noise if somehow every clip
        # shares the target's language (shouldn't happen with 5 languages)
        return white_noise(n, rng)

    k = int(rng.integers(2, 4))  # 2 or 3
    picks = rng.choice(len(candidates), size=min(k, len(candidates)), replace=False)
    mix = np.zeros(n, dtype=np.float32)
    for idx in picks:
        c = candidates[int(idx)]
        src, src_sr = load_mono(c["path"])
        if src_sr != sr:
            # simple linear resample -- babble is background noise, exact
            # fidelity doesn't matter here
            src_t = np.linspace(0, 1, len(src), endpoint=False)
            dst_t = np.linspace(0, 1, int(len(src) * sr / src_sr), endpoint=False)
            src = np.interp(dst_t, src_t, src).astype(np.float32)
        if len(src) == 0:
            continue
        # loop/tile to cover length n, then take a random offset window so
        # different targets don't all start the babble at t=0
        reps = n // len(src) + 2
        tiled = np.tile(src, reps)
        offset = int(rng.integers(0, len(src)))
        clip = tiled[offset:offset + n]
        if len(clip) < n:
            clip = np.pad(clip, (0, n - len(clip)))
        rms = np.sqrt(np.mean(clip ** 2)) + 1e-12
        mix += clip / rms  # normalize each babble source to unit RMS before summing
    return mix


def scale_noise_to_snr(speech: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    speech_power = np.mean(speech.astype(np.float64) ** 2)
    noise_power = np.mean(noise.astype(np.float64) ** 2) + 1e-12
    target_noise_power = speech_power / (10 ** (snr_db / 10.0))
    gain = np.sqrt(target_noise_power / noise_power)
    return (noise * gain).astype(np.float32)


def mix_and_normalize(speech: np.ndarray, noise: np.ndarray) -> np.ndarray:
    mixed = speech + noise
    peak = np.max(np.abs(mixed))
    if peak > 1.0:
        mixed = mixed / peak
    return mixed.astype(np.float32)


def collect_pool(root: str) -> list:
    """Load every entry across the three source manifests, with resolved
    wav paths, into one flat pool for babble sourcing."""
    pool = []
    for rel in SOURCE_DIRS:
        mdir = os.path.join(root, rel)
        manifest_path = os.path.join(mdir, "manifest.json")
        if not os.path.exists(manifest_path):
            print(f"WARNING: missing {manifest_path}, skipping")
            continue
        with open(manifest_path, encoding="utf-8") as f:
            entries = json.load(f)
        for e in entries:
            pool.append({**e, "path": os.path.join(mdir, e["wav"]), "src_dir": rel})
    return pool


def build_condition(root: str, noise_type: str, snr_db: int, pool: list):
    out_dir = os.path.join(root, "testdata", "eval_noisy", f"{noise_type}_snr{snr_db}")
    os.makedirs(out_dir, exist_ok=True)
    manifest = []
    for e in pool:
        samples, sr = load_mono(e["path"])
        n = len(samples)
        rng = rng_for(noise_type, snr_db, e["wav"])
        if noise_type == "white":
            noise = white_noise(n, rng)
        elif noise_type == "pink":
            noise = pink_noise(n, rng)
        elif noise_type == "babble":
            noise = babble_noise(n, sr, e["lang"], pool, rng)
        else:
            raise ValueError(noise_type)

        noise = scale_noise_to_snr(samples, noise, snr_db)
        mixed = mix_and_normalize(samples, noise)

        out_name = e["wav"]
        sf.write(os.path.join(out_dir, out_name), mixed, sr)
        entry = {k: v for k, v in e.items() if k not in ("path", "src_dir")}
        manifest.append(entry)

    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"{noise_type}_snr{snr_db}: wrote {len(manifest)} clips -> {out_dir}")


def main():
    ap = argparse.ArgumentParser()
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--root", default=default_root,
                     help="repo root containing testdata/ and models/ (default: derived from script location)")
    args = ap.parse_args()

    pool = collect_pool(args.root)
    print(f"Loaded pool of {len(pool)} clips across {len(SOURCE_DIRS)} source sets "
          f"(langs: {sorted(set(e['lang'] for e in pool))})")
    if not pool:
        print("No source clips found -- check --root points at a checkout with testdata/eval_real*")
        sys.exit(1)

    for noise_type in NOISE_TYPES:
        for snr_db in SNR_LEVELS_DB:
            build_condition(args.root, noise_type, snr_db, pool)

    print(f"\nDone: {len(NOISE_TYPES) * len(SNR_LEVELS_DB)} conditions written under "
          f"{os.path.join(args.root, 'testdata', 'eval_noisy')}")


if __name__ == "__main__":
    main()
