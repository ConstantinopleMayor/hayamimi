"""Postprocessing-order and zh-punctuation regression tests.

No real ONNX models are required: the first four tests exercise the
pure-logic wiring of `asr_engine.RoutedASR.transcribe()`'s postprocessing
tail (ITN -> ko spacer -> ja/zh punct -> --replace, last) against a stub
that fakes the decode, and the last two cover the zh-digit cross-regression
with ITN and the real zh punct model (skip-if-missing golden).
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import asr_engine
import itn_cjk
from zh_digit import restore_zh_digits
from zh_space import normalize_zh_spaces


class _Recorder:
    """Fake RoutedASR stand-in for the forced-zh transcribe() tail.

    Records which postprocessing stages actually ran (and their order) and
    can mimic the real zh-punct failure modes:
      - punct_load_fail: the model is missing -> the lazy property returns
        None and the stage is skipped entirely;
      - punct_restore_fail: restore() crashes -> transcribe()'s try/except
        keeps the ITN-ed text.
    """

    forced_lang = "zh"  # --mode single: skip all LID/switch logic
    pinned_tier = "pz"
    last_lang = None
    _pending_lang = None
    _pending_count = 0
    _unavailable = set()
    _closed = False
    _check_open = asr_engine.RoutedASR._check_open
    _itn_overrides = itn_cjk.EMPTY_OVERRIDES

    def __init__(self, text, punct_load_fail=False, punct_restore_fail=False):
        self.text = text
        self.punct_load_fail = punct_load_fail
        self.punct_restore_fail = punct_restore_fail
        self.stages = []

    def _route(self, lang):
        assert lang == "zh"
        return ("fake-rec", "pz")

    def _decode(self, rec, samples, sample_rate):
        return self.text

    @property
    def ko_spacer(self):
        return None  # ko stage skipped (lang is zh)

    @property
    def punct(self):
        return None  # ja stage skipped (lang is zh)

    @property
    def punct_zh(self):
        if self.punct_load_fail:
            return None  # missing model: degrade quietly, like the real property
        return self

    def restore(self, text):
        self.stages.append("restore")
        if self.punct_restore_fail:
            raise RuntimeError("punct_zh restore crashed")
        return text + "。"

    def _replace(self, text, zh=False):
        self.stages.append("replace")
        for wrong, right in getattr(self, "_replacements", ()):
            text = text.replace(wrong, right)
        return text


def _run_transcribe(rec):
    return asr_engine.RoutedASR.transcribe(
        rec, np.zeros(4800, dtype=np.float32), 16000,
        known_lang="zh", live=True)


def test_zh_final_runs_itn_then_punct_zh():
    rec = _Recorder("三千二百个学生")
    result = _run_transcribe(rec)
    # ITN converts 三千二百 -> 3200 first, punct_zh then appends a period:
    assert result["text"] == "3200个学生。"
    assert result["lang"] == "zh"
    assert result["tier"] == "pz"
    # punct_zh.restore ran before --replace (replace is last in the order)
    assert rec.stages == ["restore", "replace"]


def test_zh_final_replace_runs_last_after_punct():
    rec = _Recorder("三千二百个学生")
    rec._replacements = (("学生", "students"),)  # --replace user dictionary
    result = _run_transcribe(rec)
    assert result["text"] == "3200个students。"
    assert rec.stages == ["restore", "replace"]


def test_zh_punct_model_missing_keeps_itn_text():
    rec = _Recorder("三千二百个学生", punct_load_fail=True)
    result = _run_transcribe(rec)
    # ITN ran (三千二百 -> 3200); the punct stage was skipped, not crashed
    assert result["text"] == "3200个学生"
    assert rec.stages == ["replace"]


def test_zh_punct_restore_crash_never_loses_transcription():
    rec = _Recorder("三千二百个学生", punct_restore_fail=True)
    result = _run_transcribe(rec)
    # restore() crashed inside the try/except: ITN output survives untouched
    assert result["text"] == "3200个学生"
    assert rec.stages == ["restore", "replace"]


def test_zh_digit_spaced_percent_forms():
    """Paraformer token spacing wraps the percent digits ("百分之1 . 4"),
    which used to defeat both percent patterns: only the head matched and
    the decimal tail was orphaned -- the final carried "1% . 4"
    (2026-10-18 report, confirmed in the serve log)."""
    assert restore_zh_digits("波及百分之1 . 4的council用户") == "波及1.4%的council用户"
    assert restore_zh_digits("百分之 1 . 4") == "1.4%"  # space after 百分之
    assert restore_zh_digits("百分之1.4") == "1.4%"
    assert restore_zh_digits("百分之一点四") == "1.4%"  # kanji-form regression
    assert restore_zh_digits("百分之 一点 四") == "1.4%"  # kanji + spaces


def test_zh_digit_cross_regression_with_itn():
    # "百分之十七": ITN converts the inner digits (百分之17), then zh_digit
    # restores the percentage -- both must compose cleanly.
    via_itn = itn_cjk.convert("百分之十七", "zh")
    assert via_itn == "百分之17"
    assert restore_zh_digits(via_itn) == "17%"
    # and the pure zh_digit path still handles the un-ITN'd original
    assert restore_zh_digits("百分之十七") == "17%"
    # idioms stay untouched by ITN (conservative)
    assert itn_cjk.convert("一石二鳥", "zh") == "一石二鳥"


def test_zh_repair_drops_spaced_digit_sentence_mark():
    """pz token spacing can wrap the mark in spaces ("4。 12.3"); the
    literal alnum guard sees the spaces, so the spaced-digit check must
    drop the mark there too (same tear as "4。12.3")."""
    from punct_zh import PunctuatorZh

    class _NoJieba:
        def _get_jieba(self):
            raise RuntimeError("jieba intentionally unavailable")

    repair = PunctuatorZh._repair_word_splits
    assert repair(_NoJieba(), "涨到4。 12.3") == "涨到4 12.3"
    assert repair(_NoJieba(), "涨到4 。 12.3") == "涨到4  12.3"
    # a real CJK sentence end stays untouched
    assert repair(_NoJieba(), "开源了。第一张表里对比") == "开源了。第一张表里对比"


def test_false_period_repair_only_drops_internal_phrase_cuts():
    from punct_zh import PunctuatorZh

    repair = PunctuatorZh._repair_false_periods
    assert repair("屏幕上是今天的。主要内容") == "屏幕上是今天的主要内容"
    assert repair("新版。模型专门针对") == "新版模型专门针对"
    assert repair("并免费开放。至10月31日") == "并免费开放至10月31日"
    # A verb/time before a new clause is a real sentence end: do not demote
    # it to a comma. The sentence tail state handles unfinished decodes.
    assert repair("第13天。宣布完整重置") == "第13天。宣布完整重置"
    assert repair("五小时限制。开放dos功能") == "五小时限制。开放dos功能"


def test_terminal_period_is_left_for_sentence_state():
    from punct_zh import PunctuatorZh

    repair = PunctuatorZh._repair_false_periods
    for text in ("第13天。", "新版。", "屏幕上是今天的。", "今天的。",
                 "五小时限制。", "损失大规模。", "今天的资讯。",
                 "主要内容。", "AI早报。", "明天见。", "是的。",
                 "这是对的。"):
        assert repair(text) == text


def test_false_period_repair_keeps_real_sentence_ends():
    from punct_zh import PunctuatorZh

    repair = PunctuatorZh._repair_false_periods
    assert repair("是的。") == "是的。"
    assert repair("这是对的。下一句") == "这是对的。下一句"
    assert repair("第13天。我们继续") == "第13天。我们继续"
    assert repair("新版。我们继续") == "新版。我们继续"


def test_zh_punct_model_golden_skip_if_missing():
    try:
        from punct_zh import PunctuatorZh

        p = PunctuatorZh()
    except Exception:
        pytest.skip("zh punct model not downloaded (download_models.py)")
    out = p.restore("我们都是木头人不会说话不会动")
    assert "，" in out and out.endswith("。")


def test_ct_period_positions_stable_across_number_and_term_spellings():
    try:
        from punct_zh import PunctuatorZh

        p = PunctuatorZh()
    except Exception:
        pytest.skip("zh punct model not downloaded (download_models.py)")

    def marks(text):
        return [ch for ch in text if ch in "，。？！"]

    pairs = [
        ("下午15.310分开会十八点结束", "下午15.30分开会十八点结束"),
        ("OpenAI Codex负责人Tibo在28天活动的第13天宣布完整重置",
         "OpenAI Codex负责人table在28天活动的第13天宣布完整重置"),
        ("新版模型专门针对WorkBuddy等平台进行了优化今天的资讯播送完了明天见",
         "新版模型专门针对workbody等平台进行了优化今天的资讯播送完了明天见"),
        ("这一版是4.1 flash小一它比上一代快了很多",
         "这一版是4.1flash小一它比上一代快了很多"),
    ]
    for a, b in pairs:
        assert marks(p.restore(a)) == marks(p.restore(b)), (a, b)


def test_ct_decimal_spacing_is_cleaned_by_zh_space():
    try:
        from punct_zh import PunctuatorZh

        p = PunctuatorZh()
    except Exception:
        pytest.skip("zh punct model not downloaded (download_models.py)")

    out = normalize_zh_spaces(p.restore("这一版是4.1 flash小一它比上一代快了很多"))
    assert "4.1 flash" in out
    assert "4.1flash" not in out


def test_zh_replace_runs_digit_and_space_normalization():
    # _replace's zh branch is the shared zh tail for finals AND drafts
    # (partial() calls _replace directly): after the user dictionary and
    # zh_digit, zh_space collapses Paraformer's ASCII token spacing.
    class _Stub:
        _replacements = ()

    out = asr_engine.RoutedASR._replace(
        _Stub(), "升级到了4 . 1 flash小一和g r m 5 . 3的状态似乎", zh=True)
    assert out == "升级到了4.1 flash小一和grm 5.3的状态似乎"
    # and zh=False (the ja/ko/en/yue path) must NOT touch the spacing
    out_en = asr_engine.RoutedASR._replace(_Stub(), "released in 2024", zh=False)
    assert out_en == "released in 2024"
