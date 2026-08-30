# hayamimi（早耳）

[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**纯 CPU 实时多语种语音转文字。** 实时字幕、浏览器仪表盘、说话人标签、即时翻译 —— 不需要 GPU，不依赖云端 API，内存占用低于 2GB。

"早耳"（hayamimi）在日语中意为"耳聪目明、反应快的人"。本项目的设计目标正是如此：你还在说话时，部分字幕已经出现；说完后约 **100ms** 内，确认的字幕行即落地。

English README → [README.md](README.md) / 日本語版 → [README.ja.md](README.ja.md)

## 为什么

大多数仅凭 CPU 的实时转写方案都会退而求其次采用一个通用模型（如 Whisper），并接受其准确率上限。hayamimi 则把每一段语音路由到最适合该语言的专用模型，全部以量化（INT8）ONNX 模型通过 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 运行 —— 无需 PyTorch、无需 CUDA。

在真实日文广播音频上（见 `docs/SCORECARD.md`），这套路由的 **CER 为 5.8%**，不及 `whisper-large-v3-turbo` 在同样片段的 13.8%，且在 6 核桌面 CPU 上以 10-50 倍于实时的速度运行。

## 功能

| 功能 | 说明 |
|---|---|
| 5 路语言路由 | ja/zh/ko/yue 及 en+24 种欧洲语言各配专用最优模型；其余约 1600 种语言回退到 Meta Omnilingual ASR |
| 部分字幕 | 说话过程中约每 0.5s 更新一次草稿文本 |
| 快速确认 | 日语停顿时，确认行通常约 100ms 落地（其他语言见 `docs/GOALS.md`） |
| 两遍精修 | 静音 2 秒后对最近若干话语批量重新解码，产出更高精度的"清稿"（日语真实广播 CER 15.5% → 12.0%） |
| 说话人标签 | `--speakers` 用 CAM++ 说话人嵌入为每段话语标注 S1/S2/...（按话轮，非完整说话人分离） |
| 翻译 | `--translate en,zh,ko,es,...` 实时翻译日语行（en 走 FuguMT；其余任意 M2M-100 目标代码，模型词表支持即可；zh/ko/es 已实测质量，见 docs/TRANSLATE_M2M.md） |
| 热词/用户词典 | `--hotwords` 向解码注入专有名词偏好（当前对 ja 路由无效，见 Limitations）；`--replace` 做事后查找替换，全路由生效 |
| OBS 覆盖层 + 仪表盘 | `--serve` 启动本地 HTTP 服务器，提供浏览器源覆盖层与实时仪表盘 |
| 网络音频输入 | `--input ws` 接受经 WebSocket 传来的麦克风音频（手机、ESP32/stackchan 等），走同一流水线，包括仪表盘/覆盖层 |
| 内存有界 | LRU 模型淘汰策略让常驻模型保持在可配置上限内（默认总内存 <2GB） |
| 纯 CPU | 所有模型均经 sherpa-onnx 以量化 ONNX 运行；无需 GPU 或 PyTorch |

## 演示 UI

`--serve` 启动本地服务器，暴露三个视图：

- **`http://localhost:8833/dashboard`** —— 实时仪表盘：进行中语音的部分文本条、带语言徽章的确认信息流、说话人标签、每行延迟，以及两遍精修的"清稿"栏。
- **`http://localhost:8833/`** —— 供 OBS 浏览器源使用的最小覆盖层（将 URL 作为浏览源添加到 OBS 可得到直播字幕）。确认行与进行中行为两行；追加 `?show=final` 或 `?show=partial` 可只渲染其中一行，方便各自独立摆放与定制。
- **`http://localhost:8833/transcript`** —— 简易滚动字幕历史。

![dashboard](docs/images/dashboard.png)

## 网络音频输入

`--input ws` 以 WebSocket 接收端点代替本地麦克风。手机或 stackchan 类 ESP32 板可通过局域网串流麦克风音频，并经 hayamimi 正常流水线转写：

```bash
.venv/Scripts/python scripts/realtime_transcribe.py --input ws --serve
# -> ws://<host>:8766/ingest 接收音频；http://localhost:8833/dashboard 显示结果
```

协议：连接 `/ingest`，发送一条 JSON 文本帧（`{"sr": 16000, "format": "pcm_s16le", "channels": 1}`），然后以二进制帧串流原始 `pcm_s16le` 音频。服务器会重采样非 16kHz 音频，以仪表盘 SSE 流相同的 partial/final/translation/refine JSON 事件应答，客户端可据此自行显示字幕。同一时刻只接受一个音频生产客户端；`scripts/ws_mic_client.py` 是无依赖参考客户端（按实时节奏串流一个 wav 文件），也可作为手机/ESP32 实现模板。

## 环境要求

Python 3.10+，系统中可调用 ffmpeg。已在 **Windows 11** 上开发与测试；macOS/Linux 预期可用（运行时均跨平台），但尚未端到端 CI 验证 —— 欢迎反馈。

## 快速开始

```bash
python -m venv .venv

# Windows
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python scripts\download_models.py

# macOS / Linux
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/download_models.py

# 麦克风实时转写
.venv/Scripts/python scripts/realtime_transcribe.py     # Windows
.venv/bin/python scripts/realtime_transcribe.py          # macOS/Linux

# 带仪表盘 + OBS 覆盖层
.venv/Scripts/python scripts/realtime_transcribe.py --serve
# -> 浏览器打开 http://localhost:8833/dashboard
```

`scripts/download_models.py` 下载约 3.1GB 预训练模型到 `models/`（git 忽略）。传 `--minimal` 为约 1.1GB 的 ja/en 精简安装（ReazonSpeech、whisper-tiny、Silero VAD、日语标点）。各模型的许可证见 `THIRD_PARTY_NOTICES.md`。

## 桌面字幕窗（`desktop-subtitle/`）

本分支新增了一个透明、始终置顶的**桌面字幕窗**，直接在屏幕上渲染 hayamimi 的 OBS 覆盖层 —— 无需 OBS。它位于 `desktop-subtitle/`（Electron 应用，与 Python 服务器相互独立）。

**功能**

- 在桌面上显示实时字幕（透明背景、无边框、始终置顶、有任务栏图标）。
- `final`（确认行）与 `partial`（进行中草稿）共用**同一行**，一起换行；翻译独立成**单独一行**显示在其下方。
- 字幕自上而下流动，窗口**高度自动贴合内容**（宽度固定 —— 文本通过换行容纳）。
- 左上角固定按钮：
  - **🔒 / 🔓** —— 切换"交互拖拽模式 / 点击穿透模式"。
  - **⚙** —— 设置菜单（字号、字体、翻译语言、点击穿透、退出）。
  - **EN / ZH / KO / OFF** —— 循环切换要显示的翻译语言。
- 拖拽采用系统原生 `-webkit-app-region`，移动窗口不会改变尺寸或让内容漂移。

**安装运行**

```bash
cd desktop-subtitle
npm install            # 安装 Electron（devDependency）
npm start              # 打开字幕窗
```

服务器需以 `--serve` 运行（见快速开始）。字幕窗默认加载 `http://localhost:8833/`。

**快捷键**

| 按键 | 作用 |
|---|---|
| `Ctrl+Alt+D` | 切换点击穿透 |
| `Ctrl+Alt+L` | 循环切换翻译语言 |
| `Esc` | 退出字幕窗 |

**Windows 一键启动**（`desktop-subtitle/启动早耳.bat`）

双击即可启动（或复用）默认带 `--translate zh` 的 hayamimi 服务器，随后打开字幕窗。配套的 `desktop-subtitle/停止早耳.bat` 可一键停止两者。

## 运行时翻译热切换（无需重启）

在**服务器运行中**改变日语行翻译语言，有两条独立途径：

1. **控制台命令** —— 在服务器终端窗口中输入：
   `translate en,zh,ko`（启用）、`translate off`（停用）、
   `translate zh`（切换）、`quit`（退出）。
2. **HTTP 端点** —— 向 `POST /api/translate` 提交
   `{"langs": "zh"}` / `{"langs": "en,zh,ko"}` / `{"langs": ""}`，
   即热切换当前生效的翻译器。这正是字幕窗 **EN/ZH/KO/OFF** 按钮所调用的方式。

底层实现：`TranslationWorker` 新增 `set_langs()`（线程安全切换，不会遍历失效副本），`main()` 始终启动一个空的 worker，因此后续热切换无需重启。翻译器仍在首次使用时懒加载，第一次 `translate ...` 需要数秒加载模型。

## CLI 参考

所有参数见 `scripts/realtime_transcribe.py` 的 `--help`。常用项：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--wav PATH` | 麦克风输入 | 改从 16kHz 单声道 WAV 文件模拟流式输入 |
| `--no-realtime` | 关 | 搭配 `--wav` 时不按实时节奏播（快速批处理） |
| `--input {mic,wav,ws}` | mic 或 wav | 音频来源；`ws` 接受网络音频 |
| `--serve [PORT]` | 关，8833 | 启动仪表盘 + OBS 覆盖层 |
| `--translate [LANGS]` | 关，en | 把日语行翻译成这些逗号分隔的语言 |
| `--speakers` | 关 | 以 S1/S2/... 标记话语说话人 |
| `--hotwords PATH` | 无 | 热词列表（每行一个），向解码注入专有名词偏好 |
| `--replace PATH` | 无 | 用户词典：每行 `错误=正确`，作用于全部输出 |

## 架构

```
                          ┌─────────────┐
  mic / wav ───────────▶ │  Silero VAD │  0.35s 停顿时长 + 0.8s 前滚
                          └──────┬──────┘
                                 │ 语音段
                                 ▼
                   ┌───────────────────────────┐
                   │  whisper-tiny 口语语言识别  │  段仍在输入时于前 4s 运行
                   │  (+ 字符集仲裁)             │
                   └─────────────┬─────────────┘
                                 │ 语言标签
                 ┌───────────────┼────────────────┬─────────────┬──────────────┐
                 ▼               ▼                ▼             ▼              ▼
             ┌───────┐      ┌─────────┐      ┌──────────┐  ┌─────────┐   ┌──────────┐
             │  ja   │      │   zh    │      │  ko/yue  │  │ en + 24 │   │  ~1600   │
             │ Reazon│      │Paraformer│      │SenseVoice│  │EU 语种  │   │  其他    │
             │Speech │      │   -zh   │      │  small   │  │Parakeet │   │Omnilingual│
             │Zipform│      │         │      │          │  │TDT v3   │   │  ASR     │
             └───┬───┘      └────┬────┘      └────┬─────┘  └────┬────┘   └────┬─────┘
                 └───────────────┴────────────────┴─────────────┴─────────────┘
                                                │
                     partial（约每 0.5s）        │      final（停话约 0.1s）
                     ◀───────────────────────────┴───────────────────────▶
                                                │
                          ┌─────────────────────┼─────────────────────┐
                          ▼                     ▼                     ▼
                 ┌────────────────┐   ┌──────────────────┐   ┌────────────────┐
                 │ ja 标点恢复     │   │ 说话人标注        │   │ 翻译            │
                 │ (BERT restore) │   │ (CAM++, --speakers)│   │ (FuguMT/M2M-100)│
                 └────────────────┘   └──────────────────┘   └────────────────┘
                                                │
                      静音 2 秒：批量重解码最近话语（两遍精修）
                                                │
                                                ▼
                              仪表盘 / OBS 覆盖层 / 字幕文件
```

模型首次使用时懒加载；LRU 缓存按 `--max-resident` 逐出最近最少使用的非日语模型，使内存保持有界。

## 实测性能

端到端（语言识别 → 路由 → 解码 → 日语标点），真实语音，无前滚/两遍精修（单片段）。`en` 使用 WER，其余使用 CER（`yue` 做 t2s 归一）。完整方法见 `docs/SCORECARD.md`。

| 语言 | 片段数 | 语言识别准确率 | 路由 | 平均错误率 | 平均 RTF |
|---|---|---|---|---|---|
| ja | 15 | 15/15 | ReazonSpeech | 7.5% | 0.071 |
| en | 15 | 15/15 | Parakeet v3 | 2.3% | 0.109 |
| zh | 12 | 12/12 | Paraformer-zh | 5.3% | 0.102 |
| ko | 12 | 12/12 | SenseVoice | 8.1% | 0.062 |
| yue | 12 | 12/12 | SenseVoice | 6.1% | 0.061 |

各路由 RTF 均远低于 0.2，意味着在纯 CPU 上每条路由比实时快 9-16 倍 —— 完整目标表见 `docs/GOALS.md`，完整迭代日志见 `docs/BENCHMARKS.md`（30+ 实测改动，以及每项延迟/内存/准确率权衡的原因）。

该日志中的关键数字：

- **日语 CER 5.8%**（beam search）于真实广播音频，对比同样片段的 `whisper-large-v3-turbo` 13.8% —— 不足其一半错误率。
- **日语平均 final 延迟约 100ms**（含标点）；全功能 5 语言压力测试下平均 236ms、最大 552ms。
- **内存 <2GB**（`--max-resident 3`；`--max-resident 2` 时约 1.35GB）。

## 已知局限（坦诚列表）

- **不支持句内语言混码。** 路由按话语选择一种语言；一句内混日语和英语时，少数语言部分会被弄乱或丢弃。按话语级别的切换（如同声传译交替整句）表现良好；句内词级切换则不行。
- **小段落在响铃/音效/BGM 冲击后可能误路由。** 语言切换守卫（`--lang-switch-guard`，搭配 `--lid-switch-confirm`）可缓解，但"高度自信却错误"的 LID+解码组合（乱码恰好落在错误语言的字符集里）仍是已知盲区 —— 见 `docs/BENCHMARKS.md` 迭代 #29 的量化前后对比。`--lid-switch-confirm 1 --lang-switch-guard 0` 可完全关闭粘滞迟滞（任何检测立即切换会话语言），以噪声鲁棒性换取最大响应度。
- **会话的首段话语总是先用 SenseVoice 确认语言**（见 `docs/NOISE.md` 的双 LID 确认章节），whisper-tiny 启动时的误报不再会把会话路由到无对应模型的语言。SenseVoice 覆盖 5 种语言（ja/en/zh/ko/yue）之外的欧洲/`--minimal` 未覆盖语言，仍需达到 `--lang-switch-guard` 时长的片段重复 `--lid-switch-confirm` 次才能确认为会话语言，因此合法欧洲语言会话在启动时会有短暂延迟才确立。
- **`--hotwords` 当前对 ja（ReazonSpeech）路由无效。** ReazonSpeech 的 `tokens.txt` 是字节级 BPE，与 hayamimi 热词使用的 `modeling_unit=cjkchar` 编码不兼容，所有热词编码失败（sherpa-onnx 只以 stderr 警告报告并仍以 0 退出 —— 见 GitHub issue #1）。hayamimi 现于启动时打印有多少热词编码失败；日语专有名词请改用 `--replace`。真正的修复需要 ReazonSpeech 发行版附带的匹配 `bpe.model`（当前未随包），或从零实现字节 BPE 热词编码器 —— 列为未来工作。
- **两个同时说话的说话人不会被分离。** `--speakers` 做按话轮的说话人标注（每个已确认 VAD 段一个嵌入、最近质心分配），并非真正的说话人分离；同时语音只会得到一个标签。
- **翻译质量存在真正的上限，而非仅调参问题。** FuguMT（ja->en）与 M2M-100（ja->zh/ko）是小模型；重复循环被抑制但未根除，且 ja->zh/ko 翻译中数字不能可靠保留（依靠其处理数字或财务内容前，请看 `docs/TRANSLATE.md` 与 `docs/TRANSLATE_M2M.md` 的实测失败用例）。
- **端到端麦克风流水线未经本项目自身测试之外独立验证** —— 见 `docs/GOALS.md` 的余留工作章节。你的结果与该数字不符时欢迎提交 issue。

## 许可证

源码为 MIT（`LICENSE`，copyright oboroge0）。本仓库不包含任何模型权重 —— `scripts/download_models.py` 在安装时从其原始发布方获取，每个模型自带许可证（完整表格见 `THIRD_PARTY_NOTICES.md`）。

**有一个模型并非宽松许可**：ja->en 翻译模型（`mojicast-fugumt-ja-en-ct2`，`--translate en` 使用）为 **CC BY-SA 4.0（相同方式共享）**。若再分发该模型权重，你必须保留署名并以 CC BY-SA 4.0 许可再分发。这不影响 hayamimi 自身代码许可，也不影响其他 `--translate` 目标（M2M-100 为 MIT）。

## 致谢

hayamimi 建立在以下项目之上，没有它们就不会存在：

- [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) —— 本项目所有模型都经由其运行的 ONNX 推理引擎。
- [ReazonSpeech](https://research.reazon.jp/)（Reazon 人机交互实验室）—— 支撑本项目准确率主张的日语 ASR 模型。
- [NVIDIA NeMo / Parakeet](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) —— 英语及 24 种欧洲语言。
- [Meta AI Omnilingual ASR](https://github.com/facebookresearch/omnilingual-asr) —— 约 1600 种语言的兜底，让"多语种"名副其实。
- [FunASR / SenseVoice](https://github.com/FunAudioLLM/SenseVoice)（阿里达摩院）—— 中文、韩语、粤语 ASR。
- [Mojicast](https://github.com/ishiki-emo/mojicast)（ishiki-emo）—— 实时字幕流水线的设计灵感，以及本项目采用的标点/翻译模型制品来源。Mojicast 本身也是一款值得一试的完整离线实时字幕应用。
- [Silero VAD](https://github.com/snakers4/silero-vad) —— 语音活动检测。
- [3D-Speaker](https://github.com/modelscope/3D-Speaker)（阿里达摩院）—— `--speakers` 背后的 CAM++ 说话人嵌入模型。
- [Kiwi](https://github.com/bab2min/kiwipiepy) —— 韩语形态分析分词器，用于修复 SenseVoice 的韩语 token 空格输出。

## 参与贡献

见 `CONTRIBUTING.md`。