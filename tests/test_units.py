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

from realtime_transcribe import (AudioHistory, PartialPrinter, PREROLL_S, Refiner,
                                 SemanticSplitter, apply_translation_spec,
                                 digits_consistent, translate_by_sentence,
                                 _boundary_overlap, _coverage_ok,
                                 _join_final_texts)
import asr_engine
import realtime_transcribe
import translate_m2m


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


# ---- hotword encodability check (issue #1) ---------------------------------

def test_cjkchar_units_splits_cjk_chars_and_keeps_ascii_words():
    assert asr_engine._cjkchar_units("屈足湖") == ["屈", "足", "湖"]
    assert asr_engine._cjkchar_units("GANKE FES") == ["GANKE", "FES"]
    assert asr_engine._cjkchar_units("屈足 FES") == ["屈", "足", "FES"]


def test_check_hotwords_encodable_all_missing(tmp_path):
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("<blk> 0\n<unk> 1\n▁ƊĢĥ 2\n", encoding="utf-8")
    hw = tmp_path / "hotwords.txt"
    hw.write_text("屈足湖\nGANKE FES\n# comment\n\n", encoding="utf-8")
    total, bad = asr_engine.check_hotwords_encodable(str(hw), str(tokens))
    assert (total, bad) == (2, 2)


def test_check_hotwords_encodable_some_found(tmp_path):
    tokens = tmp_path / "tokens.txt"
    tokens.write_text("<blk> 0\n屈 1\n足 2\n", encoding="utf-8")
    hw = tmp_path / "hotwords.txt"
    hw.write_text("屈足\n屈足湖\n", encoding="utf-8")
    total, bad = asr_engine.check_hotwords_encodable(str(hw), str(tokens))
    assert (total, bad) == (2, 1)  # "屈足" fully found, "屈足湖" missing 湖


def test_check_hotwords_encodable_empty_path():
    assert asr_engine.check_hotwords_encodable("", "tokens.txt") == (0, 0)


# ---- M2M-100 target acceptance / validation tiers ---------------------------

# These read the real model's vocabulary file (models/ is untracked), so they
# only run where the M2M-100 model has been downloaded -- not on minimal CI.
_m2m_vocab_available = translate_m2m.is_supported_target("ja")
needs_m2m_vocab = pytest.mark.skipif(
    not _m2m_vocab_available,
    reason="M2M-100 model vocabulary not downloaded (models/ absent)",
)


@needs_m2m_vocab
def test_is_supported_target_accepts_known_m2m100_codes():
    # __zh__ / __ko__ / __es__ / __fr__ all exist in the model's own vocabulary.
    assert translate_m2m.is_supported_target("zh")
    assert translate_m2m.is_supported_target("ko")
    assert translate_m2m.is_supported_target("es")
    assert translate_m2m.is_supported_target("fr")


@needs_m2m_vocab
def test_is_supported_target_rejects_unknown_code():
    assert not translate_m2m.is_supported_target("xx")
    assert not translate_m2m.is_supported_target("not-a-lang-code")


def test_is_supported_target_missing_model_dir_returns_false():
    # A bad model_dir must fail closed (no vocab file to check against), not raise.
    assert not translate_m2m.is_supported_target("zh", model_dir="/no/such/dir")


@needs_m2m_vocab
def test_validated_targets_are_a_subset_of_supported():
    for lang in translate_m2m.VALIDATED_TARGETS:
        assert translate_m2m.is_supported_target(lang), f"{lang} is VALIDATED but not accepted by the model"


def test_validated_targets_tier_matches_measured_set():
    # zh/ko/es have measured chrF (docs/TRANSLATE_M2M.md); an arbitrary M2M-100
    # target with no measurement (e.g. fr) must stay out of the validated tier.
    assert {"zh", "ko", "es"} <= translate_m2m.VALIDATED_TARGETS
    assert "fr" not in translate_m2m.VALIDATED_TARGETS


def test_translator_rejects_unsupported_target_before_loading_model():
    with pytest.raises(ValueError):
        translate_m2m.TranslatorM2M("xx")


def test_default_beam_size_used_for_unvalidated_targets():
    # es/fr etc. have no dedicated entry in BEAM_SIZE_BY_TARGET; they must fall
    # back to DEFAULT_BEAM_SIZE rather than KeyError.
    assert translate_m2m.BEAM_SIZE_BY_TARGET.get("fr", translate_m2m.DEFAULT_BEAM_SIZE) == translate_m2m.DEFAULT_BEAM_SIZE
    assert "es" not in translate_m2m.BEAM_SIZE_BY_TARGET  # measured via the fallback, not a dedicated tuning


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


# ---- sticky LID hysteresis --------------------------------------------------

def test_sticky_first_utterance_has_no_last_lang_yet():
    # last_lang=None (session bootstrap), no SenseVoice probe hint: even a
    # bootstrap detection must accumulate switch_confirm repeats before the
    # session commits to it -- it is no longer instant-accepted (a real-mic
    # incident: an uncontested first-segment whisper-tiny misfire used to
    # seed the whole session with a wrong, sometimes SenseVoice-unarbitrable,
    # language). Meanwhile it decodes using its own candidate since no probe
    # hint was given.
    lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
        "en", None, 3.0, 2.0, 2, None, 0)
    assert (lang, suppress, pend, cnt) == ("en", False, "en", 1)


def test_sticky_bootstrap_prefers_probe_language_over_unarbitrable_guess():
    # Real-mic accident scenario (exact repro, testdata/switch_scenario.wav):
    # whisper-tiny says "ru" (a language SenseVoice can't arbitrate on its
    # own) on the 1.9s first segment of a session; the caller's SenseVoice
    # probe for this exact audio says "ja". This segment is short of the
    # default 2.0s guard, so it's held/suppressed like any short candidate,
    # but critically it decodes using the probe's language, not "ru" --
    # the fix that closes the collapse (previously: instant-accept "ru").
    lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
        "ru", None, 1.9, 2.0, 2, None, 0, bootstrap_probe_lang="ja")
    assert lang == "ja"
    assert suppress is True
    assert (pend, cnt) == (None, 0)  # too short to advance the switch-confirm counter


def test_sticky_bootstrap_prefers_probe_language_for_long_unarbitrable_guess():
    # Same disagreement, but long enough to count toward switch_confirm:
    # still decodes via the probe's language while "ru" accumulates.
    lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
        "ru", None, 2.5, 2.0, 2, None, 0, bootstrap_probe_lang="ja")
    assert lang == "ja"
    assert suppress is False
    assert (pend, cnt) == ("ru", 1)


def test_sticky_bootstrap_non_sv_lang_confirms_after_repeats():
    # Two consecutive long "fr" detections at bootstrap confirm "fr" as the
    # session language -- legitimate European-language sessions still get
    # through, just with the same switch_confirm delay as any other switch
    # (a probe hint, if any, only covers the held segments in between).
    lang1, suppress1, pend1, cnt1 = asr_engine.resolve_sticky_lang(
        "fr", None, 5.0, 2.0, 2, None, 0, bootstrap_probe_lang="ja")
    assert lang1 == "ja"
    assert (pend1, cnt1) == ("fr", 1)

    lang2, suppress2, pend2, cnt2 = asr_engine.resolve_sticky_lang(
        "fr", None, 5.0, 2.0, 2, pend1, cnt1, bootstrap_probe_lang="ja")
    assert lang2 == "fr"
    assert suppress2 is False
    assert (pend2, cnt2) == (None, 0)


def test_sticky_same_lang_resets_pending():
    lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
        "ja", "ja", 3.0, 2.0, 2, "en", 1)
    assert (lang, suppress, pend, cnt) == ("ja", False, None, 0)


def test_sticky_single_misfire_is_held_not_switched():
    # A lone babble-noise misfire to "en" while the session is "ja" must be
    # held at "ja" for a long (>= min_switch_s) segment, and the omni
    # fallback must NOT be suppressed (it's presumed real speech, just
    # decoded under the wrong tier's model).
    lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
        "en", "ja", 3.0, 2.0, 2, None, 0)
    assert lang == "ja"
    assert suppress is False
    assert (pend, cnt) == ("en", 1)


def test_sticky_short_misfire_suppresses_fallback():
    # Same, but the segment is shorter than min_switch_s (jingle/SFX case):
    # held language's empty decode must not be resurrected by omni.
    lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
        "en", "ja", 1.0, 2.0, 2, None, 0)
    assert lang == "ja"
    assert suppress is True


