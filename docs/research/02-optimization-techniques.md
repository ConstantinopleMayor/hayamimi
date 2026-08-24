# Whisper系音声認識の高速化技術サーベイ (2026年8月時点)

## 1. 量子化 (Quantization)

| 手法 | 仕組み | 典型速度向上 | 精度コスト | 実装難度 | 主な採用プロジェクト |
|---|---|---|---|---|---|
| INT8 (PTQ) | 重み・活性化を8bit整数化。CTranslate2は独自カーネルでMKL/cuBLASに最適化 | GPU/CPUともに~2倍 (FP16比)、VRAM ~40%減 | WERへの影響は概ね0.1%未満(絶対値) | 低(既存ツールでフラグ一つ) | faster-whisper (CTranslate2)、ONNX Runtime量子化 |
| GGML k-quants (q4_0/q5_0/q5_1/q8_0) | ブロック単位混合精度量子化。CPU SIMD/Metal/CUDAバックエンド対応 | tiny: 1.5倍、medium: 2.4倍、large-v3: 2.2〜2.4倍(INT4)。large-v3 FP16はRTF1.26で実時間未達だがINT4は8スレッドでRTF0.91まで改善 | q5_1でWER低下1%未満、q4_0で2〜4%程度 | 低〜中(量子化コンバータ付属) | whisper.cpp / ggml |
| FP16/BF16 | 半精度化のみ、量子化誤差なし | FP32比 概ね2倍前後 | ほぼ無視できる | 最低 | ほぼ全ランタイムの標準 |
| GPTQ/AWQ的手法 | Whisper自体への直接適用例はLLMほど一般的ではない。"Quantizing Whisper" (2025) が設計選択の影響を系統的に検証 | モデル依存 | 手法・キャリブレーションデータ依存 | 中〜高 | 研究段階が中心 |

