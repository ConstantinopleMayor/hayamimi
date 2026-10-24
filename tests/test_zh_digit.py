"""Pure-logic tests for zh_digit's conservative number conversion."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from zh_digit import restore_zh_digits


def test_compound_duration_units():
    assert restore_zh_digits("五小时") == "5小时"
    assert restore_zh_digits("两小时") == "2小时"
    assert restore_zh_digits("五小时二十分钟") == "5小时20分钟"


def test_liang_with_pure_classifier_stays_chinese():
    assert restore_zh_digits("两项") == "两项"
    assert restore_zh_digits("两个") == "两个"
    assert restore_zh_digits("三项") == "3项"


def test_approximate_and_ranges():
    assert restore_zh_digits("三五个") == "三五个"
    assert restore_zh_digits("三到五个") == "3到5个"
    assert restore_zh_digits("三至五个") == "3至5个"
    assert restore_zh_digits("五到10个") == "5到10个"


def test_ordinary_magnitude_numbers_still_convert():
    assert restore_zh_digits("十五个") == "15个"
    assert restore_zh_digits("三十五个") == "35个"