def test_sticky_confirmed_switch_after_two_consecutive_detections():
    # First "en" sighting: held. Second consecutive "en" sighting: switch.
    lang1, _, pend1, cnt1 = asr_engine.resolve_sticky_lang(
        "en", "ja", 3.0, 2.0, 2, None, 0)
    assert lang1 == "ja" and (pend1, cnt1) == ("en", 1)

    lang2, suppress2, pend2, cnt2 = asr_engine.resolve_sticky_lang(
        "en", "ja", 3.0, 2.0, 2, pend1, cnt1)
    assert lang2 == "en"
    assert suppress2 is False
    assert (pend2, cnt2) == (None, 0)


def test_sticky_alternating_misfires_never_accumulate():
    # Two DIFFERENT wrong-language misfires in a row must not add up to a
    # switch -- each one resets the pending candidate.
    lang1, _, pend1, cnt1 = asr_engine.resolve_sticky_lang(
        "en", "ja", 3.0, 2.0, 2, None, 0)
    assert (pend1, cnt1) == ("en", 1)

    lang2, _, pend2, cnt2 = asr_engine.resolve_sticky_lang(
        "zh", "ja", 3.0, 2.0, 2, pend1, cnt1)
    assert lang2 == "ja"          # still held at the session language
    assert (pend2, cnt2) == ("zh", 1)  # candidate reset to the new guess


def test_partial_forced_lang_routes_directly_without_lid_or_sv_probe():
    # --mode single (forced_lang set): partial() must never touch LID or the
    # SenseVoice probe -- it should route straight to the forced language,
    # exactly like transcribe() does. Regression for a bug where partial()
    # ignored forced_lang entirely and ran the full sticky/SenseVoice probe
    # path on every draft.
    class _Stub:
        forced_lang = "ko"
        pinned_tier = "sv"
        last_lang = "en"  # deliberately different, to prove it's ignored
        # transcribe()/partial() now refuse to run on a closed engine
        # (RoutedASR.close()); borrowed rather than faked so this stub
        # keeps checking whatever the real guard checks.
        _closed = False
        _check_open = asr_engine.RoutedASR._check_open
        # draft punctuation rides the same borrowed-method pattern: for the
        # sv tier the helper short-circuits (lang "ko" matches no pz/rz arm)
        # and returns the text untouched, without touching any punctuator
        _draft_punct = asr_engine.RoutedASR._draft_punct

        def _route(self, lang):
            assert lang == "ko"
            return ("ko-recognizer", "sv")

        def _decode(self, rec, samples, sample_rate):
            assert rec == "ko-recognizer"
            return "raw text"

        def _replace(self, text, zh=False):
            return text

        def _get(self, name):
            raise AssertionError("partial() must not touch the SenseVoice probe when forced_lang is set")

        def _decode_full(self, *a, **kw):
            raise AssertionError("partial() must not touch the SenseVoice probe when forced_lang is set")

    stub = _Stub()
    result = asr_engine.RoutedASR.partial(stub, np.zeros(1600, dtype=np.float32), 16000)
    assert result == "raw text"


class _MarkPunct:
    """Stand-in punctuator whose restore marks that it ran by appending a
    period, the way the real CT restore closes a finished clause."""

    def restore(self, text):
        if text.endswith("。"):
            return text
        return text + "。"


class _BoomPunct:
    def restore(self, text):
        raise AssertionError("punctuator must not run for non-zh drafts")


def test_draft_punct_cleans_ct_token_spacing():
    # CT-Transformer separates a decimal point from its digits ("4 . 1")
    # in its output. The final path cleans that in _replace; drafts used to
    # return the CT spacing verbatim, making the in-progress line flicker
    # between 4.1 and 4 . 1.
    class _SpacedCT:
        def restore(self, text):
            return "升级到了4 . 1 flash。"

    class _Stub:
        punct_zh = _SpacedCT()
        _draft_punct = asr_engine.RoutedASR._draft_punct

    out = asr_engine.RoutedASR._draft_punct(_Stub(), "升级到了4.1 flash", "zh", "pz")
    assert out == "升级到了4.1 flash。"


def test_draft_punct_omni_mistagged_cjk_draft_gets_zh_conversion_and_punct():
    # 2026-10-18 report: a Mandarin segment whose English-name opening made
    # whisper-tiny's 2s LID emit an unsupported tag routed its drafts to the
    # omnilingual CTC fallback -- bare text, no digit conversion, no marks,
    # so the sentence-boundary flush never fired and the card showed one
    # run-on bare draft until the segment closed ~13s later. The final was
    # fine (transcribe()'s script-correction re-decode); the draft path had
    # no equivalent. Han-dominant, kana-free omni drafts must now be
    # converted and punctuated like any zh draft.
    from zh_digit import restore_zh_digits

    class _Stub:
        punct_zh = _MarkPunct()
        _draft_punct = asr_engine.RoutedASR._draft_punct

        def _replace(self, text, zh=False):
            assert zh is True, "the omni arm must run the zh transformations"
            return restore_zh_digits(text)

    raw = "免费开放只十月三十一日d四前表示已经优化了今天的咨讯"
    out = asr_engine.RoutedASR._draft_punct(_Stub(), raw, "ru", "omni")
    assert "10月31日" in out
    assert out.endswith("。")


def test_draft_punct_omni_non_zh_drafts_untouched():
    # English and kana-bearing omni drafts are not zh; the punctuator must
    # stay out of them, as must the zh transformations.

    class _Stub:
        punct_zh = _BoomPunct()
        _draft_punct = asr_engine.RoutedASR._draft_punct

        def _replace(self, text, zh=False):
            raise AssertionError("zh transformations must not run")

    for raw, tag in [("hello world this is a live test", "ru"),
                     ("明日の会議で発表します", "ru")]:
        assert (asr_engine.RoutedASR._draft_punct(_Stub(), raw, tag, "omni")
                == raw), tag


def test_draft_punct_omni_cjk_without_punct_zh_untouched():
    # Minimal install without the zh punctuator: the omni arm must skip
    # rather than crash (the surrounding try/except would also catch it,
    # but the draft must stay byte-identical either way).

    class _Stub:
        punct_zh = None
        _draft_punct = asr_engine.RoutedASR._draft_punct

        def _replace(self, text, zh=False):
            raise AssertionError("zh transformations must not run")

    raw = "免费开放只十月三十一日"
    assert asr_engine.RoutedASR._draft_punct(_Stub(), raw, "ru", "omni") == raw


def test_cjk_dominant_thresholds():
    # Same 0.3 han fraction and kana exclusion as script_corrected_lang's
    # script contradiction rule.
    assert asr_engine._cjk_dominant("免费开放只十月三十一日d四前表示") is True
    assert asr_engine._cjk_dominant("hello world this is a test") is False
    assert asr_engine._cjk_dominant("明日の会議で発表します") is False  # kana
    assert asr_engine._cjk_dominant("ok嗯") is False  # < 4 letters
    assert asr_engine._cjk_dominant("") is False
    # reset_session() only touches plain attributes (no model calls), so it
    # can be exercised against a bare stand-in without loading any models.
    class _Stub:
        pass

    stub = _Stub()
    stub.last_lang = "ja"
    stub._pending_lang = "en"
    stub._pending_count = 1
    asr_engine.RoutedASR.reset_session(stub)
    assert stub.last_lang is None
    assert stub._pending_lang is None
    assert stub._pending_count == 0


def test_sticky_switch_confirm_one_disables_hysteresis():
    # switch_confirm=1 reproduces "switch immediately" (pre-hysteresis
    # behaviour), useful as a regression anchor for responsiveness.
    lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
        "en", "ja", 3.0, 2.0, 1, None, 0)
    assert lang == "en"
    assert (pend, cnt) == (None, 0)


def test_switch_confirm_one_and_guard_zero_fully_disable_the_lock():
    # --lid-switch-confirm 1 --lang-switch-guard 0: the combination that
    # should turn the sticky-LID lock fully off. A single misfire to "zh"
    # (even a very short one -- guard=0 means min_switch_s=0.0, so no
    # detection is ever "too short" to count) switches immediately, and the
    # very next detection back to "ja" switches back immediately too. This
    # is the escape hatch for validating whether the lock -- not just its
    # tuning -- is the right call for a given setup.
    lang1, suppress1, pend1, cnt1 = asr_engine.resolve_sticky_lang(
        "zh", "ja", 0.3, 0.0, 1, None, 0)
    assert lang1 == "zh"
    assert suppress1 is False
    assert (pend1, cnt1) == (None, 0)

    lang2, suppress2, pend2, cnt2 = asr_engine.resolve_sticky_lang(
        "ja", "zh", 0.3, 0.0, 1, pend1, cnt1)
    assert lang2 == "ja"
    assert suppress2 is False
    assert (pend2, cnt2) == (None, 0)


