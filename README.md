# Whisper-faster

CPU軽量・リアルタイム・多言語（日英中心）の音声認識パイプライン。

Whisperの高速化調査から出発し、実測比較の結果、CPUリアルタイム要件では
Parakeet（NVIDIA, TDT/CTC系）への乗り換えが唯一の現実解と判断（実測40〜65倍差）。
Whisper large-v3-turbo は精度基準（リファレンス）として保持する。

## 構成

- **多言語認識**: Parakeet TDT 0.6B v3 INT8（欧州25言語） — CPU RTF 0.062
- **日本語認識**: Parakeet tdt_ctc 0.6B ja INT8（ReazonSpeech学習） — CPU RTF 0.048
- **言語ルーティング**: whisper-tiny によるSpoken Language ID → ja / v3 に振り分け
- **VAD**: Silero VAD（発話区間のみデコード）
- ランタイム: sherpa-onnx (ONNX Runtime, CPU INT8)

## ディレクトリ

- `docs/GOALS.md` — 目標と決定事項
- `docs/BENCHMARKS.md` — 実測記録
- `docs/research/` — 調査レポート（Whisperエコシステム / 高速化手法 / 競合ASR）
- `scripts/bench_offline.py` — オフラインRTFベンチマーク
- `scripts/realtime_transcribe.py` — リアルタイム認識（マイク / wavシミュレーション）
- `scripts/eval_accuracy.py` — 精度評価（vs faster-whisper large-v3-turbo）
- `models/` — ONNXモデル（git管理外、下記から取得）

## セットアップ

```bash
python -m venv .venv
.venv/Scripts/pip install sherpa-onnx soundfile numpy sounddevice faster-whisper jiwer edge-tts
```

モデルは [sherpa-onnx asr-models リリース](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models) から:
`sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8` / `sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8` /
`silero_vad.onnx` / `sherpa-onnx-whisper-tiny` を `models/` に展開。

## 使い方

```bash
# オフラインベンチ
.venv/Scripts/python scripts/bench_offline.py --model-dir models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8

# リアルタイム（マイク）
.venv/Scripts/python scripts/realtime_transcribe.py

# リアルタイム（wavシミュレーション）
.venv/Scripts/python scripts/realtime_transcribe.py --wav testdata/ja_test.wav
```
