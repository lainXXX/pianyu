"""T5: scoring harness picks the right text for each reference range."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("eval_mod", ROOT / "experiments" / "eval.py")
eval_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_mod)


def test_srt_parsing(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,500 --> 00:00:03,000\n你好\n\n2\n00:01:00,000 --> 00:01:02,250\n再见\n", encoding="utf-8")
    assert eval_mod._srt_segments(srt) == [(1.5, 3.0, "你好"), (60.0, 62.25, "再见")]


def test_each_segment_counted_once_by_midpoint():
    segs = [(0, 10, "甲"), (55, 65, "乙"), (61, 70, "丙")]
    assert eval_mod.text_in_range(segs, 0, 60) == "甲"
    assert eval_mod.text_in_range(segs, 60, 120) == "乙丙"


def test_score_combines_ranges(monkeypatch):
    monkeypatch.setattr(eval_mod, "SOURCES", {"x": lambda sid, wd: [(0, 5, "今天天汽很"), (100, 105, "15毫升")]})
    refs = [{"start": 0, "end": 10, "text": "今天天气很好"}, {"start": 90, "end": 110, "text": "50毫升"}]
    (row,) = eval_mod.score("s", refs, None)
    assert row["ref_tokens"] == 6 + 3
    assert row["error_rate"] == pytest.approx(3 / 9, abs=1e-3)   # 2 + 1 errors
    assert row["number_errors"] == [{"ref": "50", "hyp": "15"}]