# ---- --lang-switch-guard actually gates switching (issue #2) ---------------

def test_sticky_short_detection_never_advances_pending_count():
    # A detection shorter than min_switch_s must not count toward
    # switch_confirm at all -- otherwise raising --lang-switch-guard would
    # have no effect on whether the session switches (issue #2).
    lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
        "zh", "ja", 1.9, 10.0, 2, None, 0)
    assert lang == "ja"
    assert suppress is True
    assert (pend, cnt) == (None, 0)  # no candidate accumulated


def test_sticky_short_detections_never_confirm_a_switch():
    # Reporter's scenario (issue #2): a ja-only session hit by repeated
    # short zh misfires under a large guard must stay on ja no matter how
    # many short misfires land in a row, because none of them ever advance
    # the confirmation counter.
    lang, pend, cnt = "ja", None, 0
    for speech_s in (9.1, 1.9, 1.5, 1.5):
        lang, suppress, pend, cnt = asr_engine.resolve_sticky_lang(
            "zh", "ja", speech_s, 10.0, 2, pend, cnt)
        assert lang == "ja"
        assert suppress is True
        assert (pend, cnt) == (None, 0)


def test_sticky_short_detection_does_not_reset_a_real_candidate():
    # A long (real) candidate detection starts accumulating...
    lang1, _, pend1, cnt1 = asr_engine.resolve_sticky_lang(
        "en", "ja", 3.0, 2.0, 2, None, 0)
    assert (pend1, cnt1) == ("en", 1)

    # ...and a short, unrelated blip must not wipe it out.
    lang2, suppress2, pend2, cnt2 = asr_engine.resolve_sticky_lang(
        "zh", "ja", 0.5, 2.0, 2, pend1, cnt1)
    assert lang2 == "ja"
    assert suppress2 is True
    assert (pend2, cnt2) == ("en", 1)  # unchanged

    # The original candidate can still confirm on its next long detection.
    lang3, suppress3, pend3, cnt3 = asr_engine.resolve_sticky_lang(
        "en", "ja", 3.0, 2.0, 2, pend2, cnt2)
    assert lang3 == "en"
    assert (pend3, cnt3) == (None, 0)


# ---- dual-LID switch confirmation (docs/LID.md) ----------------------------

def test_sv_lid_tag_normalizes_bracketed_tag():
    assert asr_engine.sv_lid_tag("<|ja|>") == "ja"
    assert asr_engine.sv_lid_tag("<|yue|>") == "yue"
    assert asr_engine.sv_lid_tag("<|unk|>") == ""


def test_dual_confirm_same_lang_is_a_noop():
    lang, switched = asr_engine.resolve_dual_confirm("ja", "ja", 3.0, "ja")
    assert (lang, switched) == ("ja", False)


def test_dual_confirm_bootstrap_trusts_probe_over_whisper_misfire():
    # LID.md real-mic accident scenario: session start, whisper-tiny says
    # "zh" (wrong), SenseVoice's own probe says "ja" (right) -- the very
    # first segment must decode as "ja", not "zh".
    lang, switched = asr_engine.resolve_dual_confirm("zh", None, 3.0, "ja")
    assert lang == "ja"
    assert switched is False  # the two LIDs disagreed; not a confirmed agreement


def test_dual_confirm_bootstrap_agreement_marks_switched():
    lang, switched = asr_engine.resolve_dual_confirm("en", None, 3.0, "en")
    assert (lang, switched) == ("en", True)


def test_dual_confirm_holds_current_lang_on_disagreement():
    # LID.md scenario: a ja-only session hit by a whisper-tiny "en" misfire;
    # SenseVoice's probe still says "ja" -> stays on "ja", no switch.
    lang, switched = asr_engine.resolve_dual_confirm("en", "ja", 1.5, "ja")
    assert lang == "ja"
    assert switched is False


def test_dual_confirm_switches_immediately_on_agreement():
    # LID.md scenario: whisper-tiny and SenseVoice both say "en" -> switch
    # immediately, no length or repeat-count gate needed (both LIDs agreeing
    # measured 85-98% accurate at every length in docs/LID.md table 3).
    lang, switched = asr_engine.resolve_dual_confirm("en", "ja", 1.0, "en")
    assert lang == "en"
    assert switched is True


def test_dual_confirm_ignores_sub_probe_length_even_on_agreement():
    lang, switched = asr_engine.resolve_dual_confirm("en", "ja", 0.3, "en")
    assert lang == "ja"
    assert switched is False


def test_dual_confirm_zh_whisper_yue_sensevoice_switches_to_yue():
    # whisper-tiny can never emit "yue" (it folds Cantonese into "zh"), so a
    # "zh"-vs-"yue" split between the two LIDs on the same audio is whisper's
    # only possible way of "agreeing" with a yue probe. Must resolve to
    # "yue", not stay stuck on the current language or fall back to "zh".
    lang, switched = asr_engine.resolve_dual_confirm("zh", "en", 3.0, "yue")
    assert (lang, switched) == ("yue", True)


def test_transcribe_bootstrap_too_short_decodes_but_does_not_seed_last_lang():
    # A too-short (<MIN_PROBE_S) segment opening a brand new session (e.g. a
    # jingle/SFX misfire) must still get a best-effort decode, but must NOT
    # seed self.last_lang -- otherwise a single noise blip on segment 1
    # would lock the whole session onto whatever language it happened to
    # guess, contradicting resolve_dual_confirm's own "never confirms a
    # switch" docstring for too-short candidates.
    class _Stub:
        forced_lang = None
        pinned_tier = None
        dual_confirm = True
        last_lang = None
        _pending_lang = None
        _pending_count = 0
        _unavailable = set()
        # transcribe()/partial() now refuse to run on a closed engine
        # (RoutedASR.close()); borrowed rather than faked so this stub
        # keeps checking whatever the real guard checks.
        _closed = False
        _check_open = asr_engine.RoutedASR._check_open

        def _decode_full(self, rec, samples, sample_rate):
            return ("hi", "<|en|>0.9")

        def _get(self, name):
            return "sv-recognizer"

        _sv_probe = asr_engine.RoutedASR._sv_probe

        def _route(self, lang):
            assert lang == "en"
            return ("en-recognizer", "v3")

        def _decode(self, rec, samples, sample_rate):
            return "hi there"

        def _replace(self, text, zh=False):
            return text

    stub = _Stub()
    result = asr_engine.RoutedASR.transcribe(
        stub, np.zeros(4800, dtype=np.float32), 16000,
        known_lang="en", speech_s=0.3, live=True)
    assert result["text"] == "hi there"
    assert result["lang"] == "en"
    # the decode succeeded, but the bootstrap candidate was too short to
    # confirm -- the session must remain "no language established yet"
    assert stub.last_lang is None


def test_transcribe_ignores_empty_and_tiny_audio_before_loading_a_model():
    # A VAD flush can hand the finalizer zero or a handful of samples during
    # silence. Omni's native CTC graph crashes the whole process on an empty
    # waveform (0xC0000094) and raises on a few samples, so transcribe() must
    # return before routing/probing/loading anything.
    class _Stub:
        forced_model = "omni"
        forced_lang = None
        pinned_tier = "omni"
        last_lang = None
        _closed = False
        _check_open = asr_engine.RoutedASR._check_open

        def _decode_pinned(self, samples, sample_rate):
            raise AssertionError("empty/short audio must not reach the decoder")

        def _get(self, name):
            raise AssertionError("empty/short audio must not load a model")

    for n in (0, 160, 800, 1599):
        result = asr_engine.RoutedASR.transcribe(
            _Stub(), np.zeros(n, dtype=np.float32), 16000, live=True)
        assert result["text"] == ""
        assert result["tier"] == "omni"
        assert result["text_source"] == "omni"
        assert result["sv_timestamps"] == []


def test_decode_full_meta_short_circuits_empty_audio():
    class _Rec:
        def create_stream(self):
            raise AssertionError("empty/short audio must not create a stream")

    for n in (0, 160, 800, 1599):
        assert asr_engine.RoutedASR._decode_full_meta(
            _Rec(), np.zeros(n, dtype=np.float32), 16000) == ("", "", [])


