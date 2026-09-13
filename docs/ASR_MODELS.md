# ASR model selection notes

## Chinese (zh) routing

`asr_engine.py` routes `zh` to
`sherpa-onnx-paraformer-zh-2024-03-09` — the Mandarin **bilingual zh+en**
build (vocab8358). English words come out whole, and Chinese digits are
restored to Arabic numerals by the pipeline (`百分之十七` -> `17%`,
implemented in `zh_digit.py`).

**Do NOT substitute** `sherpa-onnx-paraformer-zh-int8-2025-10-07` (the
四川话/川渝方言 fine-tune): it shreds unseen English into single spaced
letters (`"d e p c k"`) and writes digits as Chinese words (`"4"` -> `四`).
`scripts/download_models.py` fetches the correct model.
