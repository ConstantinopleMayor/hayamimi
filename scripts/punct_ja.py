"""Japanese punctuation restoration for ASR output.

Standalone module: loads a small BERT-char token-classification ONNX model
(from the OSS Mojicast project, see docs/PUNCT_JA.md) and inserts 、(comma)
and 。(period) into raw, unpunctuated Japanese text such as what a streaming
ASR engine (e.g. ReazonSpeech k2 zipformer) typically emits.

Usage:
    from punct_ja import PunctuatorJa
    p = PunctuatorJa()
    p.restore("明日の会議は午後三時から始まります資料の準備をお願いします")
    # -> "明日の会議は午後三時から始まります。資料の準備をお願いします。"

This module is intentionally self-contained and does NOT import or modify
asr_engine.py / realtime_transcribe.py -- integration is done elsewhere.
"""

from __future__ import annotations

import os
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
import onnxruntime as ort

try:
    import fugashi
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "fugashi is required for the Mojicast punctuation tokenizer. "
        "Install with: pip install fugashi unidic-lite"
    ) from e


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = SCRIPT_DIR.parent / "models" / "mojicast-punct-onnx"

# Trailing/leading punctuation that we should not duplicate insertion after.
_JA_PUNCT_CHARS = set("。、！？!?…「」『』（）()【】・,.\n")

# Heuristic suffixes that strongly indicate a question -- the model only
# predicts comma/period (see docs/PUNCT_JA.md), so "?" is added on top via
# this simple rule-based check on the sentence-final mora, not by the model.
_QUESTION_SUFFIXES = (
    "ですか", "ますか", "でしょうか", "かな", "かしら", "かい", "の",
    "だろうか", "でしたか", "ましたか",
)