def test_sv_probe_retains_timestamps_without_extra_decode():
    class _Stub:
        def _get(self, name):
            assert name == "sv"
            return "sv-recognizer"

        def _decode_full_meta(self, rec, samples, sample_rate):
            return ("你好，世界。", "<|zh|>0.9", [0.1, 0.2, 0.4])

        _sv_probe = asr_engine.RoutedASR._sv_probe

    probe, _ms = asr_engine.RoutedASR._sv_probe(
        _Stub(), None, np.zeros(1600, dtype=np.float32), 16000)
    assert probe == ("你好，世界。", "<|zh|>0.9", [0.1, 0.2, 0.4])


def test_sv_probe_two_value_test_stub_gets_empty_timestamps():
    class _Stub:
        def _get(self, name):
            return "sv-recognizer"

        def _decode_full(self, rec, samples, sample_rate):
            return ("hello", "<|en|>0.9")

        _sv_probe = asr_engine.RoutedASR._sv_probe

    probe, _ms = asr_engine.RoutedASR._sv_probe(
        _Stub(), None, np.zeros(1600, dtype=np.float32), 16000)
    assert probe == ("hello", "<|en|>0.9", [])


def test_transcribe_bootstrap_zh_yue_reuses_single_sv_probe_decode():
    # A brand-new session (last_lang=None) where whisper-tiny says "zh" but
    # SenseVoice's own probe says "yue" touches THREE potential SenseVoice
    # probe call sites in one transcribe() call: the bootstrap confirmation,
    # the dual-LID switch-confirmation check, and the ko/yue reuse check in
    # the decode section. RoutedASR._sv_probe must memoize across all three
    # so the actual SenseVoice decode only runs once per segment.
    call_count = 0

    class _Stub:
        forced_lang = None
        pinned_tier = None
        dual_confirm = True
        last_lang = None
        _pending_lang = None
        _pending_count = 0
        _unavailable = set()
        # transcribe()/partial() now refuse to run on a closed engine
        # (RoutedASR.close()); borrowed rather than faked so this stub
        # keeps checking whatever the real guard checks.
        _closed = False
        _check_open = asr_engine.RoutedASR._check_open
        # postprocessing order (ITN -> ko spacer -> punct -> --replace last)
        # reads these on the stub surface; None disables each stage.
        _itn_overrides = asr_engine.itn_cjk.EMPTY_OVERRIDES
        ko_spacer = None
        punct = None

        def _decode_full_meta(self, rec, samples, sample_rate):
            nonlocal call_count
            call_count += 1
            return ("some text", "<|yue|>0.9", [0.1, 0.2])

        def _get(self, name):
            assert name == "sv"
            return "sv-recognizer"

        _sv_probe = asr_engine.RoutedASR._sv_probe

        def _replace(self, text, zh=False):
            return text

    stub = _Stub()
    result = asr_engine.RoutedASR.transcribe(
        stub, np.zeros(4800, dtype=np.float32), 16000,
        known_lang="zh", speech_s=2.0, live=True)
    assert result["text"] == "some text"
    assert result["lang"] == "yue"
    assert result["tier"] == "sv"
    assert result["text_source"] == "sv"
    assert result["sv_timestamps"] == [0.1, 0.2]
    assert call_count == 1, f"expected exactly one SenseVoice probe decode, got {call_count}"


def test_dual_confirm_session_switches_from_each_stuck_lang_to_yue():
    # Integration-style check across the whole "yue gets stuck" bug report:
    # a session that has already settled on en, ja, or ko must still be able
    # to switch to "yue" once whisper-tiny says "zh" (its only spelling of
    # Cantonese) and SenseVoice's own probe says "yue" on the same audio.
    for starting_lang in ("en", "ja", "ko"):
        lang, switched = asr_engine.resolve_dual_confirm(
            "zh", starting_lang, 3.0, "yue")
        assert (lang, switched) == ("yue", True), starting_lang
        # and the session holds on "yue" afterwards (same-lang no-op) rather
        # than bouncing back.
        held_lang, held_switched = asr_engine.resolve_dual_confirm(
            "yue", lang, 3.0, "yue")
        assert (held_lang, held_switched) == ("yue", False)


def test_dual_confirm_mismatch_at_bootstrap_falls_back_to_whisper_guess():
    # if the caller couldn't get any SenseVoice tag at all (e.g. --minimal
    # install without the sv model), sv_lang is "" -- bootstrap must still
    # resolve to something rather than staying silent forever.
    lang, switched = asr_engine.resolve_dual_confirm("zh", None, 3.0, "")
    assert lang == "zh"
    assert switched is False


def test_sticky_long_detections_still_confirm_a_switch_under_large_guard():
    # Detections at or above the guard length behave exactly as before:
    # switch_confirm consecutive ones confirm a genuine switch.
    lang1, _, pend1, cnt1 = asr_engine.resolve_sticky_lang(
        "zh", "ja", 10.0, 10.0, 2, None, 0)
    assert (pend1, cnt1) == ("zh", 1)

    lang2, suppress2, pend2, cnt2 = asr_engine.resolve_sticky_lang(
        "zh", "ja", 10.0, 10.0, 2, pend1, cnt1)
    assert lang2 == "zh"
    assert suppress2 is False
    assert (pend2, cnt2) == (None, 0)


# ---- refine-pass dual-LID confirmation (real-mic incident: switch_scenario.wav) --

def test_refine_short_solo_group_never_reconsiders_language():
    # A refine "group" can be a single short segment sitting alone between
    # silence gaps -- not a real multi-segment utterance. Below
    # REFINE_MIN_REGROUP_S, whisper-tiny's re-judgment is skipped outright,
    # even if it disagrees with the current language (this is what let a
    # correctly dual-confirmed live "ko" get flipped back to "ru" by a lone
    # whisper-tiny re-judgment during refine).
    lang, changed = asr_engine.resolve_refine_lang("ko", "ru", "", 1.9)
    assert (lang, changed) == ("ko", False)


def test_refine_disagreement_without_sv_confirmation_keeps_current_lang():
    # Long enough to reconsider, but SenseVoice's probe on the same merged
    # audio does NOT agree with whisper-tiny's re-judgment: keep the
    # current (fast-path) language rather than trusting whisper-tiny alone.
    lang, changed = asr_engine.resolve_refine_lang("ko", "ru", "ja", 5.0)
    assert (lang, changed) == ("ko", False)


def test_refine_agreement_above_length_gate_overrides():
    # whisper-tiny's re-judgment AND SenseVoice's probe on the merged audio
    # agree, and the group is long enough: accept the correction.
    lang, changed = asr_engine.resolve_refine_lang("ko", "ja", "ja", 5.0)
    assert (lang, changed) == ("ja", True)


def test_refine_same_lang_is_a_noop():
    lang, changed = asr_engine.resolve_refine_lang("ja", "ja", "ja", 5.0)
    assert (lang, changed) == ("ja", False)


def test_refine_agreement_below_length_gate_still_does_not_override():
    # Even if whisper-tiny and SenseVoice happen to agree, a sub-2.5s group
    # doesn't get to override -- docs/LID.md's own curve hasn't separated
    # from noise yet at that length for several languages.
    lang, changed = asr_engine.resolve_refine_lang("ko", "ja", "ja", 2.0)
    assert (lang, changed) == ("ko", False)


def test_refine_reuses_sv_probe_text_for_ko_yue_instead_of_redecoding():
    # resolve_refine_lang only accepts a correction when SenseVoice's probe
    # on the merged group audio agrees with whisper-tiny's re-judgment, so
    # for ko/yue (both routed to SenseVoice) that probe already decoded the
    # exact text a second self.asr.transcribe() call would produce. The
    # refine worker must reuse it instead of a redundant SenseVoice pass --
    # regression for the redundant re-decode found in code review.
    class _FakeAsr:
        forced_lang = None  # auto mode: refine LID/probe path exercised
        pinned_tier = None

        def __init__(self):
            self.transcribe_calls = 0
            self.decode_full_calls = 0
            self.ko_spacer = None

        def _identify_lang(self, buf, sr):
            return "ko"

        def _get(self, name):
            assert name == "sv"
            return "sv-rec"

        def _decode_full(self, rec, buf, sr):
            self.decode_full_calls += 1
            return ("코리안 텍스트 refine 예시입니다", "<|ko|>0.9")

        def _replace(self, text, zh=False):
            return text

        def transcribe(self, buf, sr, known_lang=None, live=False):
            self.transcribe_calls += 1
            raise AssertionError(
                "transcribe() must not be called when the SV probe result is reused")

    sr = 16000
    history = AudioHistory(sr, keep_s=30.0)
    history.push(np.zeros(60000, dtype=np.float32))
    printer = PartialPrinter(enabled=False)
    asr = _FakeAsr()
    refiner = Refiner(asr, history, sr, printer)
    refiner.add_span(0, 48000, "en", "some english text", "")
    refiner.maybe_refine(48000, force=True, force_sync=True)

    assert asr.decode_full_calls == 1, (
        f"expected exactly one SenseVoice probe decode, got {asr.decode_full_calls}")
    assert asr.transcribe_calls == 0


