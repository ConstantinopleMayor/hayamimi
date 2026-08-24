# 調査サマリー: Whisper高速化プロジェクト (2026-08-23)

3本の調査レポートの統合サマリー。詳細は各ファイルを参照。

- [01-whisper-ecosystem.md](01-whisper-ecosystem.md) — Whisper本体と派生実装のエコシステム
- [02-optimization-techniques.md](02-optimization-techniques.md) — 高速化手法の網羅調査とパレートフロンティア
- [03-competitor-asr.md](03-competitor-asr.md) — Whisper以外のASRモデルと速度目標の相場観

## ローカル環境

- GPU: NVIDIA GeForce RTX 3080 Ti (12GB VRAM)
- CPU: AMD Ryzen 5 5600 (6C12T)
- RAM: 32GB / Python 3.11.9 / Windows 11

## 主要な結論

### 1. 高速化は3系統の「掛け算」

| 系統 | 代表手法 | 効果 |
|---|---|---|
| モデルを小さくする | 蒸留 (large-v3-turbo / distil-whisper / kotoba-whisper)、量子化 (INT8/INT4) | 蒸留で約6倍、量子化で約2倍 |
| 無駄な計算を省く | VADセグメント化 + バッチ推論 (WhisperX方式) | 最大約12倍（精度劣化なし） |
| 計算自体を速くする | CTranslate2 / FlashAttention-2 / torch.compile / TensorRT | 2〜5倍 |

### 2. 現在の定番最速構成 (NVIDIA GPU)

faster-whisper (CTranslate2, FP16/INT8) + VADバッチ化 + large-v3-turbo または distil 系。
RTX 4090級で「Whisperのまま」の到達点は RTFx 20〜40程度（バッチ化込みで実効さらに上）。

### 3. アーキテクチャの壁

Whisperの自己回帰デコーダが本質的ボトルネック。CTC/TDT系 (NVIDIA Parakeet) は
RTFx 2000〜3400 と桁違いに速く、WERも6%台前半で十分競争力がある。
「Whisper系のまま最適化」と「アーキテクチャ乗り換え」では到達可能な速度が1〜2桁違う。

### 4. まだ主流実装に統合されていない伸びしろ（新規性の候補）

- **Moonshine式の可変長入力**: Whisperの30秒固定パディングを撤廃。短い発話で最大5倍、理論上限35倍
- **Whisper-Medusa (投機的デコード)**: +1.5倍。CTranslate2/whisper.cppには未統合
- **distil-whisperをドラフトとする speculative decoding**: 出力同一性を保証しつつ2倍
- **ASKD-Whisper (2026)**: 自己知識蒸留で5倍速化かつ教師超えWER
- これらの組み合わせ（例: VADバッチ + turbo + Medusaヘッド + INT8）は前例が少ない

### 5. 日本語対応

kotoba-whisper (ReazonSpeech学習、6.3倍高速) が日本語特化蒸留の代表。
日本語ベンチマークでは Qwen3-ASR-1.7B / whisper-large-v3-turbo / reazonspeech-espnet-v2 が上位。

## 未決定事項（ユーザーと議論）

1. 主用途: バッチ文字起こし / リアルタイム / API サーバー
2. 対象言語: 日本語重視か英語か多言語か
3. 精度の許容劣化幅
4. 方向性: Whisper系のまま最適化 vs CTC/TDT系への乗り換えも視野
