"""Chinese/English punctuation restoration for ASR output.

Standalone module: loads sherpa-onnx's official CT-Transformer punctuation
model (punc_ct-transformer_zh-cn-common, converted by the sherpa-onnx
team, see docs/PUNCT_ZH.md) and inserts Chinese/English punctuation
(，。？！ etc.) into raw, unpunctuated text such as what the Paraformer-zh
bilingual recognizer typically emits.

Usage:
    from punct_zh import PunctuatorZh
    p = PunctuatorZh()
    p.restore("我们都是木头人不会说话不会动")
    # -> "我们都是木头人，不会说话，不会动。"

This module is intentionally self-contained and does NOT import or modify
asr_engine.py / realtime_transcribe.py -- integration is done elsewhere.
"""

from __future__ import annotations

from pathlib import Path

import sherpa_onnx


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = SCRIPT_DIR.parent / "models" / "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8"


class PunctuatorZh:
    """Restores Chinese/English punctuation in unpunctuated text.

    Uses sherpa-onnx's OfflinePunctuation (CT-Transformer zh-en, int8) on
    CPU. The model predicts, for the whole input, where ，。？！ should go.
    Missing model files raise FileNotFoundError so callers can degrade
    quietly (same contract as PunctuatorJa).
    """

    def __init__(self, model_dir: str | Path = DEFAULT_MODEL_DIR):
        model_dir = Path(model_dir)
        onnx_path = model_dir / "model.int8.onnx"
        if not onnx_path.exists():
            raise FileNotFoundError(
                f"Punctuation model not found under {model_dir}. "
                "Expected model.int8.onnx "
                "(see docs/PUNCT_ZH.md for download instructions)."
            )
        config = sherpa_onnx.OfflinePunctuationConfig(
            model=sherpa_onnx.OfflinePunctuationModelConfig(
                ct_transformer=str(onnx_path),
                num_threads=4,
            ),
        )
        self._punctuator = sherpa_onnx.OfflinePunctuation(config)

    def restore(self, text: str) -> str:
        """Return `text` with 、。？！ inserted; never strips anything."""
        if not text:
            return text
        return self._punctuator.add_punctuation(text)


if __name__ == "__main__":
    p = PunctuatorZh()
    out = p.restore("我们都是木头人不会说话不会动这是一个测试你好吗")
    print(out)