# ---- Refiner.add_span: groups must not cross a language boundary ----------

class _FakeRefiner:
    """Exercises Refiner.add_span's grouping decision in isolation, with
    maybe_refine stubbed out so no model/audio-buffer work happens (mirrors
    the real method's effect on self.spans: a forced flush empties it)."""

    class _Asr:
        forced_lang = None  # auto mode: script correction drives grouping
        pinned_tier = None

    asr = _Asr()
    add_span = Refiner.add_span
    # add_span() now refuses to run on a closed Refiner (Refiner.close());
    # borrowed, same as add_span itself, so this stub checks what the real
    # guard checks.
    _closed = False
    _check_open = Refiner._check_open

    def __init__(self):
        self.spans = []
        self.calls = []

    def maybe_refine(self, now_sample, force=False, force_sync=None):
        self.calls.append((now_sample, force, force_sync))
        self.spans = []


def test_refiner_add_span_keeps_same_language_in_one_group():
    r = _FakeRefiner()
    r.add_span(0, 16000, "ja", "こんにちは", "")
    r.add_span(16000, 32000, "ja", "元気ですか", "")
    assert r.calls == []
    assert len(r.spans) == 2


def test_refiner_add_span_splits_group_at_language_boundary():
    # Real-mic incident: an en segment sandwiched between two ja ones used
    # to accumulate into one refine group (the "mixed" guard in maybe_refine
    # protected the DECODE but not the display), printing as a single
    # "[refine/ja] ..." line that swallowed the English sentence. A
    # language change must force-flush the previous group asynchronously
    # (force=True, force_sync=False: not urgent, must not block the hot
    # path) before starting a new one.
    r = _FakeRefiner()
    r.add_span(0, 16000, "ja", "そこの上流かな", "")
    r.add_span(48000, 64000, "en", "He was in a fevered state of mind", "")
    assert r.calls == [(48000, True, False)]
    assert len(r.spans) == 1
    assert r.spans[0][2] == "en"


def test_refiner_add_span_splits_again_on_return_to_original_language():
    # ja -> en -> ja must produce three separate groups, not merge the
    # trailing ja span back into anything.
    r = _FakeRefiner()
    r.add_span(0, 16000, "ja", "そこの上流かな", "")
    r.add_span(48000, 64000, "en", "He was in a fevered state of mind", "")
    r.add_span(96000, 112000, "ja", "得意のドルフィンキック", "")
    assert r.calls == [(48000, True, False), (96000, True, False)]
    assert len(r.spans) == 1
    assert r.spans[0][2] == "ja"


# ---- Refiner worker: output order must track enqueue order, not decode time --

def test_refiner_worker_prints_groups_in_enqueue_order_despite_slower_first_decode():
    # Regression for a reviewed-as-plausible ordering bug: the refine worker
    # used to be "spawn a fresh thread per group, serialize the critical
    # section with a Lock". Thread.start() order is not lock-acquisition
    # order, so a language-boundary flush (add_span calling
    # maybe_refine(force=True, force_sync=False)) racing the next group's
    # own flush could let the *second* group's thread win the lock and print
    # first, reversing [refine/...] output relative to real time. The fix
    # replaces per-call threads with a single FIFO worker fed by a
    # queue.Queue, so enqueue order (== call order == audio chronology)
    # deterministically equals decode/print order regardless of how long
    # any individual group's mock decode takes. This test enqueues a
    # deliberately SLOW first group and a fast second group and asserts the
    # slow one still finishes and prints first.
    import threading
    import time

    printed_order = []

    class _FakeAsr:
        forced_lang = None
        pinned_tier = None

        def _identify_lang(self, buf, sr):
            # report agreement with the caller's own language so
            # resolve_refine_lang short-circuits to "no change" and the
            # refine path falls straight through to transcribe(). Decided
            # from the buffer's length (16000 samples for group 1, 32000
            # for group 2, since PREROLL_S pulls each group's buffer back
            # to sample 0) rather than a mutable attribute set by the main
            # thread -- that attribute would race with the worker thread
            # reading it once both groups are queued, which is exactly the
            # kind of nondeterminism this test must not itself depend on.
            return "ja" if len(buf) == 16000 else "en"

        def transcribe(self, buf, sr, known_lang=None, live=False):
            if known_lang == "ja":
                time.sleep(0.2)  # much slower than the second group
            printed_order.append(known_lang)
            return {"text": f"text-{known_lang}"}

    sr = 16000
    history = AudioHistory(sr, keep_s=30.0)
    history.push(np.zeros(sr * 3, dtype=np.float32))
    printer = PartialPrinter(enabled=False)
    asr = _FakeAsr()
    refiner = Refiner(asr, history, sr, printer)

    # group 1: ja, slow decode, spans [0, 16000). Text must actually look
    # Japanese -- script_corrected_lang() would otherwise reclassify an
    # ASCII string tagged "ja" as "en" and defeat the buffer-length check
    # _identify_lang relies on below.
    refiner.spans = [(0, 16000, "ja", "テスト文章です", "")]
    refiner.maybe_refine(16000, force=True, force_sync=False)

    # group 2: en, fast decode, spans [16000, 32000) -- enqueued right after
    # group 1, mirroring a language-boundary flush closely followed by the
    # next group's own flush.
    refiner.spans = [(16000, 32000, "en", "group two text", "")]
    refiner.maybe_refine(32000, force=True, force_sync=False)

    refiner._task_queue.join()  # wait for both queued tasks to finish

    assert printed_order == ["ja", "en"], (
        f"expected FIFO (enqueue) order despite the slower first decode, got {printed_order}")

    # and there is exactly one persistent worker thread, not one per call
    worker_threads = [t for t in threading.enumerate() if t is refiner._worker_thread]
    assert len(worker_threads) == 1


def _omni_safety_net_stub(omni_available: bool, forced: bool = False):
    """RoutedASR stub for the empty-specialist safety net in transcribe():
    auto mode with a stable ja session, the ja route returns nothing, and the
    only question is what happens when the omni fallback is or is not
    installed. forced=True pins ja manually: the net must then stay off
    (manual mode must never resurrect the generalist it just released)."""
    asked = []

    class _Stub:
        forced_lang = "ja" if forced else None
        pinned_tier = "rz" if forced else None
        last_lang = "ja"
        dual_confirm = False
        min_switch_s = 0.0
        lid_switch_confirm = 1
        _pending_lang = None
        _pending_count = 0
        _unavailable = set() if omni_available else {"omni"}
        # transcribe()/partial() now refuse to run on a closed engine
        # (RoutedASR.close()); borrowed rather than faked so this stub
        # keeps checking whatever the real guard checks.
        _closed = False
        _check_open = asr_engine.RoutedASR._check_open
        _itn_overrides = asr_engine.itn_cjk.EMPTY_OVERRIDES
        ko_spacer = None
        punct = None
        punct_zh = None

        def _identify_lang(self, samples, sample_rate):
            return "ja"

        def _get(self, name):
            asked.append(name)
            if name in self._unavailable:
                raise asr_engine.ModelUnavailable(name)
            return f"<{name}>"

        def _route(self, lang):
            assert lang == "ja"
            return ("<rz>", "rz")

        def _decode(self, rec, samples, sample_rate):
            return "オムニ結果" if rec == "<omni>" else ""

        def _replace(self, text, zh=False):
            return text

    return _Stub(), asked


def test_empty_specialist_falls_through_to_omni_when_installed():
    stub, asked = _omni_safety_net_stub(omni_available=True)
    result = asr_engine.RoutedASR.transcribe(
        stub, np.zeros(16000, dtype=np.float32), 16000, live=True)
    assert asked == ["omni"]
    assert result["text"] == "オムニ結果"
    assert result["tier"] == "omni"


