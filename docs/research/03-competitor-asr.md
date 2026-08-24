# 非Whisper系ASRモデルの速度・精度リサーチ（2026年8月時点）

## 1. オープンモデル一覧

| モデル | 開発元 | アーキテクチャ | 特徴 |
|---|---|---|---|
| **Canary-Qwen-2.5B** | NVIDIA | SALM（FastConformerエンコーダ+LLMデコーダ） | Open ASR Leaderboardで長らくトップ級（WER 5.63%）。精度最優先、速度は普通 |
| **Canary-1B-v2** | NVIDIA | Encoder-Decoder (FastConformer) | 多言語ASR+翻訳(AST)対応 |
| **Parakeet TDT/CTC/RNN-T (0.6B v2/v3, 1.1B)** | NVIDIA | FastConformer + TDT/CTC/RNN-T | 速度最優先。v2は英語特化でRTFx ~3380（バッチ128）。v3は多言語（25言語）でRTFx 3332.74、WER 6.32% |
| **NeMo FastConformer** | NVIDIA | Conformer変種（CTC/RNN-T兼用） | Parakeet/Canaryの土台エンコーダ |
| **Meta MMS / SeamlessM4T / wav2vec2** | Meta | wav2vec2系self-supervised | 1000言語超対応（MMS）。精度は専用モデルに劣るが言語カバレッジが強み |
| **Moonshine (Tiny/Base) / v2** | Useful Sensors | 軽量Encoder-Decoder | エッジ/CPU特化。WhisperTinyと同等WERで計算量5分の1、5〜15倍高速、レイテンシ200ms以下 |
| **Kyutai STT (stt-1b, stt-2.6b)** | Kyutai | Mimiコーデック+ストリーミングTransformer | 2025年6月OSS化。ストリーミング特化 |
| **Reverb ASR** | Rev.com | WeNet (Conformer) | 20万時間の人手書き起こしで学習 |
| **Distil-Whisper (large-v3, v3.5)** | HF/Whisper派生 | 蒸留Encoder-Decoder | large-v3比で約6.3倍高速、WER差1%以内（長尺） |
| **Qwen3-ASR (1.7B/0.6B)** | Alibaba | Qwen3-Omniベースの音声LLM | 52言語対応、オープンモデル中SOTA級。Wenetspeechで4.62%WER |
| **Qwen2.5-Omni / Qwen3.5-Omni** | Alibaba | マルチモーダルLLM | Qwen3.5-Omni-PlusはFLEURSで平均WER 6.6% |
| **Voxtral Mini/Small, Transcribe 2, Realtime** | Mistral | 音声エンコーダ+LLM | FLEURSでWhisper超（5.9% vs 7.4%）。Realtimeは480ms/960ms遅延ストリーミング |
| **Granite Speech (IBM)** | IBM | 音声対応LLM (Apache 2.0) | Granite Speech 4.1 2Bが平均WER 5.33%で上位 |

補足: Cohere Transcribe（2026年3月, WER 5.42%）もリーダーボード上位。

## 2. 日本語ASR

- **ReazonSpeech (reazon-research)**: 日本のテレビ音声から720万クリップの日本語コーパスを構築。k2/ESPnet系モデル（reazonspeech-espnet-v2など）は放送・メディア用途で強い。
- **kotoba-whisper (v1.0/v2.0)**: ReazonSpeech全サブセットで学習したWhisper蒸留モデル。large-v3比6.3倍高速で、in-domainではlarge-v3を上回るCER/WER。
- **Nue-ASR**: 定量データは今回の調査では確認できず。
- 2026年のベンチマーク記事（Neosophie, 2026-02）では「精度最優先ならQwen3-ASR-1.7BかWhisper-large-v3-turbo、放送/メディア系ならreazonspeech-espnet-v2」という住み分け。

