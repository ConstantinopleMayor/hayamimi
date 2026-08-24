"""Generate a small TTS test set (Japanese + English) into testdata/eval/.

Usage:
    python scripts/make_testset.py

Produces testdata/eval/*.wav (16kHz mono 16-bit PCM) and testdata/eval/manifest.json.
Idempotent: re-running regenerates everything from scratch.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8")

import edge_tts

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "testdata", "eval")

# (id, lang, voice, text)
ITEMS = [
    # --- Japanese (8) ---
    ("ja_01", "ja", "ja-JP-NanamiNeural",
     "来週の火曜日に開催される定例会議の資料を、明日の夕方までに共有フォルダへアップロードしておいてください。"),
    ("ja_02", "ja", "ja-JP-KeitaNeural",
     "昨日の夜、友達と久しぶりに近所の居酒屋で飲んだんだけど、すごく楽しかったよ。"),
    ("ja_03", "ja", "ja-JP-NanamiNeural",
     "本日の売上は合計で三百四十二万円となり、先月の同じ日と比べて約十五パーセント増加しました。"),
    ("ja_04", "ja", "ja-JP-KeitaNeural",
     "次の打ち合わせは二千二十六年九月三日の午後三時から、四階の会議室で行う予定です。"),
    ("ja_05", "ja", "ja-JP-NanamiNeural",
     "このアプリケーションはクラウド上のサーバーレス関数を利用して、大量のデータをリアルタイムで処理しています。"),
    ("ja_06", "ja", "ja-JP-KeitaNeural",
     "駅前にできた新しいカフェのラテがすごく美味しくて、もう三回も通っちゃった。"),
    ("ja_07", "ja", "ja-JP-NanamiNeural",
     "契約書の第三条には、解約する場合は少なくとも三十日前までに書面で通知することと明記されています。"),
    ("ja_08", "ja", "ja-JP-KeitaNeural",
     "新しく導入したデータベースのインデックス設計のおかげで、検索速度が約五倍になりました。"),

    # --- English (8) ---
    ("en_01", "en", "en-US-JennyNeural",
     "Could you please send me the quarterly budget report before the end of business tomorrow?"),
    ("en_02", "en", "en-US-GuyNeural",
     "Hey, are we still on for coffee this weekend, or did something come up on your end?"),
    ("en_03", "en", "en-US-JennyNeural",
     "Total revenue for the third quarter reached four point six million dollars, up twelve percent from last year."),
    ("en_04", "en", "en-US-GuyNeural",
     "The project deadline has been moved to September twenty third, so we need to finalize the design by next Friday."),
    ("en_05", "en", "en-US-JennyNeural",
     "The new microservice architecture uses containerized deployments and an event driven message queue for scalability."),
    ("en_06", "en", "en-US-GuyNeural",
     "I tried that new ramen place downtown last night, and honestly the broth was even better than I expected."),
    ("en_07", "en", "en-US-JennyNeural",
     "Please make sure the invoice number twelve thousand and forty five is paid within thirty days of receipt."),
    ("en_08", "en", "en-US-GuyNeural",
     "We upgraded the database indexing strategy, and query latency dropped from two hundred milliseconds to about forty."),
]


async def synth(text: str, voice: str, mp3_path: str):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(mp3_path)


def mp3_to_wav(mp3_path: str, wav_path: str):
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", mp3_path,
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            wav_path,
        ],
        check=True,
    )


def main():
    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR, exist_ok=True)

    manifest = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        for item_id, lang, voice, text in ITEMS:
            mp3_path = os.path.join(tmp_dir, f"{item_id}.mp3")
            wav_name = f"{item_id}.wav"
            wav_path = os.path.join(OUT_DIR, wav_name)

            print(f"synthesizing {item_id} ({lang}, {voice}) ...")
            asyncio.run(synth(text, voice, mp3_path))
            mp3_to_wav(mp3_path, wav_path)

            manifest.append({"wav": wav_name, "lang": lang, "ref": text})
            print(f"  -> {wav_path}")

    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\nWrote {len(manifest)} entries to {manifest_path}")


if __name__ == "__main__":
    main()
