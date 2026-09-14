"""Token-space normalization for Chinese ASR output.

Paraformer-zh tokenizes CJK characters glued together but separates ASCII
tokens with spaces: English letters read out one by one come out as
"g l m", and even a decimal point becomes its own token, so "4.1" is
emitted as "4 . 1". Chinese subtitles never write like that -- this module
restores the conventional form:

  R1  a run of two or more single Latin letters separated by spaces is
      joined into one word (g r m -> grm, A I -> AI). Multi-letter English
      words keep their spacing, so "machine learning" stays two words.
  R2  half-width punctuation wedged between digits loses its spaces
      (4 . 1 -> 4.1, 1 - 2 -> 1-2), and a percent sign glues itself to
      the number in front of it (50 % -> 50%).
  R3  spaces at digit<->letter boundaries go away (4.1 flash -> 4.1flash,
      grm 5 -> grm5). The accepted trade-off: an English phrase like
      "in 2024" merges to "in2024" -- rare inside Chinese speech.

Spaces between CJK and ASCII are deliberately untouched ("的 API 状态"
stays as it is): those are a typographic choice, not tokenizer residue.

Applied in RoutedASR._replace()'s zh branch, i.e. as the final stage of
the shared postprocessing used by finals AND drafts (partial() runs
_replace too, without ITN/punctuation). Pure Python, zero dependencies,
idempotent.
"""

from __future__ import annotations

import re

# R1: >=2 single Latin letters separated by single spaces. Hand-rolled
# lookbehinds instead of \b because CJK chars are \w in Python's unicode
# regexes, so "\b" would refuse to match a letter run glued to Chinese
# ("和g r m" has no boundary between 和 and g).
_LETTER_RUN_RE = re.compile(r"(?<![A-Za-z])[A-Za-z](?: [A-Za-z])+(?![A-Za-z])")

# R2: half-width punctuation wedged between two digits (decimal points,
# ranges, ratios ...). CJK on either side must not trigger it.
_PUNCT_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)\s*([.,:;!?%+\-*/=])\s*(?=\d)")
# R2b: a spaced percent sign belongs to the digit in front of it even when
# what follows is CJK ("50 % 的" -> "50% 的").
_PCT_RE = re.compile(r"(?<=\d)\s+([%‰])")

# R3: single spaces at digit<->letter boundaries.
_DIGIT_LETTER_RE = re.compile(r"(?<=\d) (?=[A-Za-z])")
_LETTER_DIGIT_RE = re.compile(r"(?<=[A-Za-z]) (?=\d)")


def normalize_zh_spaces(text: str) -> str:
    """Return `text` with Paraformer ASCII token-spacing collapsed to the
    way a Chinese subtitle would write it. Text without spaces is
    returned untouched (fast path)."""
    if not text or " " not in text:
        return text
    s = _LETTER_RUN_RE.sub(lambda m: m.group(0).replace(" ", ""), text)
    s = _PUNCT_BETWEEN_DIGITS_RE.sub(r"\1", s)
    s = _PCT_RE.sub(r"\1", s)
    s = _DIGIT_LETTER_RE.sub("", s)
    s = _LETTER_DIGIT_RE.sub("", s)
    return s


if __name__ == "__main__":
    for t in [
        "升级到了4 . 1 flash小一和g r m 5 . 3的状态似乎。",
        "machine learning 模型很能打",
        "这个 API 很好用",
        "版本5 . 3耗时50 % 左右",
        "数到 1 2 3 就停",
    ]:
        print(t, "->", normalize_zh_spaces(t))
