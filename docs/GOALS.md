# プロジェクト目標 (2026-08-23 ユーザー合意ベース)

## 決定事項

| 項目 | 決定 |
|---|---|
| 主用途 | リアルタイム認識（マイク入力を低遅延で逐次認識） |
| 対象言語 | 多言語 — 日英中心+主要言語で十分。**注意: Parakeet TDT v3 の25言語は欧州言語のみで日中韓は非対応と実測で判明（2026-08-23）**。日本語は Parakeet tdt_ctc-0.6b-ja（ReazonSpeech学習）を言語ルーティングで併用する構成を検証中 |
| 精度 | 劣化ほぼゼロ（Whisper large-v3-turbo 同等以上を基準） |
| 手段 | アーキテクチャ乗り換え含め自由 |
| **実行環境** | **軽量・CPU優先**。CPUで高速に動くならそれを最優先。GPUはオプション扱い |

## ターゲット環境

Ryzen 5 5600 (6C12T) / RAM 32GB / Windows 11（GPU: RTX 3080 Ti はオプション加速用）

## 数値目標（CPU基準・案)

| 指標 | 目標 | 根拠 |
|---|---|---|
| 部分認識レイテンシ (partial) | **< 500ms** | 商用ストリーミングAPI相場 (Deepgram 200-300ms) を意識 |
| 確定認識レイテンシ (final) | 発話終了後 **< 1s** | VAD終端検出 + 最終デコードの合計 |
| RTF (CPU) | **< 0.2**（5倍速以上） | Parakeet TDT v3 は同級CPUで RTF 0.033 の実績があり十分現実的 |
| 精度 | large-v3-turbo 比 WER/CER **+0.5%以内** | 「劣化ほぼゼロ」の運用定義 |
| メモリ | モデル+ランタイムで **< 2GB** | 軽量前提。INT8量子化で達成可能な水準 |

## 候補アプローチ（CPU優先で改訂、優先順）

1. **Parakeet TDT 0.6B v3 (25言語) + sherpa-onnx / onnxruntime INT8**
   - CPUでRTF 0.033の実績。WER 6.32%で精度も十分。日本語含む25言語。最有力
   - 課題: 25言語で足りるかの確認、日本語CERの実測、ストリーミング対応の実装形態
2. **NeMo cache-aware streaming FastConformer** — ネイティブストリーミング設計。言語カバレッジ要確認
3. **whisper.cpp + large-v3-turbo INT8/INT5 + VAD** — 99言語で精度基準そのもの。ただしCPUではRTF 0.5〜1が限界で、リアルタイムはギリギリ。two-passの確定側として活用
4. **ハイブリッド (two-pass)**: 軽量ストリーミングモデル（Parakeet系）で部分認識 + turbo量子化版で確定認識を後追い再デコード
   - 部分は速く、確定は正確に。精度劣化ゼロ要件とCPU軽量要件を両立できる本命構成

## 次フェーズ

1. ベースライン計測環境の構築（RTF / レイテンシ / WER・CER計測スクリプト、日英テスト音声）
2. CPU実測比較: Parakeet TDT v3 (sherpa-onnx INT8) vs whisper.cpp turbo量子化 vs faster-whisper turbo INT8
3. 最有力構成の選定 → ストリーミング層の実装 → 深掘り最適化（量子化・VADチューニング・two-pass）
