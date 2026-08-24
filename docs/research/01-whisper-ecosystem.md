# Whisper と派生実装エコシステム調査レポート（2026年8月時点）

## 1. OpenAI公式 Whisper

**アーキテクチャ**
- エンコーダー・デコーダー型 Transformer。音声を30秒窓で切り出し、80チャンネルのlog-melスペクトログラムに変換してエンコーダーに入力する。
- 学習データは68万時間の弱教師あり（インターネット収集）多言語音声。論文: Radford et al. "Robust Speech Recognition via Large-Scale Weak Supervision" (2022)。

**モデルサイズとパラメータ数**

| モデル | パラメータ数 |
|---|---|
| tiny | 39M |
| base | 74M |
| small | 244M |
| medium | 769M |
| large / large-v2 / large-v3 | 1.55B |
| large-v3-turbo | 809M（デコーダー層を32→4層に削減、エンコーダーは無変更） |

large-v3-turbo は2024年10月リリース。large-v3から追加2エポックの微調整（翻訳データは除外、転写専用）で、推論速度は2〜5倍、精度低下はわずか。

**ライセンス**: MITライセンス（コード・重みとも）。

Sources: [Whisper Large V3 Turbo解説](https://medium.com/axinc-ai/whisper-large-v3-turbo-high-accuracy-and-fast-speech-recognition-model-be2f6af77bdc), [turbo model release discussion](https://github.com/openai/whisper/discussions/2363), [Whisper Model Sizes Guide](https://openwhispr.com/blog/whisper-model-sizes-explained), [openai/whisper-large-v3 HF](https://huggingface.co/openai/whisper-large-v3)

---

## 2. コミュニティ／派生実装

### faster-whisper (SYSTRAN)
- **技術**: CTranslate2推論エンジンへの重み変換。レイヤー融合、INT8/FP16量子化、バッチデコードの最適化。
- **速度**: 同一精度でリファレンス実装比 最大約4倍（GPU 2〜4倍、CPUでも約4倍）。VRAM使用量も30〜40%削減。RTX 4070でlarge-v3が約12×リアルタイム（whisper.cppのCUDA版は約8×）。
- **精度**: 同一重みを使うため実質同一WER。
- **対象HW**: CPU/GPU両対応。
- **保守状況**: 活発に開発継続中（2026年時点でも主要選択肢）。

Source: [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper), [faster-whisper vs whisper.cpp比較](https://codersera.com/blog/faster-whisper-vs-whisper-cpp-speech-to-text-2026/)

### whisper.cpp (ggml-org)
- **技術**: C/C++ + GGMLによるゼロ依存実装。AVX/AVX2 + pthreads、OpenBLASやIntel OpenVINOエンコーダーパスにも対応。GGML量子化（q4_0, q5_0など）。
- **速度/精度**: 4bit量子化でlarge-v3が1.5GB→380MBに圧縮、WER上昇は約1%程度。Raspberry Pi 5でtiny/baseがリアルタイム動作。
- **対象HW**: CPU中心（組込み/エッジに強い）、CUDAバックエンドもあり。
- **保守状況**: 活発。組込み・モバイル用途で定番。

Source: [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp), [whisper.cpp量子化ドキュメント](https://deepwiki.com/ggml-org/whisper.cpp/5.2-model-download-and-conversion)

### WhisperX (m-bain)
- **技術**: faster-whisperバックエンドでのバッチ推論＋VADフィルタリング（デフォルトON）＋wav2vec2等によるforced alignmentで単語レベルタイムスタンプ・話者分離を実現。`--without_timestamps True`により1サンプル1フォワードパスのバッチ処理を可能に。
- **速度**: large-v2で60〜70×リアルタイム（バッチ推論込み）。
- **精度**: VADで無音区間を除去し幻覚（hallucination）を抑制、単語タイムスタンプの精度が向上。
- **対象HW**: GPU。
- **保守状況**: 活発。話者分離・アライメント機能が必要な用途で定番。

Source: [m-bain/whisperX](https://github.com/m-bain/whisperx)

### insanely-fast-whisper (Vaibhavs10)
- **技術**: Transformers + Optimum + FlashAttention-2 + BetterTransformer + バッチ処理。
- **速度**: large-v3で2.5時間の音声を約98秒で転写（Colab T4上、fp16+batch24+FlashAttention2）。
- **制約**: NVIDIA GPU/Macのみ対応。FlashAttention-2導入に追加ビルドと十分なVRAMが必要。

Source: [Vaibhavs10/insanely-fast-whisper](https://github.com/Vaibhavs10/insanely-fast-whisper/blob/main/README.md)

### whisper-jax (sanchit-gandhi)
- **技術**: JAX実装＋JITコンパイル＋バッチ処理。CPU/GPU/TPU対応。
- **速度**: PyTorchリファレンス比最大70倍（TPU v4-8使用時）。バッチ処理だけで7倍、JITで対GPU 2倍、TPUはA100比5倍。1時間の音声: OpenAI Whisper(A100)1001秒 → whisper-jax(A100)75.3秒 → whisper-jax(TPU)13.8秒。
- **保守状況**: 開発は比較的停滞気味（2023年が最盛期、TPU入手性の制約もあり主流ではなくなっている）。

Source: [sanchit-gandhi/whisper-jax](https://github.com/sanchit-gandhi/whisper-jax)

### distil-whisper (Hugging Face)
- **技術**: 大規模擬似ラベリングによる知識蒸留。エンコーダーはコピーして凍結、デコーダーは32層→2層（最初と最後の層から初期化）に削減。
- **速度/精度**: 6倍高速、49%小型化、分布外データでWER差1%以内。ハルシネーションにも強い。
- **応用**: 投機的デコーディング（speculative decoding）のドラフトモデルとしてWhisperと組み合わせ、数学的に同一出力を保証しつつ2倍高速化。
- **対象HW**: GPU/CPU両対応。

Source: [huggingface/distil-whisper](https://github.com/huggingface/distil-whisper), [Distil-Whisper論文](https://arxiv.org/abs/2311.00430)

### whisper-medusa (aiOla)
- **技術**: Medusaヘッド方式の投機的デコーディング。最終デコーダー層の出力に複数の「Medusaヘッド」（線形層＋残差接続）を追加し、各ステップでK+1トークンを予測。Medusa-Linear/Medusa-Blockの2種。
- **速度/精度**: 平均1.5倍高速化。Medusa-Blockは同等WER/CERを維持しつつレイテンシ20〜68%削減、Medusa-Linearはより高速（30〜80%削減）だが精度がやや低下。
- **保守状況**: 研究プロジェクト寄り、継続開発はやや限定的。

Source: [aiola-lab/whisper-medusa](https://github.com/aiola-lab/whisper-medusa), [aiOla解説](https://aiola.ai/blog/medusa-architect/)

### whisper-timestamped (Linagora/linto-ai)
- **技術**: クロスアテンション重みへのDynamic Time Warping (DTW)適用で単語レベルタイムスタンプ・信頼度スコアを算出。Silero/auditok等のVADを前処理として選択可能。
- **用途**: 速度よりも精度（タイムスタンプ品質）重視の派生。

Source: [linto-ai/whisper-timestamped](https://github.com/linto-ai/whisper-timestamped)

### sherpa-onnx (k2-fsa)
- **技術**: ONNX Runtimeベースの移植。float32/int8量子化対応。C++コアで多言語バインディング（Python, Go, C#, Swift, Kotlinなど）。
- **対象HW**: 組込み・エッジに強い（Raspberry Pi, Android, iOS, HarmonyOS, RISC-V, RK/Axera/AscendのNPU、Qualcomm NPU(QNN)も2026年に追加）。
- **注意点**: 一部Issueでfaster-whisperよりCER（文字誤り率）が高くなるケースが報告されている＝実装依存の精度差に注意。
- **保守状況**: 活発、対応ハードウェアの拡張が続いている。

Source: [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), [CER比較Issue](https://github.com/k2-fsa/sherpa-onnx/issues/2900)

### Apple Silicon / MLX系（argmaxinc WhisperKit, lightning-whisper-mlx）
- **WhisperKit**: Argmax社によるSwiftネイティブ実装。CoreMLコンパイル済みエンコーダー・デコーダーがNeural Engine/GPU/CPUに自動割当。PyTorch/Python/CUDA不要。2026年5月にリポジトリが `argmaxinc/argmax-oss-swift` に改名され、WhisperKit + SpeakerKit（pyannote話者分離）+ TTSKit（Qwen3-TTS）を1つのMITライセンスSwiftパッケージに統合。
- **lightning-whisper-mlx**: MLXフレームワークでApple Silicon向けに最適化した別実装。
- **対象HW**: Apple Silicon専用（iOS/macOSアプリ組込み向け）。

Source: [argmaxinc/argmax-oss-swift](https://github.com/argmaxinc/argmax-oss-swift), [mustafaaljadery/lightning-whisper-mlx](https://github.com/mustafaaljadery/lightning-whisper-mlx)

### TensorRT-LLM Whisper / vLLM Whisper
- **TensorRT-LLM**: NVIDIA公式。PyTorch重みをTRT-LLMフォーマットに変換しTensorRTエンジンをビルド。distil-whisperの重みにも対応。Triton Inference Serverバックエンドとしても提供。
- **vLLM**: `openai/whisper-large-v3`等をエンコーダー・デコーダーモデルとしてロードして推論可能（vLLM 0.7系で対応）。
- **対象HW**: NVIDIA GPU、本番大規模サービング向け。

Source: [NVIDIA/TensorRT-LLM whisper example](https://github.com/NVIDIA/TensorRT-LLM/tree/main/examples/models/core/whisper), [vLLM Whisper docs](https://docs.vllm.ai/en/v0.7.3/getting_started/examples/whisper.html)

### Hugging Face Transformers 最適化（SDPA/FlashAttention/torch.compile）
- **技術**: `attn_implementation="sdpa"` でPyTorchのScaled Dot Product Attention（内部でFlashAttention/メモリ効率的アテンションカーネルを呼び出し）を利用。torch>=2.1.1ではデフォルト有効。torch.compileでフォワードパスをJITコンパイルし融合カーネルにディスパッチ。
- **速度**: SDPA + torch.compileの組み合わせで最大5倍高速化との報告。
- **制約**: FlashAttentionはfp16/bf16のみ対応、SDPAは`output_attentions=True`等一部パラメータ非対応で自動的にeager実装にフォールバック。

Source: [HF Transformers GPU inference docs](https://huggingface.co/docs/transformers/main/perf_infer_gpu_one), [Attention backends docs](https://huggingface.co/docs/transformers/attention_interface)

### faster-whisper-server / speaches
- **概要**: faster-whisperをバックエンドとするOpenAI互換API（`/v1/audio/transcriptions`, `/v1/audio/translations`）サーバー。Docker配布、GPU/CPU両対応。
- **speaches**（faster-whisper-serverの改称後継）: SSEでのストリーミング部分転写、OpenAI互換Realtime APIエンドポイントを提供。OpenAI公式SDK/CLIからそのまま利用可能。

Source: [speaches解説](https://gotranscript.com/public/deploy-faster-whisper-server-for-rapid-transcriptions), [andyjjrt/faster-whisper-server](https://github.com/andyjjrt/faster-whisper-server)

### Moonshine (Useful Sensors) — Whisper隣接
- **位置づけ**: Whisperとは別アーキテクチャだが同カテゴリの軽量ASRモデル。RoPE（回転位置埋め込み）採用でパディング不要、可変長音声を実時間で処理（Whisperの固定30秒窓と対照的）。
- **モデルサイズ**: Tiny 27.1M、Base 61.5M パラメータ。
- **速度**: Whisper tiny.en比、10秒音声で計算量5分の1、WER増加なし。
- **用途**: 低コストハードウェア上でのリアルタイム音声認識・音声コマンド認識に特化（英語のみ）。

Source: [Moonshine論文](https://arxiv.org/pdf/2410.15608), [UsefulSensors/moonshine HF](https://huggingface.co/UsefulSensors/moonshine)

---

## 3. 技術別サマリー

| 手法 | 代表実装 | 効果 |
|---|---|---|
| 量子化(INT8/4bit) | faster-whisper, whisper.cpp | メモリ削減＋速度向上、WER増は1%未満 |
| バッチ処理 | WhisperX, whisper-jax, insanely-fast-whisper | 数倍〜数十倍の速度向上 |
| 蒸留(デコーダー縮小) | distil-whisper, large-v3-turbo | パラメータ半減以上、速度2〜6倍、WER1%以内の劣化 |
| カーネル最適化(FlashAttention/SDPA) | insanely-fast-whisper, HF Transformers | 数倍高速化、精度劣化なし |
| VADチャンキング | WhisperX, whisper-timestamped, faster-whisper | 無音除去でハルシネーション抑制＋実効速度向上 |
| 投機的デコーディング | whisper-medusa, distil-whisper+large | 1.5〜2倍、出力同一性を数学的に保証（distil-whisper方式） |
| 専用推論エンジン | CTranslate2, TensorRT-LLM, ONNX Runtime, CoreML | ハードウェア特化の大幅高速化 |

---

## 4. 2025〜2026年の新展開

- **OpenAI自身の後継**: Whisperの正式な「次世代版」は出ていないが、2025年3月20日に **gpt-4o-transcribe** と **gpt-4o-mini-transcribe** をAudio APIに追加。GPT-4oの音声理解をベースにした転写専用SKUで、アクセント・背景ノイズ・話速変動に対するWERと言語認識精度がWhisperより改善。16,000トークンのコンテキストウィンドウ。料金は入力$2.50/1Mトークン、出力$10/1Mトークン、目安$0.006/分。ただしオープンウェイトではなくAPI提供のみ。
- **WhisperKitの統合再編**（2026年5月）: argmaxinc/WhisperKitが `argmax-oss-swift` に改名し、話者分離・TTSまで含むApple Silicon向け音声AIスイートに拡張。
- **sherpa-onnxのNPU対応拡大**: Qualcomm NPU (QNN)、RK/Axera/Ascend NPUなどエッジチップ対応が継続的に拡大。
- 全体として、2025〜2026年はOpenAI本体は「Whisperのオープンウェイト更新」より「クローズドAPIでのgpt-4o系転写モデル」路線にシフトしており、オープンソース高速化エコシステムはコミュニティ主導で発展している。

Sources: [GPT-4o Transcribe Model docs](https://developers.openai.com/api/docs/models/gpt-4o-transcribe), [Gate.AI GPT-4o Transcribe解説](https://gate.ai/blog/gpt-4o-transcribe-openai-specs-pricing-api-use-cases), [argmax-oss-swift](https://github.com/argmaxinc/argmax-oss-swift)
