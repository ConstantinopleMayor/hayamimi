# Whisper-faster

CPU軽量・リアルタイム・多言語（日英中心）の音声認識パイプライン。

Whisperの高速化調査から出発し、実測比較の結果、CPUリアルタイム要件では
Parakeet（NVIDIA, TDT/CTC系）への乗り換えが唯一の現実解と判断（実測40〜65倍差）。
Whisper large-v3-turbo は精度基準（リファレンス）として保持する。

## 構成（5層モデルカタログ + 二段パス）

| ルート | モデル | 根拠（実音声CER/WER） |
|---|---|---|
| 日本語 | ReazonSpeech k2 Zipformer INT8 | CER 8.6%（whisper-turbo 13.8%を上回る）RTF 0.02 |
| 中国語 | Paraformer-zh INT8 | CER 5.6% |
| 韓国語・広東語 | SenseVoice small INT8 | ko 9.3% / yue 6.0% |
| 英語+欧州24言語 | Parakeet TDT 0.6B v3 INT8 | en WER 2.5%（大文字小文字・句読点付き） |
| その他 約1600言語 | Meta Omnilingual ASR 300M CTC INT8 | フォールバック、RTF 0.086 |

- **パイプライン**: Silero VAD（0.35s終端検出+0.8sプリロール）→ 発話中に早期言語判定+モデル先読み → 確定デコード
- **速報字幕**: 発話中0.5秒ごとにドラフト表示、発話終了から約0.1秒で確定（日本語は句読点BERT付き）
- **二段パス**: 2秒の無音で発話群を一括再デコードし高精度版を出力（実放送jaでCER 15.5%→12.0%）
- **メモリ**: LRUアンロールで約2GB以内。モデルは遅延ロード
- ランタイム: sherpa-onnx (ONNX Runtime, CPU INT8) のみ。GPU・PyTorch不要

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

# OBS字幕オーバーレイ付き（OBSのブラウザソースに http://localhost:8765 を追加）
# トランスクリプト一覧は http://localhost:8765/transcript
.venv/Scripts/python scripts/realtime_transcribe.py --serve

# 高精度トランスクリプトをファイルに保存
.venv/Scripts/python scripts/realtime_transcribe.py --transcript out.txt
```
