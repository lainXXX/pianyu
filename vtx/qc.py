"""Automatic quality checks: flag suspicious places, never block output.

`activity(a, b)` returns the fraction of [a, b) that contains speech (from a VAD); it is
injected so the rules stay testable. `second` is an optional independent transcript
(e.g. B站 AI subtitles) as [(start, end, text)].
"""
import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from vtx.asr import Segment
from vtx.textnorm import error_rate, number_errors, tokens

GAP_S = 8            # silence-free stretch without text longer than this is suspicious
END_S = 20           # speech continuing this long after the last sentence
SPEECH_FRAC = 0.5    # "there is speech here"
SILENCE_FRAC = 0.2   # "there is (almost) no speech here"
MAX_RATE = 12        # tokens per second; faster than any human speaker
WINDOW_S = 30        # second-source comparison window
MISMATCH = 0.35      # B站 AI subtitles alone are ~10% off (T7), so only big disagreements count
_LOOP = re.compile(r"(.{1,4}?)\1{4,}")


@dataclass(frozen=True)
class Issue:
    kind: str        # gap | ends_early | repeat | loop | text_in_silence | speech_rate |
                     # time_order | out_of_range | source_mismatch | number_mismatch
    start: float
    end: float
    detail: str


def _norm(text: str) -> str:
    return "".join(tokens(text))


def _structure(segs: Sequence[Segment], duration: float, activity) -> List[Issue]:
    out = []
    prev_end = 0.0
    for i, s in enumerate(segs):
        if i and s.start < segs[i - 1].start:
            out.append(Issue("time_order", s.start, s.end, f"第 {s.index} 句早于上一句"))
        if s.end > duration + 1:
            out.append(Issue("out_of_range", s.start, s.end, f"结束于 {s.end:.1f}s，超出时长 {duration:.1f}s"))
        if s.start - prev_end > GAP_S and activity(prev_end, s.start) >= SPEECH_FRAC:
            out.append(Issue("gap", prev_end, s.start, f"{s.start - prev_end:.0f} 秒有人声但没有文字"))
        prev_end = max(prev_end, s.end)
    if segs and duration - prev_end > END_S and activity(prev_end, duration) >= SPEECH_FRAC:
        out.append(Issue("ends_early", prev_end, duration, f"最后一句之后还有 {duration - prev_end:.0f} 秒人声"))
    return out


def _content(segs: Sequence[Segment], activity) -> List[Issue]:
    out = []
    for i, s in enumerate(segs):
        norm = _norm(s.text)
        if i and len(norm) >= 4 and norm == _norm(segs[i - 1].text) and s.start - segs[i - 1].end < 2:
            out.append(Issue("repeat", s.start, s.end, f"与上一句完全相同：{s.text[:30]}"))
        loop = _LOOP.search(norm)
        if loop and len(loop.group(0)) >= max(8, len(norm) // 2):
            out.append(Issue("loop", s.start, s.end, f"循环重复：{loop.group(0)[:20]}"))
        if activity(s.start, s.end) < SILENCE_FRAC:
            out.append(Issue("text_in_silence", s.start, s.end, f"这里几乎没有人声：{s.text[:30]}"))
        rate = len(tokens(s.text)) / max(s.end - s.start, 0.5)
        if rate > MAX_RATE:
            out.append(Issue("speech_rate", s.start, s.end, f"语速 {rate:.0f} 字/秒，异常快"))
    return out


def _is_num(tok: str) -> bool:
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?%?", tok))


def _num_value(tok: str) -> float:
    return float(tok.rstrip("%"))


def _disagreement(a: str, b: str) -> float:
    """Symmetric: edit distance over the longer side, so a source that drops text is not >100%."""
    from rapidfuzz.distance import Levenshtein

    ta, tb = tokens(a), tokens(b)
    return Levenshtein.distance(ta, tb) / max(len(ta), len(tb), 1)


def _compare(segs: Sequence[Segment], second: Sequence[Tuple[float, float, str]], duration: float) -> List[Issue]:
    windows = []
    t = 0.0
    while t < duration:
        w_end = min(t + WINDOW_S, duration)
        ours = "".join(s.text for s in segs if t <= (s.start + s.end) / 2 < w_end)
        theirs = "".join(x for a, b, x in second if t <= (a + b) / 2 < w_end)
        if ours and theirs and len(tokens(theirs)) >= 10:
            windows.append((t, w_end, ours, theirs, _disagreement(theirs, ours)))
        t = w_end
    if not windows:
        return []

    out = []
    rates = sorted(w[4] for w in windows)
    median = rates[len(rates) // 2]
    if median > MISMATCH:
        # the second source is broadly unreliable for this video: one note, no per-window noise
        out.append(Issue("second_source_unreliable", 0.0, duration,
                         f"平台字幕与识别整体差异 {median:.0%}，本视频不做逐段比对"))
    threshold = max(MISMATCH, 2 * median)
    for a, b, ours, theirs, rate in windows:
        numbers = [e for e in number_errors(theirs, ours)
                   if _is_num(e["ref"]) and _is_num(e["hyp"]) and _num_value(e["ref"]) != _num_value(e["hyp"])]
        if numbers:
            pairs = "，".join(f"字幕 {e['ref']} / 识别 {e['hyp']}" for e in numbers[:3])
            out.append(Issue("number_mismatch", a, b, f"数字不一致：{pairs}"))
        if median <= MISMATCH and rate > threshold:
            out.append(Issue("source_mismatch", a, b, f"与平台字幕差异 {rate:.0%}"))
    return out


def check(segments: Sequence[Segment], duration: float, activity: Callable[[float, float], float],
          second: Optional[Sequence[Tuple[float, float, str]]] = None) -> List[Issue]:
    issues = _structure(segments, duration, activity) + _content(segments, activity)
    if second:
        issues += _compare(segments, second, duration)
    return sorted(issues, key=lambda i: (i.start, i.kind))


def vad_activity(wav) -> Callable[[float, float], float]:
    """Speech fraction from Silero VAD (small CPU model, no ASR)."""
    from vtx.asr import SR, silero_spans

    spans = [(a / SR, b / SR) for a, b in silero_spans(wav)]

    def activity(a: float, b: float) -> float:
        if b <= a:
            return 0.0
        return sum(max(0.0, min(b, y) - max(a, x)) for x, y in spans) / (b - a)

    return activity
