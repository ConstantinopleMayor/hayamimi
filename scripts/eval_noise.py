"""Noise-robustness scorecard for the production RoutedASR path.

Runs the same "real path" evaluation as scripts/eval_engine.py (LID ->
tier routing -> decode -> ja punctuation) over the clean real-speech eval
sets AND every noisy condition produced by scripts/make_noisyset.py, then
reports per (lang, noise, snr): LID accuracy, mean err, mean RTF, and the
degradation delta versus that language's clean baseline.

Noise conditions (see scripts/make_noisyset.py docstring for exact
definitions): white/pink/babble noise types x SNR in {20,10,5,0} dB,
speech-RMS-relative SNR, seed=42, fully reproducible.

Because the full sweep (13 conditions x ~65 clips) is slow on CPU, results
are checkpointed to a JSON cache (testdata/_noise_eval_cache.json under
--root; testdata/ is already gitignored) so this script can be re-run
multiple times, optionally scoped with --noise/--snr, and it will only
(re)compute conditions missing from the cache. Pass --report to (re)write
docs/NOISE.md from whatever is in the cache without evaluating anything.

Usage:
    python scripts/eval_noise.py --root H:\\Programming\\hayamimi
    python scripts/eval_noise.py --root H:\\Programming\\hayamimi --noise white
    python scripts/eval_noise.py --root H:\\Programming\\hayamimi --snr 0 5
    python scripts/eval_noise.py --root H:\\Programming\\hayamimi --report
"""
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import soundfile as sf

from asr_engine import RoutedASR
from eval_accuracy import cer_ja, wer_en

WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS_PATH = os.path.join(WORKTREE_ROOT, "docs", "NOISE.md")

CLEAN_DIRS = ["testdata/eval_real", "testdata/eval_real_zhko", "testdata/eval_real_yue"]
NOISE_TYPES = ["white", "pink", "babble"]
SNR_LEVELS_DB = [20, 10, 5, 0]
SEED = 42  # must match scripts/make_noisyset.py

try:
    from opencc import OpenCC

    _t2s = OpenCC("t2s").convert
except Exception:
    _t2s = None


def score(lang: str, ref: str, hyp: str) -> float:
    if lang == "en":
        return wer_en(ref, hyp)
    if lang == "yue" and _t2s is not None:
        ref, hyp = _t2s(ref), _t2s(hyp)
    return cer_ja(ref, hyp)[0]


def cache_path(root: str) -> str:
    return os.path.join(root, "testdata", "_noise_eval_cache.json")


def load_cache(root: str) -> dict:
    p = cache_path(root)
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(root: str, cache: dict):
    p = cache_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def condition_dir(root: str, noise: str, snr) -> str:
    if noise == "clean":
        return None  # clean uses CLEAN_DIRS, handled separately
    return os.path.join(root, "testdata", "eval_noisy", f"{noise}_snr{snr}")


def eval_manifest_dir(asr: RoutedASR, mdir: str):
    """Run every clip in mdir/manifest.json through the routed engine,
    return a list of per-clip result rows."""
    with open(os.path.join(mdir, "manifest.json"), encoding="utf-8") as f:
        entries = json.load(f)
    rows = []
    for e in entries:
        wav_path = os.path.join(mdir, e["wav"])
        samples, sr = sf.read(wav_path, dtype="float32")
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        dur = len(samples) / sr
        t0 = time.perf_counter()
        r = asr.transcribe(samples, sr)
        wall = time.perf_counter() - t0
        rows.append({
            "wav": e["wav"], "lang": e["lang"], "detected": r["lang"],
            "tier": r["tier"], "err": score(e["lang"], e["ref"], r["text"]),
            "rtf": wall / dur, "dur": dur,
        })
        print(f"  {e['wav']:16} true={e['lang']:3} lid={r['lang']:3} tier={r['tier']:4} "
              f"err={rows[-1]['err']:.3f} rtf={rows[-1]['rtf']:.3f}")
    return rows


def condition_key(noise, snr) -> str:
    return "clean" if noise == "clean" else f"{noise}_snr{snr}"


def run_conditions(root: str, conditions, cache: dict, asr_holder: list):
    for noise, snr in conditions:
        key = condition_key(noise, snr)
        if key in cache:
            print(f"[{key}] cached, skipping ({len(cache[key])} rows)")
            continue

        if asr_holder[0] is None:
            print("Loading RoutedASR (threads=6)...")
            asr_holder[0] = RoutedASR(threads=6, preload=False)
        asr = asr_holder[0]

        print(f"[{key}] evaluating...")
        if noise == "clean":
            rows = []
            for rel in CLEAN_DIRS:
                mdir = os.path.join(root, rel)
                rows.extend(eval_manifest_dir(asr, mdir))
        else:
            mdir = condition_dir(root, noise, snr)
            if not os.path.exists(os.path.join(mdir, "manifest.json")):
                print(f"  WARNING: {mdir} missing manifest.json, skipping "
                      f"(run scripts/make_noisyset.py first)")
                continue
            rows = eval_manifest_dir(asr, mdir)

        cache[key] = rows
        save_cache(root, cache)
        print(f"[{key}] done, {len(rows)} rows cached")


