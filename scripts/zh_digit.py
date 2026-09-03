"""translate_api-independent deterministic post-processing: restore Chinese
digit words to Arabic digits.

The Paraformer-zh family (both the 8404 and 8358 vocabs, verified by
side-by-side decoding) outputs digits as Chinese words: 三十五 -> 35,
百分之十七 -> 17%, 四点八 -> 4.8. FunASR's pipeline hides this with an ITN
post-process; sherpa-onnx does not ship one -- this module is that
post-process, implemented conservatively:

- A digit phrase is restored ONLY when it is followed by a unit word
  (三十五秒 -> 35秒), or stands at a non-CJK boundary (版本四四零,
  三十五点八,).
- Idioms and non-numeric contexts stay untouched: 万古长青, 四面八方,
  三十而立, 一路顺风, 二十四节气, 三点水, 万一, 十分感谢.
- Time phrases X点Y分 with X<=12 become X:Y (三点二十五分 -> 3:25);
  otherwise X点Y is a decimal (三十四点八 -> 34.8).
- 万/亿 stay as the unit word (三百四十万 -> 340万, 二亿三千万 -> 2.3亿);
  percentages keep the % sign (百分之十七 -> 17%).
"""

from __future__ import annotations

import re

_DIGIT_VALUES = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}

_DIGIT_CHARS = "零〇一二两三四五六七八九"
_NUM_CHARS = _DIGIT_CHARS + "十百千万亿"
# After 点, minutes read with 十: 三点二十五分 -> 3:25 (also 三点二五 -> 3.25).
_FRAC_CHARS = _DIGIT_CHARS + "十"

# Unit words a digit phrase may be glued to (三十五秒 -> 35秒).
_UNIT_WORDS = (
    "分", "秒", "时", "天", "月", "年", "号", "日", "周", "元", "块",
    "倍", "成", "折", "个", "位", "人", "台", "辆", "次", "岁", "层",
    "届", "名", "家", "份", "款", "项", "公里", "千米", "米", "厘米",
    "毫米", "克", "千克", "公斤", "吨", "升", "毫升", "度", "期", "版",
    "集", "间", "场", "里", "斤", "亩", "课", "首", "章", "件", "套",
)

_UNIT_ALT = "|".join(sorted(_UNIT_WORDS, key=len, reverse=True))

# Pure classifiers: 一 + these stays Chinese (一个/一块/一次/一台/一家
# are less readable as 1个/1块...). Numeric-sense units (万/年/月/日/分/
# 秒/元/倍/公里...) still restore (一万 -> 1万, 一年 -> 1年).
_Q_UNITS = frozenset((
    "个", "块", "次", "台", "家", "部", "声", "盘", "件", "张", "位", "名",
    "辆", "场", "间", "层", "份", "款", "项", "套", "首", "章", "课", "亩",
    "里", "斤", "栋", "册", "本", "门", "堂", "双", "幅", "篇", "集", "段",
    "步", "口", "杯", "瓶", "碗", "桌", "把", "根", "支", "条", "匹", "头",
    "只", "群", "堆", "沓", "箱", "袋", "包", "艘", "架", "届", "辆",
))

# Ordinal prefix 第 is captured so 第三十五届 -> 第35届.
_RE = re.compile(
    r"(第?)([" + _NUM_CHARS + r"]+)(?:点([" + _FRAC_CHARS + r"]+))?(" + _UNIT_ALT + r")?"
)
_PCT_RE = re.compile(
    r"百分之([" + _NUM_CHARS + r"]+)(?:点([" + _FRAC_CHARS + r"]+))?"
)


def _parse_int(s: str) -> int:
    """Parse a Chinese digit phrase to int (supports 千/百/十/万/亿 groups).

    _parse_int("三百四十") == 340; ("二亿三千万") == 230000000.
    A bare string of one-digit chars (no 十百千万亿) is read digit by
    digit, as in version numbers and years: "四四零" -> 440, "一九四九"
    -> 1949.
    """
    if not re.search("[十百千万亿]", s):
        return int("".join(str(_DIGIT_VALUES[c]) for c in s))
    total = 0
    for ypart in s.split("亿"):
        pt = 0
        for wpart in ypart.split("万"):
            t = 0
            sec = 0
            for ch in wpart:
                if ch in _DIGIT_VALUES:
                    sec = _DIGIT_VALUES[ch]
                elif ch == "十":
                    t += (sec or 1) * 10
                    sec = 0
                elif ch == "百":
                    t += (sec or 1) * 100
                    sec = 0
                elif ch == "千":
                    t += (sec or 1) * 1000
                    sec = 0
            t += sec
            pt = pt * 10000 + t
        total = total * 100000000 + pt
    return total


def _frac_digits(frac: str) -> str:
    """小数/分钟段 with possible 十: 二十五->25 零五->05 八->8 二五->25."""
    if re.search("[十百千]", frac):
        return str(_parse_int(frac))
    return "".join(str(_DIGIT_VALUES[c]) for c in frac)


def _num_str(v) -> str:
    """int/float -> compact string without trailing zeros."""
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        v = int(v)
    return str(v) if isinstance(v, int) else ("%.12g" % v)