def test_empty_specialist_stays_empty_on_a_minimal_install():
    """A minimal install has no omni model. The safety net must degrade to
    the specialist's empty result (a non-speech line, dropped by the caller)
    instead of letting ModelUnavailable escape -- on 2026-09-04 that escape
    took the whole live process down mid-stream on the first empty decode."""
    stub, asked = _omni_safety_net_stub(omni_available=False)
    result = asr_engine.RoutedASR.transcribe(
        stub, np.zeros(16000, dtype=np.float32), 16000, live=True)
    assert asked == ["omni"]
    assert result["text"] == ""
    assert result["tier"] == "rz"


def test_empty_specialist_never_loads_omni_in_manual_mode():
    """Manual pinning releases omni; an empty specialist decode (music/
    silence) must stay empty instead of reloading the generalist and
    defeating the resident-model release."""
    stub, asked = _omni_safety_net_stub(omni_available=True, forced=True)
    result = asr_engine.RoutedASR.transcribe(
        stub, np.zeros(16000, dtype=np.float32), 16000, live=True)
    assert asked == []
    assert result["text"] == ""
    assert result["tier"] == "rz"


def test_empty_specialist_never_loads_omni_with_a_model_pin():
    """Same safety net as the language-pin test above, but through the new
    forced_model path: transcribe() must not touch omni (or any other tier)
    when the pinned model returns empty."""
    asked = []

    class _Stub:
        forced_model = "rz"
        forced_lang = None
        pinned_tier = "rz"
        last_lang = "ja"
        _pending_lang = None
        _pending_count = 0
        _unavailable = set()
        _closed = False
        _check_open = asr_engine.RoutedASR._check_open
        _decode_pinned = asr_engine.RoutedASR._decode_pinned
        _itn_overrides = asr_engine.itn_cjk.EMPTY_OVERRIDES
        punct = None
        ko_spacer = None

        def _get_with_fallback(self, name):
            asked.append(name)
            return ("<rz>", "rz")

        def _get(self, name):
            raise AssertionError(f"model pin must not load {name}")

        def _decode(self, rec, samples, sample_rate):
            return ""

        def _replace(self, text, zh=False):
            return text

    stub = _Stub()
    result = asr_engine.RoutedASR.transcribe(
        stub, np.zeros(16000, dtype=np.float32), 16000, live=True)
    assert asked == ["rz"]
    assert result["text"] == ""
    assert result["tier"] == "rz"


def test_hot_translate_switch_rebinds_the_refiner(monkeypatch):
    """The desktop window's language button (POST /api/translate) and the
    stdin "translate" command share apply_translation_spec. Both must
    rebind Refiner.translators too: the Refiner used to hold the dict
    captured at construction, so refining a clean-book paragraph kept
    translating exactly as configured at startup -- turn translation on
    after booting with none and refined lines gained no translation (and
    the reverse leaked translations after "off")."""
    import realtime_transcribe as rt

    class _Worker:
        def __init__(self):
            self.langs = None

        def set_langs(self, translators):
            self.langs = translators

    class _Refiner:
        def __init__(self):
            self.translators = {}

    built = {"en": object()}
    monkeypatch.setattr(rt, "build_translators", lambda spec: dict(built))
    worker, refiner = _Worker(), _Refiner()

    rt.apply_translation_spec("en", worker, lambda: refiner)
    assert worker.langs == built
    assert refiner.translators == built, "the refine pass must see the new target"

    rt.apply_translation_spec("off", worker, lambda: refiner)
    assert worker.langs == {}
    assert refiner.translators == {}, "translation off must silence refine too"

    # --no-refine / pre-construction race: no refiner object, no crash
    rt.apply_translation_spec("en", worker, None)
    assert worker.langs == built
    rt.apply_translation_spec("en", worker, lambda: None)
    assert worker.langs == built


# ---- sentence-boundary flush guard (SemanticSplitter) ----------------------

def _flush(text, speech_s=3.0, audio_pos=10.0):
    # fresh splitter: cooldown 0, FLUSH_MIN_SPEECH_S satisfied
    return SemanticSplitter().should_flush(text, speech_s, audio_pos)


def test_flush_skips_pz_spaced_decimal_tear():
    """pz token spacing wraps a decimal point in spaces ("31 . 2"), which
    defeats the literal alnum guard -- the flush used to fire mid-number and
    cut the audio after the first digit ("四十二点三" arrived as "四" +
    "十二点三", rendered "4。12.3。"; 2026-10-18 user report)."""
    assert not _flush("terminal bench从31 . 2涨到40")
    assert not _flush("返v4 . 1flash11")
    # collapsed form already covered by the literal guard
    assert not _flush("terminal bench从31.2涨到40")


def test_flush_skips_spaced_digit_sentence_mark():
    assert not _flush("涨到4。 12.3")
    assert not _flush("涨到4 。 12.3")


def test_flush_still_fires_on_real_chinese_sentence_end():
    assert _flush("开源了。第一张表里对比")
    # a real CJK sentence end followed by a number is still a boundary
    assert _flush("测试完成。3项全部上涨")
    assert _flush("AI早报。屏幕是今天")


def test_flush_defers_residual_ct_false_periods_without_silence_gate():
    """Text repair covers the known patterns; if a residual false period
    still reaches the splitter, its continuation tail identifies it. This is
    a text deferral, not a silence gate: ？/！ and strong periods are
    untouched."""
    assert not _flush("屏幕上是今天的。主要内容")
    assert not _flush("新版。模型专门针对")
    assert not _flush("第13天。宣布完整重置")
    assert not _flush("五小时限制。开放dos功能")
    assert not _flush("并免费开放。至10月31日")
    # a real sentence followed by a new subject still flushes
    assert _flush("AI早报。屏幕是今天")


def test_flush_still_fires_on_english_period():
    # EN drafts use spaced ". Next" boundaries; the spaced guard is
    # digit-only and must not swallow them
    assert _flush("I really want to go out. Please")


def test_flush_still_skips_literal_acronym_tear():
    assert not _flush("调用AP。I然后呢")


def test_flush_skips_left_detached_spaced_tears():
    """Every real sentence mark attaches leftward ("out. Please",
    "开源了。第一句"); only pz token spacing emits left-detached dots
    ("31 . 2" / "A . P . I"). Any mark preceded by a space is that artifact
    and must never trigger a flush -- letters, digits or mixed."""
    assert not _flush("调用 A . P . I 然后呢")
    assert not _flush("升级到 flash . 1017 版本了")


def test_flush_skips_cjk_numeral_tear():
    """A mark wedged between two CJK numerals ("四。十二点三") is a model
    tear; a numeral on ONE side only is still a real boundary."""
    assert not _flush("视频里从四。十二点三涨到五")
    assert _flush("答案就是十二。然后我们继续")


def test_flush_skips_english_abbreviation():
    """A dot closing a common abbreviation is not a sentence end; "No."
    IS a sentence on its own and must keep flushing, and words merely
    ENDING in the abbreviation letters ("best.") are untouched."""
    assert not _flush("I spoke to Mr. Smith about it")
    assert _flush("No. I don't think so")
    assert _flush("That was the best. It really was")


def test_flush_defers_while_a_number_is_still_in_progress():
    """A fresh tail visibly ending INSIDE a number ("…波及百分之",
    "…涨到29") must not flush yet: the trim would snap the audio between
    "百分之" and "一点四" (2026-10-18: rendered "百分之。1.4"). The next
    partial re-checks once the number has finished."""
    assert not _flush("暴露此事。与一次大规模蒸馏攻击有关波及百分之")
    assert not _flush("上涨。涨到29")
    assert not _flush("上涨。达到百分之1 .")
    # number complete, but the boundary is STALE: ~27 fresh chars (~6s of
    # speech) sit behind the mark, far outside the trim's 2.5s window --
    # exactly the 2026-10-18 "有。管" report (promised cut at "此事。",
    # actual cut at "有|关")
    assert not _flush("暴露此事。与一次大规模蒸馏攻击有关波及百分之一点四的council")


def test_join_final_texts_keeps_cjk_glued_and_latin_spaced():
    assert _join_final_texts(["主要内容。", "接下来请看"]) == "主要内容。接下来请看"
    assert _join_final_texts(["hello.", "Next"]) == "hello. Next"
    assert _join_final_texts(["OpenAI Codex", "负责人"]) == "OpenAI Codex 负责人"
    assert _join_final_texts(["中文", "English"]) == "中文 English"


def test_coverage_guard_catches_lost_prefix_and_suffix():
    fast = "新版模型专门针对平台进行了优化"
    assert _coverage_ok(fast, fast)
    assert not _coverage_ok(fast, "针对平台进行了优化")
    assert not _coverage_ok(fast, "新版模型专门针对平台进行了优")
    assert not _coverage_ok(fast, "还表示，新版模型专门针对平台进行了优化")
    # a normal punctuation/space difference is not a content loss
    assert _coverage_ok("OpenAI Codex 负责人", "OpenAI Codex负责人。")