class PunctuatorJa:
    """Restores Japanese 、/。 punctuation in unpunctuated text.

    Uses a char-level BERT token-classification model (Tohoku BERT-base
    Japanese-char-v3 body + Mojicast punctuation head, ONNX int8), run via
    onnxruntime on CPU. The model predicts, for every input character,
    whether a comma (読点 、) and/or period (句点 。) should follow it.
    """

    def __init__(
        self,
        model_dir: str | Path = DEFAULT_MODEL_DIR,
        comma_threshold: float = 0.5,
        period_threshold: float = 0.5,
        max_chars: int = 500,
        force_final_period: bool = True,
    ):
        model_dir = Path(model_dir)
        # NOTE: the int8 dynamic-quantized file shipped in this HF repo
        # (punct_bert.int8.onnx) was verified to produce near-constant,
        # token-independent logits on this onnxruntime build (1.29.0) --
        # i.e. it does not actually restore punctuation. The fp32 file
        # (punct_bert.onnx) was verified correct, so it is used here.
        # See docs/PUNCT_JA.md for details.
        onnx_path = model_dir / "punct_bert.onnx"
        vocab_path = model_dir / "vocab.txt"
        if not onnx_path.exists() or not vocab_path.exists():
            raise FileNotFoundError(
                f"Punctuation model files not found under {model_dir}. "
                "Expected punct_bert.int8.onnx and vocab.txt "
                "(see docs/PUNCT_JA.md for download instructions)."
            )

        self.comma_threshold = comma_threshold
        self.period_threshold = period_threshold
        self.max_chars = max_chars
        self.force_final_period = force_final_period

        # --- vocab: standard BERT vocab.txt, line index == token id ---
        self.vocab: dict[str, int] = {}
        with open(vocab_path, encoding="utf-8") as f:
            for idx, line in enumerate(f):
                self.vocab[line.rstrip("\n")] = idx
        self.pad_id = self.vocab["[PAD]"]
        self.unk_id = self.vocab["[UNK]"]
        self.cls_id = self.vocab["[CLS]"]
        self.sep_id = self.vocab["[SEP]"]

        # --- mecab word splitter (matches BertJapaneseTokenizer's
        # MecabTokenizer + CharacterTokenizer pipeline: NFKC-normalize,
        # split into mecab morphemes, then split each morpheme into
        # individual characters, mapping OOV chars to [UNK]) ---
        self._tagger = fugashi.Tagger()

        # --- onnxruntime session ---
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.session = ort.InferenceSession(
            str(onnx_path), sess_options=so, providers=["CPUExecutionProvider"]
        )

    # ------------------------------------------------------------------
    def _tokenize_chars(self, text: str) -> list[str]:
        """Return the list of surface characters as the model tokenizer
        would see them (NFKC-normalized, mecab-segmented then char-split).
        The concatenation of this list equals the NFKC-normalized text."""
        norm = unicodedata.normalize("NFKC", text)
        chars: list[str] = []
        for word in self._tagger(norm):
            chars.extend(list(word.surface))
        return chars

    def _char_id(self, ch: str) -> int:
        return self.vocab.get(ch, self.unk_id)

    # ------------------------------------------------------------------
    def restore(self, text: str, force_final_period: bool | None = None) -> str:
        """Insert 、/。 into `text` and return the punctuated string.

        force_final_period overrides the constructor default per call:
        the streaming draft path passes False -- a provisional line must
        not grow a trailing 。 every time it is re-decoded."""
        text = text.strip()
        if not text:
            return text
        ffp = self.force_final_period if force_final_period is None else force_final_period

        chars = self._tokenize_chars(text)
        if not chars:
            return text
        # chunk defensively (model max_position_embeddings=512, CLS+SEP+chars)
        chars = chars[: self.max_chars]

        input_ids = [self.cls_id] + [self._char_id(c) for c in chars] + [self.sep_id]
        attention_mask = [1] * len(input_ids)

        ids_np = np.array([input_ids], dtype=np.int64)
        mask_np = np.array([attention_mask], dtype=np.int64)

        logits = self.session.run(
            ["logits"], {"input_ids": ids_np, "attention_mask": mask_np}
        )[0][0]  # -> (seq_len, 2)

        probs = 1.0 / (1.0 + np.exp(-logits))

        out_parts: list[str] = []
        n = len(chars)
        for i, ch in enumerate(chars):
            out_parts.append(ch)
            # logits[i+1] corresponds to the position right after chars[i]
            # (index 0 is [CLS]).
            comma_p, period_p = probs[i + 1]
            is_last = i == n - 1
            next_ch = chars[i + 1] if not is_last else ""
            if ch in _JA_PUNCT_CHARS:
                continue
            if next_ch in _JA_PUNCT_CHARS:
                # don't double up before existing punctuation
                continue
            if period_p >= self.period_threshold and (
                not is_last or ffp
            ):
                out_parts.append("。")
            elif comma_p >= self.comma_threshold:
                out_parts.append("、")

        result = "".join(out_parts)
        if ffp and result and result[-1] not in _JA_PUNCT_CHARS:
            result += "。"

        result = self._apply_question_marks(result, force_final_period=ffp)
        return result

    @staticmethod
    def _apply_question_marks(text: str, force_final_period: bool = True) -> str:
        """Rule-based post-pass: turn a sentence-final '。' into '？' when
        the sentence ends in a common question suffix. The ONNX model only
        classifies comma/period, so this is a simple heuristic layered on
        top (see docs/PUNCT_JA.md, "known limitations").

        With force_final_period=False the LAST content segment gets no mark
        appended: the caller is punctuating a provisional draft, and a
        trailing 。 that comes and goes between re-decodes just flickers."""
        segments = text.split("。")
        # split() on "a。b。" -> ["a", "b", ""]; a trailing "" means the
        # text ended with "。" (so there's nothing after the last mark).
        last = len(segments) - 1
        out = []
        for i, seg in enumerate(segments):
            if not seg:
                continue
            if i == last and not force_final_period:
                out.append(seg)
                continue
            mark = "？" if seg.endswith(_QUESTION_SUFFIXES) else "。"
            out.append(seg + mark)
        return "".join(out)


# ------------------------------------------------------------------------
PUNCT4_LABELS = ("O", "、", "。", "？", "！")
PUNCT4_DEFAULT_MODEL_DIR = SCRIPT_DIR.parent / "models" / "punct-ja-4class-permissive"
PUNCT4_ONNX_RELPATH = os.path.join("quantized_ort", "punct_4class.int8.onnx")
PUNCT4_MAX_CHARS = 450
PUNCT4_INTRA_OP_NUM_THREADS = 2


