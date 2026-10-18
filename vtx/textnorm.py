"""Text normalisation for CER/WER and number-fact checks.

Rules (fixed for T5; changing them changes every score):
- NFKC (full width -> half width), traditional -> simplified (OpenCC t2s), lower case,
  punctuation dropped.
- Chinese numerals -> Arabic: 一点五 -> 1.5, 两千零二十六 -> 2026, 二零二六 -> 2026,
  负五 -> -5, 百分之五十 -> 50%.
- Tokens: one per CJK character, one per English word, one per number (sign, decimals
  and % stay inside the number token, so 1.5 vs 15 and 50% vs 50 differ).
- Error rate = token edit distance / reference tokens (CER for Chinese, WER for English,
  both at once for mixed text).
"""
import re
import unicodedata
from typing import List

from rapidfuzz.distance import Levenshtein

_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"十": 10, "百": 100, "千": 1000}
_BIG = {"万": 10_000, "亿": 100_000_000}
_CN = "零〇一二两三四五六七八九十百千万亿"
_CN_NUM = rf"[{_CN}]+(?:点[零〇一二两三四五六七八九]+)?"
_TOKEN = re.compile(r"-?\d+(?:\.\d+)?%?|[a-z]+(?:'[a-z]+)?|[㐀-鿿豈-﫿]")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?%?$")


def _cn_int(s: str) -> int:
    if not any(c in _UNIT or c in _BIG for c in s):
        return int("".join(str(_DIGIT[c]) for c in s))  # 二零二六 -> 2026, 三 -> 3
    total = section = number = 0
    for c in s:
        if c in _DIGIT:
            number = _DIGIT[c]
        elif c in _UNIT:
            section += (number or (1 if c == "十" else 0)) * _UNIT[c]
            number = 0
        else:
            total += (section + number) * _BIG[c]
            section = number = 0
    return total + section + number


def _cn_number(s: str) -> str:
    whole, _, frac = s.partition("点")
    value = str(_cn_int(whole)) if whole else "0"
    return value + ("." + "".join(str(_DIGIT[c]) for c in frac) if frac else "")


# Ordinary words that merely contain numeral characters; never converted.
_WORDS = re.compile(rf"(?<![{_CN}])(?:十分|万分|万一|千万|一一|一起|一样|一下|一些|一直|一定|一般|一切|一边|一旦|"
                    r"一致|一面|一向|一时|一会|一番|一度|一同|一律|一齐|一共|一再|统一|唯一|不一|之一|万万)")
# A bare numeral run counts as a quantity only when a measure word / unit follows it.
_MEASURE = set("个次年月日号天分秒点岁人只件条位名本张元块毛角斤米度倍层页种项级届期遍场把台辆所家周批组段句集章节")


def _is_quantity(run: str, before: str, after: str) -> bool:
    if before == "第" or "点" in run or any(c in _UNIT or c in _BIG for c in run):
        return True
    if len(run) >= 2:          # digit-by-digit reading: 二零二六
        return True
    return after in _MEASURE


def _convert_runs(text: str) -> str:
    protected = [m.span() for m in _WORDS.finditer(text)]

    def repl(m):
        start, end = m.span()
        if any(start < pe and ps < end for ps, pe in protected):
            return m.group(0)
        before, after = text[start - 1:start], text[end:end + 1]
        return _cn_number(m.group(0)) if _is_quantity(m.group(0), before, after) else m.group(0)

    return re.sub(_CN_NUM, repl, text)


_T2S = None


def _to_simplified(text: str) -> str:
    global _T2S
    if _T2S is None:
        import opencc

        _T2S = opencc.OpenCC("t2s")
    return _T2S.convert(text)


def normalize(text: str) -> str:
    text = _to_simplified(unicodedata.normalize("NFKC", text)).lower()
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # 1,000 -> 1000
    text = re.sub(rf"百分之({_CN_NUM})", lambda m: _cn_number(m.group(1)) + "%", text)
    text = re.sub(rf"负({_CN_NUM})", lambda m: "-" + _cn_number(m.group(1)), text)
    return _convert_runs(text)


def tokens(text: str) -> List[str]:
    return _TOKEN.findall(normalize(text))


def error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = tokens(reference), tokens(hypothesis)
    if not ref:
        if hyp:
            raise ValueError("参考文本为空，无法计算错误率")
        return 0.0
    return Levenshtein.distance(ref, hyp) / len(ref)


def number_errors(reference: str, hypothesis: str) -> List[dict]:
    """Substitutions / deletions / insertions that involve a number token."""
    ref, hyp = tokens(reference), tokens(hypothesis)
    out = []
    for op in Levenshtein.editops(ref, hyp):
        r = ref[op.src_pos] if op.tag in ("replace", "delete") else ""
        h = hyp[op.dest_pos] if op.tag in ("replace", "insert") else ""
        if _NUMBER.match(r) or _NUMBER.match(h):
            out.append({"ref": r, "hyp": h})
    return out
