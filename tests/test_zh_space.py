"""Unit tests for zh_space.normalize_zh_spaces.

Pure-function tests, no models: these pin the R1/R2/R3 rules and the
two deliberate non-goals (multi-letter English words and CJK<->ASCII
spaces keep theirs).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from zh_space import normalize_zh_spaces


def test_user_report_example():
    # The regression from the bug report: letters read out one by one and
    # a decimal point emitted as separate tokens ("GLM 5.3" -> "g r m 5 . 3").
    src = "升级到了4 . 1 flash小一和g r m 5 . 3的状态似乎。"
    assert normalize_zh_spaces(src) == "升级到了4.1flash小一和grm5.3的状态似乎。"


def test_letter_run_glued_to_cjk_merges():
    # no \b before the run: CJK chars are \w in unicode regexes
    assert normalize_zh_spaces("和g r m的模型") == "和grm的模型"
    # uppercase acronyms spelled out
    assert normalize_zh_spaces("G P U占有率") == "GPU占有率"


def test_multi_letter_words_keep_spacing():
    # R1 only merges SINGLE letters; a two-word English phrase is intact
    assert normalize_zh_spaces("machine learning模型") == "machine learning模型"
    # I am: the lookahead needs each piece to be exactly one letter
    assert normalize_zh_spaces("I am一个普通人") == "I am一个普通人"


def test_cjk_ascii_spaces_preserved():
    # the user complaint was intra-ASCII tokenization space, not CJK spacing
    assert normalize_zh_spaces("这个 API 很好用") == "这个 API 很好用"


def test_decimal_percent_and_range():
    assert normalize_zh_spaces("版本5 . 3") == "版本5.3"
    assert normalize_zh_spaces("提升50 % 左右") == "提升50% 左右"  # % glues left, CJK side space stays
    assert normalize_zh_spaces("耗时 1 - 2小时") == "耗时 1-2小时"
    # a dotted version chain collapses fully
    assert normalize_zh_spaces("版本8 . 0 . 1") == "版本8.0.1"


def test_digit_runs_not_merged():
    # R1 is letters only: enumerated digits keep their spaces
    assert normalize_zh_spaces("数到 1 2 3 就停") == "数到 1 2 3 就停"


def test_digit_letter_boundary_tradeoff():
    # documented side effect accepted during planning: an English word
    # followed by a number glues ("in 2024" -> "in2024")
    assert normalize_zh_spaces("released in 2024年") == "released in2024年"


def test_idempotent_and_fast_path():
    src = "升级到了4 . 1 flash小一和g r m 5 . 3的状态似乎。"
    once = normalize_zh_spaces(src)
    assert normalize_zh_spaces(once) == once
    # no-space fast path returns the same object, untouched text too
    assert normalize_zh_spaces("今天天气不错") == "今天天气不错"
    assert normalize_zh_spaces("") == ""