def test_boundary_overlap_audit():
    assert _boundary_overlap("新版模型", "模型专门针对") == 2
    assert _boundary_overlap("今天结束", "明天开始") == 0


def test_trim_prefers_dip_near_text_boundary():
    sr = 16000
    splitter = SemanticSplitter()
    text = "今天。休息一下明早出发"
    assert splitter.should_flush(text, 3.0, 10.0)
    n = sr  # 1.0 s
    samples = np.full(n, 0.1, dtype=np.float32)
    frame = int(0.03 * sr)
    for first in (5, 25):  # ~0.15-0.27 s and ~0.75-0.87 s
        samples[first * frame:(first + 4) * frame] = 0.0005
    splitter.arm(10.0, n)
    cut = splitter.trim_if_flushed(n, samples, sr)
    # The text boundary is near 0.27 s; the old latest-dip rule would cut
    # at ~0.87 s instead.
    assert len(cut) == 9 * frame
    assert len(cut) < 20 * frame


def test_trim_does_not_snap_to_a_remote_dip():
    sr = 16000
    splitter = SemanticSplitter()
    assert splitter.should_flush("今天。休息一下明早出发", 3.0, 10.0)
    n = sr
    samples = np.full(n, 0.1, dtype=np.float32)
    frame = int(0.03 * sr)
    # Only a late dip exists, far from the text boundary (~0.27 s).
    samples[27 * frame:31 * frame] = 0.0005
    splitter.arm(10.0, n)
    assert len(splitter.trim_if_flushed(n, samples, sr)) == n


def test_flush_refuses_stale_boundary_with_long_tail():
    """CT can surface a boundary mark seconds after its pause: by flush
    time the tail behind it has grown past what the trim's 2.5s scan window
    can still see, so the trim snaps the audio at the NEWEST dip instead --
    often a mid-word micro-pause dozens of chars past the real boundary
    (2026-10-18 report: "有。管"). Refusing the flush falls back to VAD
    closing: later than ideal, never wrong."""
    # CJK tail of 12 (~2.7s) > BOUNDARY_TAIL_MAX_CJK
    assert not _flush("暴露此事。与一次大规模蒸馏攻击，有")
    assert not _flush("暴露此事。与一次大规模蒸馏攻击有关")
    # latin tail of 36 > BOUNDARY_TAIL_MAX_LATIN
    assert not _flush("and then. The quick brown fox jumps over the lazy dog")


def test_flush_allows_fresh_boundary_within_trim_window():
    """Tails inside the window keep flushing: CJK 8 chars (~1.8s) and
    latin 20 chars (~1.5s) at the limits, fewer in between."""
    assert _flush("测试完成。明天的会议很重要")  # 8 CJK chars
    assert _flush("测试完成。明天要开会")  # 5 CJK chars
    assert _flush("and then. The quick brown fox")  # 19 latin chars
    # a mixed tail counts against the CJK limit (any CJK char in it)
    assert not _flush("暴露此事。与1.4%的council用户均有关联")
    # number complete AND the tail still fresh: usable boundary (the stale
    # case lives in test_flush_refuses_stale_boundary_with_long_tail)
    assert _flush("上涨。涨到29.5了")


def test_build_stamp_names_dir_and_newest_mtime():
    # A long-lived --serve process keeps its import-time code; the stamp's
    # newest-.py mtime is how a "still broken" report is told apart from
    # "old process never restarted". Must name the real scripts dir
    # (absolute, so repo vs portable copy is visible) and carry a parseable
    # newest timestamp.
    stamp = realtime_transcribe._build_stamp()
    assert os.path.isabs(stamp.split("  (")[0].replace("[build] ", ""))
    assert realtime_transcribe._re.search(r"newest \.py \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\)$", stamp), stamp


# --- manual recognition language (settings panel) ---------------------------

def test_tier_for_lang_routes_head_languages_and_eu_to_expected_models():
    # Current app default is --en-tier v2 (realtime_transcribe's CLI default),
    # so English must home on v2 -- a previous plan draft wrongly assumed v3.
    assert asr_engine.tier_for_lang("en", "v2") == "v2"
    assert asr_engine.tier_for_lang("en", "v3") == "v3"
    assert asr_engine.tier_for_lang("zh", "v2") == "pz"
    assert asr_engine.tier_for_lang("ja", "v2") == "rz"
    assert asr_engine.tier_for_lang("ko", "v2") == "sv"
    assert asr_engine.tier_for_lang("yue", "v2") == "sv"
    # 24 EU languages ride Parakeet v3 regardless of the en tier
    for code in ("fr", "de", "es", "ru", "uk", "el"):
        assert asr_engine.tier_for_lang(code, "v2") == "v3"
    # the omnilingual sentinel and any unknown code fall through to omni
    assert asr_engine.tier_for_lang(asr_engine.MANUAL_OMNI, "v2") == "omni"
    assert asr_engine.tier_for_lang("xx", "v2") == "omni"


def test_manual_models_and_legacy_language_migration():
    assert asr_engine.MANUAL_MODELS == ("pz", "rz", "v2", "sv", "v3", "omni")
    # legacy panel files / --lang still map onto a model
    assert asr_engine.LEGACY_LANG_MODEL["zh"] == "pz"
    assert asr_engine.LEGACY_LANG_MODEL["ja"] == "rz"
    assert asr_engine.LEGACY_LANG_MODEL["en"] == "v2"
    assert asr_engine.LEGACY_LANG_MODEL["ko"] == "sv"
    assert asr_engine.LEGACY_LANG_MODEL["yue"] == "sv"
    assert asr_engine.LEGACY_LANG_MODEL["fr"] == "v3"
    assert asr_engine.LEGACY_LANG_MODEL["omni"] == "omni"
    assert len(asr_engine.LEGACY_LANG_MODEL) == 30  # 5 head + 24 EU (en overlap) + omni
    assert "xx" not in asr_engine.LEGACY_LANG_MODEL


def test_guess_lang_from_text_recognizes_scripts():
    guess = asr_engine.guess_lang_from_text
    assert guess("hello world") == "en"
    assert guess("你好，世界") == "zh"
    assert guess("こんにちは世界") == "ja"   # kana wins over the shared Han
    assert guess("안녕하세요") == "ko"
    assert guess("привет") == "ru"
    assert guess("γειά σου") == "el"
    assert guess("123 !!!") == "en"          # no letters: default
    assert guess("", "fr") == "fr"


def test_apply_lid_mode_switches_and_validates():
    class _Stub:
        en_tier = "v2"
        resident_models = ["pz", "rz"]

        def __init__(self):
            self.calls = []
            self.forced_model = None

        def set_forced_model(self, model):
            self.calls.append(model)
            self.forced_model = model

    stub = _Stub()
    out = realtime_transcribe.apply_lid_mode(stub, "auto")
    assert out["mode"] == "auto" and out["model"] == ""
    assert stub.calls == [None]

    out = realtime_transcribe.apply_lid_mode(stub, "SV")
    assert out == {"mode": "manual", "model": "sv", "lang": "", "tier": "sv",
                   "resident": ["pz", "rz"], "en_tier": "v2"}
    assert stub.calls == [None, "sv"]

    out = realtime_transcribe.apply_lid_mode(stub, "JA")  # legacy language
    assert out["model"] == "rz" and stub.calls[-1] == "rz"

    out = realtime_transcribe.apply_lid_mode(stub, "fr")
    assert out["model"] == "v3" and stub.calls[-1] == "v3"

    stub.calls.clear()
    with pytest.raises(ValueError):
        realtime_transcribe.apply_lid_mode(stub, "xx")
    assert stub.calls == []  # invalid codes never reach the engine


def _bare_engine(**attrs):
    """RoutedASR instance without building any model: every attribute
    set_forced_model touches is injected by the test."""
    eng = object.__new__(asr_engine.RoutedASR)
    eng._load_lock = __import__("threading").Lock()
    eng._models = {}
    eng._last_used = {}
    eng._punct = None
    eng._punct_zh = None
    eng._ko_spacer = None
    eng._punctuate = True
    eng._ko_spacer_ok = True
    eng._max_resident = None
    eng._closed = False
    eng._residency_epoch = 0
    eng.en_tier = "v2"
    eng._threads = 4
    eng._hotwords_file = ""
    eng.forced_lang = None
    eng.forced_model = None
    eng.last_lang = "ja"
    eng._pending_lang = "zh"
    eng._pending_count = 2
    eng.lid = object()
    eng.spawned = []
    eng._spawn_bg = lambda target, *args: eng.spawned.append((target, args))
    for k, v in attrs.items():
        setattr(eng, k, v)
    return eng