Source: [Neosophie Japanese ASR benchmark](https://neosophie.com/en/blog/20260226-japanese-asr-benchmark), [kotoba-whisper-v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)

## 3. ベンチマーク（Open ASR Leaderboard）

**WERトップ級（英語、2026年時点）**
- Canary-Qwen-2.5B: 5.63% / Cohere Transcribe: 5.42% / IBM Granite Speech 4.1 2B: 5.33%

**RTFxトップ級（速度、高いほど良い）**
- Parakeet TDT 0.6B v2: RTFx ~3380（バッチ128）
- Parakeet TDT 0.6B v3（25言語）: RTFx 3332.74、WER 6.32%
- Parakeet 1.1B: RTFx 2000超
- Whisper large-v3: RTFxは概ね数十〜100程度のオーダー（TDT/CTC系とは一桁以上の差）
- Distil-Whisper large-v3: large-v3比6.3倍高速、WER差1%以内

Source: [Open ASR Leaderboard blog](https://huggingface.co/blog/open-asr-leaderboard), [open_asr_leaderboard (GitHub)](https://github.com/huggingface/open_asr_leaderboard)

## 4. 商用API（参考値）

| サービス | レイテンシ | 価格目安 |
|---|---|---|
| Deepgram Nova-3 | ストリーミング200〜300ms | バッチ $0.0043/分 |
| AssemblyAI Universal-2/3 Pro | P50 ~150ms（VAD検出後） | バッチ最安$0.0025/分 |
| ElevenLabs Scribe v2 / Realtime | Realtimeで150ms未満 | バッチ $0.22/時間 |
| OpenAI gpt-4o-transcribe | - | $2.50/M入力トークン、目安$0.006/分 |
| Google Cloud STT (Chirp 2) | - | $0.016/分 |
| Azure AI Speech | - | $0.017/分程度 |

## 5. アーキテクチャ比較：なぜCTC/TDTはWhisperより10〜30倍速いのか

- **Whisper（attention encoder-decoder）**: デコーダが自己回帰（1トークンずつ逐次生成）。並列化できず本質的に遅い。精度は高い。
- **CTC**: デコーダなしで全フレームを並列出力。理論上最速（Conformer-CTCはattention decoderの3〜4倍速）。言語モデル的補正がない分、精度はやや劣る傾向。
- **RNN-T/Transducer**: フレーム×ラベルの格子を逐次的に辿るためCTCより遅い。
- **TDT（Token-and-Duration Transducer, NVIDIA）**: RNN-Tを改良し1ステップで複数フレームをスキップ。RNN-Tの精度を保ちながらCTCに近い速度。Parakeet TDTが代表例。
- **音声LLM（Canary-Qwen, Qwen3-ASR, Voxtral）**: 精度は最高水準だが自己回帰のため速度はCTC/TDTに劣る。

実測: Parakeet CTCが1時間で1336時間分、TDTが1212時間分、RNN-Tが1120時間分の音声処理。

## 6. Whisperを最適化する人が狙うべき現実的な速度目標

**単一GPU (RTX 4090級):**
- Whisper large-v3をfaster-whisper等で最適化した場合、RTF 0.03〜0.08程度（RTFx 12〜30倍）が現実的な下限。
- 「Whisperのまま」の最適化ゴールはRTFx 20〜40程度。「アーキテクチャを変える」なら数百〜数千レンジ（Parakeet系: RTFx 2000〜3400）。

**CPU:**
- Parakeet TDT v3はi7-12700KF級CPUでRTF 0.033（約30倍速）。
- Whisper large系のCPU実行は基本的に非現実的（small/base/Moonshine等でないと実用速度に届かない）。

**収穫逓減点:**
- RTFxが数百を超えるとI/O・バッチング・前処理（VAD、リサンプリング）がボトルネック化。
- WERは5.3〜6.5%帯で0.5〜1ポイントを追うコストが急増。実務ではドメイン適合（専門用語・ノイズ耐性・話者分離）の方が効果が大きい。
- 投資対効果は「Whisperの延長で微調整」<「TDT/CTC系への切り替え」。

（詳細な出典URL一覧は調査エージェントの報告に基づく。主要リンクは本文中に記載）
