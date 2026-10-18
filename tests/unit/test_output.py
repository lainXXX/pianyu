"""T10 acceptance 1-3: five UTF-8 files, valid monotonic SRT, safe file names."""
import json
import re

import pytest

from vtx.asr import Segment
from vtx.output import safe_stem, srt_time, write_outputs
from vtx.qc import Issue

SEGS = [
    Segment(0, 0.0, 3.2, "呃，大家好啊。"),
    Segment(2, 3.5, 7.25, "今天讲一个问题，那那块草不能吃。"),
    Segment(3, 3661.5, 3663.0, "完。"),
]
ISSUES = [Issue("number_mismatch", 30.0, 60.0, "数字不一致：字幕 1201 / 识别 121")]
META = {"platform": "bilibili", "video_id": "BV1wFZ8YBEt4", "part": 1, "title": "为什么人类需要吃盐？",
        "url": "https://www.bilibili.com/video/BV1wFZ8YBEt4?p=1", "duration": 3663.0,
        "engine": "dashscope/paraformer-v2", "billed_seconds": 194}


@pytest.fixture
def files(tmp_path):
    return write_outputs(tmp_path, SEGS, ISSUES, META)


def test_five_files_utf8(files):
    assert set(files) == {"reading", "original", "srt", "edits", "issues"}
    for path in files.values():
        raw = path.read_bytes()
        raw.decode("utf-8")                    # raises if not UTF-8
        assert b"\r\n" not in raw, path.name   # same bytes on Windows and Linux


def test_reading_is_cleaned_and_original_is_verbatim(files):
    reading = files["reading"].read_text(encoding="utf-8")
    original = files["original"].read_text(encoding="utf-8")
    assert "呃" not in reading and "那那块" not in reading and "那块草不能吃" in reading
    assert "呃，大家好啊。" in original and "那那块" in original
    assert "dashscope/paraformer-v2" in reading and "194" in reading   # source + billed time shown


def _parse_srt(text):
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        assert lines[0].isdigit()
        m = re.fullmatch(r"(\d\d):(\d\d):(\d\d),(\d{3}) --> (\d\d):(\d\d):(\d\d),(\d{3})", lines[1])
        assert m, lines[1]
        g = list(map(int, m.groups()))
        a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        cues.append((int(lines[0]), a, b, "\n".join(lines[2:])))
    return cues


def test_srt_valid_and_monotonic(files):
    cues = _parse_srt(files["srt"].read_text(encoding="utf-8"))
    assert [c[0] for c in cues] == [1, 2, 3]
    assert all(a < b for _, a, b, _ in cues)
    assert all(cues[i][2] <= cues[i + 1][1] for i in range(len(cues) - 1))
    assert cues[2][1] == pytest.approx(3661.5)
    assert cues[0][3] == "呃，大家好啊。"     # SRT carries the verbatim text


def test_srt_time_format():
    assert srt_time(3661.5) == "01:01:01,500"
    assert srt_time(0.0004) == "00:00:00,000"


def test_edits_log_is_reversible(files):
    data = json.loads(files["edits"].read_text(encoding="utf-8"))
    assert {e["old"] for e in data} >= {"呃，", "那"}
    assert all({"segment", "line", "time", "start", "end", "old", "new", "reason"} <= set(e) for e in data)
    srt_lines = [l for l in files["srt"].read_text(encoding="utf-8").split("\n\n") if l.strip()]
    for e in data:   # `line` points at the SRT cue holding the deleted text at that offset
        cue_text = srt_lines[e["line"]].splitlines()[2]
        assert cue_text[e["start"]:e["end"]] == e["old"]


def test_issues_file(files):
    data = json.loads(files["issues"].read_text(encoding="utf-8"))
    assert data[0]["kind"] == "number_mismatch" and data[0]["jump_url"].endswith("t=30")


@pytest.mark.parametrize("title", [
    "为什么人类需要吃盐？",
    'a<b>c:"d"/e\\f|g?h*i',
    "😀" * 80 + "超长标题" * 40,
    "CON", "nul.txt", "   ", "...",
])
def test_safe_stem(title):
    stem = safe_stem("bilibili", "BV1wFZ8YBEt4", 1, title)
    assert len(stem) <= 100 - len(".reading.txt")
    assert not re.search(r'[<>:"/\\|?*\x00-\x1f]', stem)
    assert not stem.endswith((".", " "))
    assert stem.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL"}
    assert "BV1wFZ8YBEt4" in stem
