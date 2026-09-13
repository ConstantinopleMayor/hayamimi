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


def test_zh_punct_model_golden_skip_if_missing():
    try:
        from punct_zh import PunctuatorZh

        p = PunctuatorZh()
    except Exception:
        pytest.skip("zh punct model not downloaded (download_models.py)")
    out = p.restore("我们都是木头人不会说话不会动")
    assert "，" in out and out.endswith("。")