出典: [Quantizing Whisper: How Design Choices Affect ASR Performance](https://arxiv.org/html/2511.08093v2), [faster-whisper](https://github.com/AIXerum/faster-whisper), [whisper.cpp ベンチマーク議論](https://github.com/ggml-org/whisper.cpp/discussions/3752)

## 2. 蒸留 (Distillation)

- **distil-whisper**: デコーダを32層→2層に削減。large-v3比 **6.3倍高速**・50%小型化・WER劣化1%未満(短形式9.7% vs 8.4%、長形式チャンク方式では実質同等)。([huggingface/distil-whisper](https://github.com/huggingface/distil-whisper))
- **Whisper large-v3-turbo**: デコーダ32層→4層(蒸留ではなく再学習アプローチ)。**約6倍高速**、WER劣化は+0.3%程度。([解説](https://medium.com/@bnjmn_marie/whisper-large-v3-turbo-as-good-as-large-v2-but-6x-faster-97f0803fa933), [HF model card](https://huggingface.co/openai/whisper-large-v3-turbo))
- **kotoba-whisper (日本語特化)**: large-v3のエンコーダをフルに残し、デコーダを2層に圧縮。パラメータ数756M(large-v3の1550Mから半減)、**6.3倍高速**、ReazonSpeechデータで学習。v1.0/v2.0/bilingual版が存在。([kotoba-whisper-v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0))
- **ASKD-Whisper (2026, Adaptive Self-Knowledge Distillation)**: 5倍速化しつつ教師モデルよりWERが1.07%低いという逆転結果を報告。([arXiv 2601.19919](https://arxiv.org/abs/2601.19919))
- **LiteASR**: 低ランク近似によるエンコーダ圧縮アプローチ(2025)。([arXiv 2502.20583](https://arxiv.org/pdf/2502.20583))

## 3. デコーディング最適化

- **Greedy vs Beam**: greedyは最速だが誤り率がやや高い。beam search(通常5)は精度優先で遅い。実運用ではgreedy+温度フォールバックが速度重視の主流。
- **Speculative Decoding — Whisper-Medusa**: 単一デコーダの最終層出力を複数の"Medusaヘッド"(残差付き線形層、語彙射影は共有)に通し、1ステップでK+1トークンを予測。**1.5倍高速化**(WER 4.1% vs baseline 4.0%とほぼ同等)。([論文](https://arxiv.org/pdf/2409.15869), [aiOla blog](https://aiola.ai/blog/medusa-architect/))
- **バッチ化beam search**: WhisperX/WhisperS2Tなど一部ランタイムのみCTranslate2上でバッチ処理をサポート。

## 4. バッチ処理・パイプライン

- **OpenAIオリジナルのsliding window長形式処理**: 30秒チャンクを逐次処理、境界での誤り蓄積が課題。
- **WhisperX**: VAD(pyannote/wav2vec2など)でセグメント化してから各セグメントを独立にバッチ処理。VAD前処理+バッチ化で**約12倍**の速度向上を無精度損失で達成。VADなしのバッチ化は境界効果で精度劣化。([WhisperX論文](https://arxiv.org/pdf/2303.00747), [ベンチマーク議論](https://github.com/m-bain/whisperX/discussions/1352))
- **faster-whisperバッチ**: GPU fp16、batch_size=8で13分の音声を17秒で処理(オリジナル実装は2分23秒)。
- **動的バッチング**: サービング層(VoxServe等)でストリーミング対応のスケジューリングを実装。

## 5. カーネル・ランタイム最適化

- **FlashAttention-2**: メモリ効率と計算効率を両立するアテンションカーネル。バッチサイズ依存で高速化効果あり。
- **PagedAttention (vLLM)**: KVキャッシュを固定サイズブロック管理し断片化を削減。ただし2025年の研究では**vLLMのPagedAttentionカーネルがFlashAttention-2比で最大2.8倍遅い**ケースも報告。([論文](https://arxiv.org/pdf/2405.04437))
- **HFのvLLMベースWhisperエンドポイント**: compute capability 8.9以上のNVIDIA GPUでtorch.compile+CUDAグラフを有効化。([HF blog](https://huggingface.co/blog/fast-whisper-endpoints))
- **torch.compile**: TorchDynamo+TorchInductorで最大**30%程度**の推論高速化。static KVキャッシュのin-place更新がCUDAグラフキャプチャを阻害する既知の回帰問題あり(transformers v4.52+)。([Issue #40682](https://github.com/huggingface/transformers/issues/40682))
- **CTranslate2**: 融合アテンション演算子でデコードステップごとのメモリ確保オーバーヘッドを削減、MKL/cuBLAS最適化。faster-whisperの中核。
- **GGML SIMD/Metal/CUDA**: whisper.cppはAVX(x86)、NEON(ARM)、Metal/Core ML(Apple)、CUDA/ROCm/Vulkan/OpenVINOまで広くバックエンド対応。

## 6. VAD前処理・無音スキップ

- Silero-VADやpyannote等で発話区間のみ抽出し、無音・非音声区間の計算を省略。WhisperXのCut&Merge処理はWERと単語セグメンテーション精度も同時に改善。CPUでは特にバッチパディング削減効果が大きい。

## 7. アーキテクチャレベル

- **Moonshine**: RoPEベースの可変長エンコーダで、Whisperの固定30秒ゼロパディングを撤廃。10秒音声で**最大5倍**高速、理論上限では**35倍**の可能性も言及。エッジデバイス向け。([arXiv 2410.15608](https://arxiv.org/html/2410.15608v1))
- **WhisperRT**: Whisperを因果的(causal)ストリーミングモデルに転換する試み(2025)。([arXiv 2508.12301](https://arxiv.org/abs/2508.12301))
- **MURMUR**: 長形式ASR向け効率的推論システム(2026)。([arXiv 2606.01483](https://arxiv.org/pdf/2606.01483))
- ストリーミング(低遅延・逐次)とオフライン(高精度・バッチ)はトレードオフ関係にあり、用途で使い分けが必要。

## 8. キャッシング

- **KVキャッシュ**: デコーダの自己注意で標準的に利用。CTranslate2/whisper.cpp/HF transformersいずれも実装。
- **エンコーダ出力の再利用**: 同一音声に対する複数デコード試行(温度フォールバック等)でエンコーダ計算を再利用。
- **クロスアテンションキャッシュ**: デコーダの各ステップでエンコーダ出力に対するK/Vを再計算せず保持。

## 9. 2025-2026年の新規動向

- **ASKD-Whisper** (2026): 自己知識蒸留で5倍速化+教師超えのWER。([arXiv](https://arxiv.org/abs/2601.19919))
- **Quantizing Whisper** (2025): 量子化設計選択の系統的検証。([arXiv](https://arxiv.org/html/2511.08093v2))
- **Energy-Efficient Hardware Acceleration on CGLA** (2025): 専用ASIC設計でJetson AGX Orin比1.90倍、RTX 4090比9.83倍のエネルギー効率。([arXiv 2511.02269](https://arxiv.org/html/2511.02269v1))
- **Training and Inference Efficiency of Encoder-Decoder Speech Models** (2025): 蒸留・低ランク近似・量子化・KVキャッシュ構造変更を横断的に整理。([arXiv 2503.05931](https://arxiv.org/pdf/2503.05931))
- **lightning-whisper-mlx**: whisper.cpp比10倍、標準MLX Whisper比4倍を主張(Apple Silicon)。([GitHub](https://github.com/mustafaaljadery/lightning-whisper-mlx))

---

## パレートフロンティア: プラットフォーム別最適構成 (2026年8月時点)

### (a) NVIDIA GPU
**最有力**: faster-whisper (CTranslate2, FP16/INT8) + WhisperXのVADバッチ化 + distil-large-v3 or large-v3-turbo
- RTX 4070でlarge-v3が約12倍リアルタイム。バッチ化+VADでさらに最大12倍の追加効果を得られるケースも。
- 精度を落とせないユースケースはlarge-v3-turbo(WER+0.3%で6倍速)、スループット最優先ならdistil-whisper系。
- Compute Capability 8.9+ GPUではvLLMベースのHFエンドポイント(torch.compile+CUDAグラフ)も選択肢だが、PagedAttentionは万能ではない。
- 発展形としてWhisper-Medusa(投機的デコード、+1.5倍)を組み合わせる余地あり。

### (b) CPU専用
**最有力**: whisper.cpp (GGML, q5_0/q5_1量子化) + VAD前処理
- INT4/INT5量子化でlarge-v3を実時間近傍(RTF~0.5〜0.9)まで持っていける唯一の現実的経路。
- CTranslate2のINT8もCPUで有効。
- 精度要求が緩ければkotoba-whisper系やdistil-whisperのCPU実行も検討価値あり。

### (c) Apple Silicon
**最有力**: whisper.cpp + Metal/Core ML (エンコーダをANEへオフロード)、またはMLX系実装
- whisper.cppのMetal実行はlarge-v3で約10倍リアルタイム、Core MLエンコーダオフロードでCPU比3倍以上。
- MLX Whisperがwhisper.cpp比2倍、lightning-whisper-mlxはさらに4〜10倍を主張(要検証)。
- faster-whisper(CTranslate2)はMetalバックエンドがなくMac上ではCPU実行のみになるため非推奨。

（詳細な出典URL一覧は調査エージェントの報告に基づく。主要リンクは本文中に記載）