def _decimals(main: str, frac: str | None):
    """(main, frac) -> numeric value as string (小数部分逐位).

    A decimal keeps its full digit string: 二点零 -> "2.0" (a version
    number must NOT collapse to "2"), 三点二五 -> "3.25". Never routed
    through _num_str, which would drop trailing zeros.
    """
    v = _parse_int(main)
    if frac:
        return f"{v}.{_frac_digits(frac)}"
    return _num_str(v)


def _scale(main: str, v) -> str:
    """Keep 万/亿 as unit words: 三百四十万 -> 340万, 二亿三千万 -> 2.3亿."""
    if "亿" in main and v >= 100000000:
        return _num_str(v / 1e8) + "亿"
    if "万" in main and v >= 10000:
        return _num_str(v / 1e4) + "万"
    return _num_str(v)


def _pct_repl(m: re.Match) -> str:
    main, frac = m.group(1), m.group(2)
    v = _parse_int(main)
    if frac:
        return _scale(main, v) + "." + _frac_digits(frac) + "%"
    return _scale(main, v) + "%"


def _repl(m: re.Match) -> str:
    prefix, main, frac, unit = m.group(1), m.group(2), m.group(3), m.group(4)
    text = m.string
    start = m.start()
    end = m.end()

    # 几/数 before the phrase = an approximate count, not a number:
    # 几万台, 几十万台, 数十万台, 几千台 keep their characters.
    if start > 0 and text[start - 1] in ("几", "数"):
        return m.group(0)
    # 万分之X (万分之一/万分之三) keeps its characters too.
    if start >= 3 and text[start - 3:start] == "万分之":
        return m.group(0)

    # 万一-style short words (万 + one digit) are never digits.
    if main.startswith("万") and main[-1] in _DIGIT_VALUES and len(main) <= 2:
        return m.group(0)

    # A lone magnitude word glued to a unit is idiomatic, not a count:
    # 百分点/百年/千人/万人/亿台 keep their characters (百分之X was already
    # consumed by _PCT_RE above). Multi-char phrases (一百, 三万) still parse.
    if main in ("百", "千", "万", "亿") and not frac:
        return m.group(0)

    # 一 + a pure classifier stays Chinese: 一个/一块/一次/一台 are less
    # readable as 1个/1块. Numeric-sense units (万/年/月/日/分/秒/元/倍/
    # 公里...) still restore: 一万 -> 1万, 一分 -> 1分.
    if main == "一" and unit in _Q_UNITS and not frac:
        return m.group(0)

    # 亿万 (a huge, unspecified multitude) is never a number: 亿万人,
    # 亿万富翁 keep their characters. (万亿, one 万 before 亿, IS numeric:
    # 两万亿 -> 2万亿.)
    if "亿万" in main:
        return m.group(0)

    v = _parse_int(main)

    # A bare integer phrase (no unit, no decimal part) must end at a
    # non-CJK boundary (版本四四零 / 第15届。): guard 万古长青, 三十而立,
    # 二十四节气, 三点水, 十分感谢. A DECIMAL candidate (三十五点八耗时)
    # is self-evidently numeric and needs no boundary check.
    if not unit and not frac and end < len(text) and "\u4e00" <= text[end] <= "\u9fff":
        return m.group(0)

    # 十分感谢 / 十分高兴: "十分" before another character is the adverb
    # "very", not 10分. Keep it when 分 runs into a CJK char.
    if main == "十" and unit == "分" and end < len(text) and "\u4e00" <= text[end] <= "\u9fff":
        return m.group(0)

    # Time: X点Y分 with X<=12 -> X:Y (三点二十五分 -> 3:25, 五点三分 -> 5:03);
    # otherwise X点Y stays a decimal (三十四点八 -> 34.8).
    if unit == "分" and frac and v <= 12:
        return f"{prefix}{v}:{_frac_digits(frac).zfill(2)}"

    num = _decimals(main, frac)
    if "万亿" in main and v >= 1000000000000:
        num = _num_str(v / 1e12) + "万亿"
    elif "亿" in main and v >= 100000000:
        num = _num_str(v / 1e8) + "亿"
    elif "万" in main and v >= 10000:
        num = _num_str(v / 1e4) + "万"
    return prefix + num + (unit or "")


def restore_zh_digits(text: str) -> str:
    """Restore Chinese digit phrases to Arabic digits (conservative)."""
    if not text or not re.search("[" + _NUM_CHARS + "]", text):
        return text
    s = _PCT_RE.sub(_pct_repl, text)
    return _RE.sub(_repl, s)


if __name__ == "__main__":
    for t in [
        "昨天准确率提升了百分之十七，得分三十五点八，耗时五十六秒，版本四四零",
        "重点呢想谈三个问题",
        "万古长青，四面八方，三十而立，一路顺风",
        "二十四节气，三点水，万一是真，十分感谢",
        "三点二十五分开会，五点三分集合",
        "第三十五届，一个苹果，三次机会，三百四十万，二亿三千万",
    ]:
        print(t, "->", restore_zh_digits(t))