class PunctuatorJa4Class:
    """Four-class (+ "none") ja punctuation restorer, upstream #58.

    Single token-classification model (sbintuitions/modernbert-ja-30m body,
    MIT, fine-tuned on FineWeb-2 jpn_Jpan) that predicts, per subword token,
    whether one of 、/。/？/！ follows it directly -- unlike PunctuatorJa
    above, ？ and ！ come from the MODEL, not the suffix heuristic, and the
    int8 export is ~44MB lighter and ~13x faster than the fp32 mojicast
    BERT (FLEURS-ja punctuation F1 0.888 vs 0.621 -- the repo README table).
    Model dir: models/punct-ja-4class-permissive/ (download via
    scripts/download_models.py).

    restore() returns text whose FINAL mark is still a guess about a
    possibly-unfinished sentence -- same contract as the old restorer's
    force_final_period output; the draft path strips trailing marks with
    realtime_transcribe.strip_draft_tail_marks(). There is no
    force_final_period flag: marks are inserted exactly where the model
    fires, and the caller's post-processing decides about the tail.
    """

    def __init__(
        self,
        model_dir: str | Path = PUNCT4_DEFAULT_MODEL_DIR,
        onnx_relpath: str | Path = PUNCT4_ONNX_RELPATH,
        max_chars: int = PUNCT4_MAX_CHARS,
        num_threads: int = PUNCT4_INTRA_OP_NUM_THREADS,
    ):
        import onnxruntime as ort

        try:
            from tokenizers import Tokenizer
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "the `tokenizers` package is required for PunctuatorJa4Class. "
                "Install with: pip install tokenizers"
            ) from e

        model_dir = Path(model_dir)
        onnx_path = model_dir / onnx_relpath
        tokenizer_path = model_dir / "hf" / "tokenizer.json"
        if not onnx_path.exists() or not tokenizer_path.exists():
            raise FileNotFoundError(
                f"4-class punctuation model files not found under {model_dir}. "
                "Expected quantized_ort/punct_4class.int8.onnx and "
                "hf/tokenizer.json (scripts/download_models.py fetches them)."
            )

        self.max_chars = max_chars
        self.tokenizer = Tokenizer.from_file(str(tokenizer_path))

        so = ort.SessionOptions()
        so.intra_op_num_threads = num_threads
        so.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(onnx_path), sess_options=so, providers=["CPUExecutionProvider"]
        )

    def restore(self, text: str) -> str:
        """Insert 、/。/？/！ into `text` and return the punctuated string.

        Purely additive: every character of `text` survives unchanged and
        only marks are inserted. The model was trained on NFKC-normalised
        text; callers feeding raw ASR output with fullwidth digits/Latin or
        halfwidth katakana should normalise first -- the pipeline normalizes
        here in one shot (unlike the upstream split, fork-side ASR inserts
        are pre-ITN for drafts; doing it here keeps every call site safe).
        """
        text = text.strip()
        if not text:
            return text
        text = unicodedata.normalize("NFKC", text)
        text = text[: self.max_chars]

        enc = self.tokenizer.encode(text)
        ids_np = np.array([enc.ids], dtype=np.int64)
        mask_np = np.ones((1, len(enc.ids)), dtype=np.int64)

        logits = self.session.run(
            ["logits"], {"input_ids": ids_np, "attention_mask": mask_np}
        )[0][0]  # -> (seq_len, 5)
        preds = logits.argmax(-1).tolist()

        return reconstruct_punct4_text(text, enc.offsets, preds)


def reconstruct_punct4_text(text: str, offsets, label_ids, labels=PUNCT4_LABELS) -> str:
    """Pure reconstruction step for PunctuatorJa4Class.restore(): given the
    plain `text` fed to the tokenizer and parallel per-token (start, end)
    char-offset / predicted-label-id sequences (as produced by a
    `tokenizers` fast tokenizer + argmax over the ONNX model's logits),
    insert the predicted marks right after the last character each token
    covers and return the punctuated string."""
    mark_at = [""] * len(text)
    for (start, end), label_id in zip(offsets, label_ids):
        if end <= start:
            continue  # special token (CLS/SEP), offset (0, 0)
        label = labels[label_id]
        if label != "O" and end - 1 < len(mark_at):
            mark_at[end - 1] = label

    out_parts: list[str] = []
    for i, ch in enumerate(text):
        out_parts.append(ch)
        if mark_at[i]:
            out_parts.append(mark_at[i])
    return "".join(out_parts)


# ------------------------------------------------------------------------
if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")

    samples = [
        "明日の会議は午後三時から始まります資料の準備をお願いします",
        "今日めっちゃ疲れたわもう寝る",
        "これって本当に大丈夫なんですか",
    ]

    print("Loading PunctuatorJa...")
    t0 = time.perf_counter()
    punctuator = PunctuatorJa()
    load_ms = (time.perf_counter() - t0) * 1000
    print(f"Model loaded in {load_ms:.1f} ms\n")

    for s in samples:
        t0 = time.perf_counter()
        out = punctuator.restore(s)
        dt_ms = (time.perf_counter() - t0) * 1000
        print(f"IN : {s}")
        print(f"OUT: {out}")
        print(f"latency: {dt_ms:.2f} ms\n")