def aggregate(rows):
    lid_ok = sum(1 for r in rows if r["detected"] == r["lang"])
    err = sum(r["err"] * r["dur"] for r in rows) / sum(r["dur"] for r in rows)
    rtf = sum(r["rtf"] for r in rows) / len(rows)
    return {"clips": len(rows), "lid_acc": lid_ok / len(rows), "err": err, "rtf": rtf}


def write_report(root: str, cache: dict):
    langs = ("ja", "en", "zh", "ko", "yue")

    clean_by_lang = {}
    if "clean" in cache:
        for lang in langs:
            sub = [r for r in cache["clean"] if r["lang"] == lang]
            if sub:
                clean_by_lang[lang] = aggregate(sub)

    lines = ["# Noise Robustness Scorecard\n",
             "本番経路 (LID→ルーティング→デコード→ja句読点) を、クリーン音声と "
             "white/pink/babble ノイズ (SNR 20/10/5/0dB) を重ねた音声の両方で評価。"
             "metricは en=WER, 他=CER" + ("（yueはt2s正規化）" if _t2s else "（yueは生CER: opencc無し）") + "。\n",
             "## ノイズ生成条件\n",
             f"- SNRは対象音声のRMSパワー基準: `10*log10(P_speech/P_noise) == SNR_dB`\n"
             f"- noise種別: white（ホワイトノイズ）, pink（1/fピンクノイズ）, "
             f"babble（同一プールの別言語クリップを2〜3本ランダムに重ねた話し声ノイズ、"
             f"対象と同言語は使用しない）\n"
             f"- SNRレベル: {', '.join(str(s) for s in SNR_LEVELS_DB)} dB\n"
             f"- 乱数seed: {SEED}（固定・再現可能、`scripts/make_noisyset.py` 参照）\n"
             f"- クリッピング防止: 混合波形のピークが1.0を超える場合は全体をスケールダウン\n"
             f"- 生成スクリプト: `scripts/make_noisyset.py` / 評価スクリプト: `scripts/eval_noise.py`\n",
             "## 結果\n",
             "| lang | condition | clips | LID正解率 | mean err | Δerr vs clean | mean RTF |",
             "|---|---|---|---|---|---|---|"]

    conditions = [("clean", None)] + [(n, s) for n in NOISE_TYPES for s in SNR_LEVELS_DB]
    for lang in langs:
        for noise, snr in conditions:
            key = condition_key(noise, snr)
            if key not in cache:
                continue
            sub = [r for r in cache[key] if r["lang"] == lang]
            if not sub:
                continue
            agg = aggregate(sub)
            cond_label = "clean" if noise == "clean" else f"{noise} snr={snr}dB"
            delta = ""
            if lang in clean_by_lang and noise != "clean":
                delta = f"{agg['err'] - clean_by_lang[lang]['err']:+.3f}"
            lines.append(
                f"| {lang} | {cond_label} | {agg['clips']} | "
                f"{int(round(agg['lid_acc'] * agg['clips']))}/{agg['clips']} | "
                f"{agg['err']:.3f} | {delta} | {agg['rtf']:.3f} |"
            )

    os.makedirs(os.path.dirname(DOCS_PATH), exist_ok=True)
    with open(DOCS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nWrote {DOCS_PATH}")


def parse_conditions(noise_filter, snr_filter):
    noises = noise_filter if noise_filter else ["clean"] + NOISE_TYPES
    snrs = snr_filter if snr_filter else SNR_LEVELS_DB
    out = []
    if "clean" in noises:
        out.append(("clean", None))
    for n in noises:
        if n == "clean":
            continue
        for s in snrs:
            out.append((n, s))
    return out


def main():
    ap = argparse.ArgumentParser()
    default_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--root", default=default_root,
                     help="repo root containing testdata/ (default: derived from script location)")
    ap.add_argument("--noise", nargs="*", choices=["clean"] + NOISE_TYPES,
                     help="restrict to these noise types (default: all incl. clean)")
    ap.add_argument("--snr", nargs="*", type=int, choices=SNR_LEVELS_DB,
                     help="restrict to these SNR levels in dB (default: all)")
    ap.add_argument("--report", action="store_true",
                     help="only (re)write docs/NOISE.md from the existing cache, no evaluation")
    args = ap.parse_args()

    cache = load_cache(args.root)

    if not args.report:
        conditions = parse_conditions(args.noise, args.snr)
        asr_holder = [None]
        run_conditions(args.root, conditions, cache, asr_holder)

    write_report(args.root, cache)


if __name__ == "__main__":
    main()