def test_set_forced_model_releases_unselected_models_and_helpers():
    sv = object()
    eng = _bare_engine()
    eng._models = {"pz": object(), "sv": sv, "rz": object(), "v3": object()}
    eng._last_used = {n: 1.0 for n in eng._models}
    eng._punct = object()          # ja restorer: unused when pinned to sv
    eng._punct_zh = object()       # zh only
    eng._ko_spacer = object()      # needed by SenseVoice ko output

    eng.set_forced_model("sv")

    assert eng.forced_model == "sv" and eng.forced_lang is None
    assert eng.pinned_tier == "sv"
    assert set(eng._models) == {"sv"} and eng._models["sv"] is sv
    assert eng._last_used == {"sv": 1.0}  # released tiers lose their LRU stamps
    assert eng.lid is None         # whisper-tiny released too
    assert eng._punct is None and eng._punct_zh is None
    assert eng._ko_spacer is not None
    assert eng.last_lang is None
    assert (eng._pending_lang, eng._pending_count) == (None, 0)
    assert eng._residency_epoch == 1
    # the pinned tier is warmed on a background thread
    assert eng.spawned and eng.spawned[0][0] == eng._warm_pinned
    assert eng.spawned[0][1] == ("sv",)


def test_set_forced_lang_legacy_maps_to_model_and_keeps_its_helper():
    rz = object()
    eng = _bare_engine()
    eng._models = {"pz": object(), "sv": object(), "rz": rz, "v3": object()}
    eng._punct = object()
    eng._punct_zh = object()
    eng._ko_spacer = object()

    eng.set_forced_lang("ja")

    assert eng.forced_lang == "ja" and eng.forced_model is None
    assert eng.pinned_tier == "rz"
    assert set(eng._models) == {"rz"} and eng._models["rz"] is rz
    assert eng._punct is not None and eng._punct_zh is None
    assert eng._ko_spacer is None
    assert eng.spawned[0][0] == eng._warm_pinned and eng.spawned[0][1] == ("rz",)


def test_set_forced_model_auto_rebuilds_lid_before_clearing_and_reloads(monkeypatch):
    eng = _bare_engine()
    eng.forced_model = "v3"
    eng.lid = None
    monkeypatch.setattr(asr_engine, "_build_lid", lambda threads: "fresh-lid")

    eng.set_forced_model(None)

    assert eng.lid == "fresh-lid"           # rebuilt BEFORE the pin is cleared
    assert eng.forced_model is None and eng.forced_lang is None
    assert (eng._pending_lang, eng._pending_count) == (None, 0)
    assert eng.spawned and eng.spawned[0][0] == eng._reload_for_auto


def test_pinned_model_gates_punctuation_and_spacer_properties():
    eng = _bare_engine()
    eng._punct = object()
    eng._punct_zh = object()
    eng._ko_spacer = object()

    def gates():
        return (asr_engine.RoutedASR.punct.fget(eng),
                asr_engine.RoutedASR.punct_zh.fget(eng),
                asr_engine.RoutedASR.ko_spacer.fget(eng))

    # pinned to v2: ja/zh restorers and Kiwi are all out of scope
    eng.forced_model = "v2"
    assert gates() == (None, None, None)

    # pz: only the zh restorer survives
    eng.forced_model = "pz"
    assert gates() == (None, eng._punct_zh, None)

    # rz: only the ja restorer survives
    eng.forced_model = "rz"
    assert gates() == (eng._punct, None, None)

    # sv: SenseVoice can emit ko, so Kiwi stays; no punctuators needed
    eng.forced_model = "sv"
    assert gates() == (None, None, eng._ko_spacer)

    # omni: CJK drafts use the zh restorer
    eng.forced_model = "omni"
    assert gates() == (None, eng._punct_zh, None)

    # legacy yue pin routes to sv but never emits ko: no Kiwi
    eng.forced_model = None
    eng.forced_lang = "yue"
    assert gates() == (None, None, None)

    # auto restores normal lazy behavior
    eng.forced_lang = None
    assert gates() == (eng._punct, eng._punct_zh, eng._ko_spacer)


def test_decode_pinned_derives_language_from_sensevoice_and_script():
    class _Stub:
        en_tier = "v2"
        forced_model = None

        @property
        def pinned_tier(self):
            return self.forced_model

        def _get_with_fallback(self, name):
            return (f"<{name}>", self.actual)

        def _decode(self, rec, samples, sample_rate):
            return "plain text"

        def _decode_full(self, rec, samples, sample_rate):
            return self.text, self.tag

    stub = _Stub()
    stub.forced_model = "sv"
    stub.actual = "sv"
    stub.text, stub.tag = "你好，世界。", "<|zh|>0.9"
    assert asr_engine.RoutedASR._decode_pinned(
        stub, np.zeros(1600, dtype=np.float32), 16000) == ("你好，世界。", "zh", "sv")
    stub.text, stub.tag = "안녕하세요", "<|ko|>0.9"
    assert asr_engine.RoutedASR._decode_pinned(
        stub, np.zeros(1600, dtype=np.float32), 16000)[1] == "ko"

    # v3 has no reliable language tag: derive it from the script
    stub.forced_model = "v3"
    stub.actual = "v3"
    stub.text, stub.tag = "bonjour tout le monde", ""
    assert asr_engine.RoutedASR._decode_pinned(
        stub, np.zeros(1600, dtype=np.float32), 16000)[1] == "en"
    stub.text = "привет"
    assert asr_engine.RoutedASR._decode_pinned(
        stub, np.zeros(1600, dtype=np.float32), 16000)[1] == "ru"

    # a specialised model implies its language
    stub.forced_model = "pz"
    stub.actual = "pz"
    assert asr_engine.RoutedASR._decode_pinned(
        stub, np.zeros(1600, dtype=np.float32), 16000) == ("plain text", "zh", "pz")


def test_refiner_manual_mode_does_not_split_groups_by_script():
    """Pinned language: the refine group must not be split by the script of
    the decoder's output (a zh pin reading Japanese emits Han-ish text; a ja
    pin reading English emits romaji). Splitting there produced groups tagged
    with languages the session never ran, and refine re-decodes them."""
    import realtime_transcribe as rt

    class _Asr:
        forced_lang = None
        forced_model = "pz"
        pinned_tier = "pz"

    class _History:
        offset = 0
        buf = np.zeros(48000, dtype=np.float32)

    refiner = rt.Refiner(_Asr(), _History(), 16000, rt.PartialPrinter(enabled=False))
    try:
        flushes = []
        refiner.maybe_refine = lambda *a, **k: flushes.append(a)
        refiner.add_span(0, 16000, "zh", "日本語のテキスト", "")
        refiner.add_span(16000, 32000, "zh", "もう一文", "")
        assert flushes == [], "manual mode must keep one refine group"
        assert [s[2] for s in refiner.spans] == ["zh", "zh"]
    finally:
        refiner.close()


def test_refiner_manual_mode_decodes_pinned_without_lid_or_sv_probe():
    """Refine in manual mode must call transcribe(live=False) directly: no
    whisper LID (released), no SenseVoice probe (would resurrect sv behind
    set_forced_lang), no language relabel, and no fast_joined fallback."""
    import realtime_transcribe as rt

    class _Asr:
        forced_lang = None
        forced_model = "pz"
        pinned_tier = "pz"

        def __init__(self):
            self.calls = []

        def transcribe(self, buf, sr, known_lang=None, live=True):
            self.calls.append((known_lang, live))
            return {"text": "中文结果", "lang": "zh", "tier": "pz"}

        def _identify_lang(self, *a):
            raise AssertionError("manual refine must not run LID")

        def _get(self, name):
            raise AssertionError("manual refine must not probe/load models")

    class _History:
        offset = 0

        def __init__(self):
            self.buf = np.zeros(64000, dtype=np.float32)

    asr = _Asr()
    refiner = rt.Refiner(asr, _History(), 16000, rt.PartialPrinter(enabled=False))
    try:
        refiner.spans = [(0, 64000, "zh", "中文", "")]
        refiner.maybe_refine(64000, force=True, force_sync=True)
        assert asr.calls == [(None, False)]
    finally:
        refiner.close()
