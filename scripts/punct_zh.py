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

# Attributive-guard sets (see _repair_word_splits check 2): bare attributive
# chars that can never end a sentence without a predicate marker in front
# ("新。模型" is always bogus; "东西很新。" is a real end).
_ATTRIB_FINALS = set("新旧整半全该本另某每各此")
_PRED_MARKERS = set("很太真不没得更挺蛮好也又较够还都最")


def _latin_letter(c: str) -> bool:
    """Alphanumeric check shared by the Latin guards: a sentence-end mark
    between two alphanumerics -- "调用AP。I" or the version-string tear
    "v4.1。flash" -- is a punctuator artifact, not a boundary. Digits are
    INCLUDED deliberately (unlike a pure-letter check) because version
    numbers like v4.1 sit right in the crosshair; real ends always have a
    CJK char (or nothing) on one side ("支持HTML5。然后"), so they survive."""
    return ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9")


class PunctuatorZh:
    """Restores Chinese/English punctuation in unpunctuated text.

    Uses sherpa-onnx's OfflinePunctuation (CT-Transformer zh-en, int8) on
    CPU. The model predicts, for the whole input, where ，。？！ should go.
    Missing model files raise FileNotFoundError so callers can degrade
    quietly (same contract as PunctuatorJa).

    sherpa's add_punctuation() exposes no per-position probabilities, and
    one of its ASR-text failure modes is splitting a WORD with a sentence
    mark ("着重针对" -> "着重。针对" -- user report). _repair_word_splits()
    re-segments a small window around every inserted 。？！with jieba and
    deletes the marks that cut through a word token; jieba is a dictionary
    segmenter (no neural net), loaded lazily once (<1s) and queried at
    microsecond cost per mark.
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
        self._jieba = None  # lazy: dictionary load (~1s) only when repair is needed

    def _get_jieba(self):
        if self._jieba is None:
            import jieba

            jieba.initialize()
            self._jieba = jieba
        return self._jieba

    def _repair_word_splits(self, text: str) -> str:
        """Delete sentence-end marks (。？！) that cannot be real boundaries.

        Two conservative checks per mark (？！included, ，never touched):

        1. jieba join: a 2+2 char window around the mark (mark excluded) is
           re-segmented; a single token SPANNING the mark's position means
           the mark tore a word apart ("着。重") -- drop it.
        2. attributive guard: a mark right after a bare attributive char
           ("新。模型" -- the user's second sample) is syntactically dead:
           these chars end a sentence only when a predicate marker stands
           in front of them ("东西很新。"), so a mark whose PREVIOUS char
           is one of _ATTRIB_finals and whose pre-previous is NOT one of
           _PRED_markers is bogus -- drop it. ("最新。模型" stays: 最 is
           not in the guard set because 最新/最快 are true predicates too;
           protecting them costs the rare miss.)

        Marks passing both checks survive; everything else is the model's
        output untouched."""
        try:
            jieba = self._get_jieba()
        except Exception:
            jieba = None  # jieba missing: the attributive guard still runs
        marks = "。？！、"
        for _ in range(2):  # two passes: one repair can expose another
            changed = False
            out = []
            i = 0
            n = len(text)
            while i < n:
                ch = text[i]
                drop = False
                if ch in marks:
                    prev = text[i - 1] if i >= 1 else ""
                    nxt = text[i + 1] if i + 1 < n else ""
                    prev2 = text[i - 2] if i >= 2 else ""
                    if _latin_letter(prev) and _latin_letter(nxt):
                        # alphanumerics on BOTH sides: the mark tore an
                        # acronym or version string apart ("调用AP。I" /
                        # "v4.1。flash") -- real ends always have CJK or
                        # nothing on one side ("支持HTML5。然后"). Applies to
                        # 、 too: CT drops a 、 between letter runs it cannot
                        # parse ("code、body" from CodeBuddy), and a real
                        # Latin-item enumeration ("腾讯、阿里") has a CJK
                        # neighbor and survives
                        drop = True
                    elif ch != "、" and prev in _ATTRIB_FINALS and prev2 not in _PRED_MARKERS:
                        # attributive guard is sentence-end logic; a 、 after
                        # "新" is a legit pause ("新、旧两种") -- never touch it
                        drop = True
                    elif jieba is not None:
                        lo, hi = max(0, i - 2), min(n, i + 3)
                        window = text[lo:i] + text[i + 1:hi]
                        pos = i - lo  # the mark's position inside the window
                        acc = 0
                        for tok in jieba.cut(window):
                            acc += len(tok)
                            if acc > pos and acc - len(tok) < pos:
                                # a token starts before and ends after the
                                # mark: the mark splits a word -> drop it
                                drop = True
                                break
                    if drop:
                        out.append("")
                        changed = True
                    else:
                        out.append(ch)
                else:
                    out.append(ch)
                i += 1
            new = "".join(out)
            if not changed:
                break
            text = new
        return text

    def restore(self, text: str) -> str:
        """Return `text` with ，。？！ inserted; never strips anything
        except the word-splitting marks _repair_word_splits() removes."""
        if not text:
            return text
        return self._repair_word_splits(self._punctuator.add_punctuation(text))


if __name__ == "__main__":
    p = PunctuatorZh()
    out = p.restore("我们都是木头人不会说话不会动这是一个测试你好吗")
    print(out)
