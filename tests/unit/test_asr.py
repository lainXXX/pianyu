"""Shared ASR helpers: tag stripping and long-span splitting."""
from vtx.asr import SR, strip_tags


def test_strip_tags():
    assert strip_tags("<|zh|><|NEUTRAL|><|Speech|><|withitn|>今天天气不错。") == "今天天气不错。"
    assert strip_tags("<|en|><|EMO_UNKNOWN|><|BGM|><|woitn|>") == ""


def test_long_vad_span_is_split_at_quiet_point():
    import numpy as np

    from vtx.asr import split_long_spans

    audio = np.full(SR * 100, 0.5, dtype="float32")
    audio[SR * 25: SR * 25 + 1600] = 0.0              # quiet dip inside [2/3, 1] of the first 29.5 s
    spans = split_long_spans([(0, SR * 100)], int(29.5 * SR), lambda a, b: audio[a:b])
    assert all(e - s <= int(29.5 * SR) for s, e in spans)
    assert SR * 25 <= spans[0][1] <= SR * 25 + 1600    # cut falls inside the quiet dip
    assert spans[0][0] == 0 and spans[-1][1] == SR * 100
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:]))  # contiguous, nothing lost
