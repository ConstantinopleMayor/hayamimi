"""Fast regression tests for the model-free logic.

Run with: .venv/Scripts/python -m pytest tests -q
No ASR/MT models are loaded; these guard the plumbing that past iterations
broke or nearly broke (preroll bleed, replacement parsing, digit guard,
routing table consistency).
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from realtime_transcribe import (AudioHistory, PREROLL_S, digits_consistent,
                                 translate_by_sentence)
import asr_engine


class FakeTranslator:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def translate(self, text):
        self.calls.append(text)
        return self.mapping.get(text, text)


# ---- digit guard -----------------------------------------------------------

def test_digits_pass_when_source_has_no_ascii_digits():
    assert digits_consistent("午後三時から始まります", "It starts at 3 pm")


def test_digits_pass_on_exact_and_superset():
    assert digits_consistent("3時です", "It is 3:00")
    assert digits_consistent("500円です", "It costs 500 yen")


def test_digits_fail_on_mangled_number():
    assert not digits_consistent("500万円です", "It costs 5 pounds")


# ---- sentence-split translation -------------------------------------------

def test_translate_by_sentence_splits_on_terminators():
    tr = FakeTranslator({"こんにちは。": "Hello.", "元気ですか？": "How are you?"})
    assert translate_by_sentence(tr, "こんにちは。元気ですか？") == "Hello. How are you?"
    assert tr.calls == ["こんにちは。", "元気ですか？"]


def test_translate_by_sentence_drops_untranslated():
    tr = FakeTranslator({})
    assert translate_by_sentence(tr, "こんにちは。") == ""


def test_translate_by_sentence_number_guard_falls_back():
    tr = FakeTranslator({"500万円です。": "It costs 5 pounds."})
    # mangled number -> per-sentence fallback -> treated as untranslated
    assert translate_by_sentence(tr, "500万円です。") == ""


# ---- AudioHistory / preroll -----------------------------------------------

def test_preroll_prepends_context():
    h = AudioHistory(sample_rate=100)  # tiny sr keeps the math readable
    h.push(np.arange(1000, dtype=np.float32))
    seg = np.zeros(10, dtype=np.float32)
    out = h.with_preroll(seg_start=500, seg_samples=seg)
    assert len(out) == int(PREROLL_S * 100) + 10
    assert out[0] == 500 - int(PREROLL_S * 100)


def test_preroll_never_bleeds_into_previous_segment():
    h = AudioHistory(sample_rate=100)
    h.push(np.arange(1000, dtype=np.float32))
    h.with_preroll(seg_start=300, seg_samples=np.zeros(100, dtype=np.float32))  # ends at 400
    out = h.with_preroll(seg_start=450, seg_samples=np.zeros(10, dtype=np.float32))
    assert out[0] == 400  # clamped to the previous segment's end, not 450-preroll


def test_preroll_respects_rolling_buffer_offset():
    h = AudioHistory(sample_rate=100, keep_s=5.0)  # keeps 500 samples
    h.push(np.arange(2000, dtype=np.float32))
    assert h.offset == 1500
    out = h.with_preroll(seg_start=1600, seg_samples=np.zeros(10, dtype=np.float32))
    assert out[0] == 1600 - int(PREROLL_S * 100)


# ---- replacement dictionary -----------------------------------------------

def test_load_replacements_formats(tmp_path):
    f = tmp_path / "dict.txt"
    f.write_text("# comment\nA=B\nC\tD\nE→F\n\n", encoding="utf-8")
    pairs = asr_engine._load_replacements(str(f))
    assert pairs == [("A", "B"), ("C", "D"), ("E", "F")]


def test_load_replacements_empty_path():
    assert asr_engine._load_replacements("") == []


# ---- routing table consistency --------------------------------------------

def test_routing_sets_are_disjoint():
    sets = [asr_engine.RZ_LANGS, asr_engine.PARA_LANGS, asr_engine.SV_LANGS, asr_engine.V3_LANGS]
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            assert not (sets[i] & sets[j]), f"overlap between routing sets {i} and {j}"


def test_expected_language_homes():
    assert "ja" in asr_engine.RZ_LANGS
    assert "en" in asr_engine.V3_LANGS       # rz English is ALL-CAPS; must stay on v3
    assert "yue" in asr_engine.SV_LANGS      # LID can't detect yue; sv arbitrates
    assert "zh" in asr_engine.PARA_LANGS


# ---- script correction matrix ----------------------------------------------

def test_script_correction_matrix():
    f = asr_engine.script_corrected_lang
    assert f("en", "選手紹介のときのブルーカーペット") == "ja"   # CJK under latin tag
    assert f("ja", "NICETOMEETYOUEVERYONE") == "en"             # ASCII caps under ja tag
    assert f("vi", "안녕하세요 반갑습니다 여러분") == "ko"        # hangul under wrong tag
    assert f("yue", "值得在这座迷人村庄") == "yue"               # CJK under CJK tag: keep
    assert f("ja", "はい") == "ja"                               # short: keep
    assert f("en", "Nice to meet you.") == "en"


def test_has_kana():
    assert asr_engine._has_kana("漢字とかなの文")
    assert not asr_engine._has_kana("值得在这座迷人村庄")
