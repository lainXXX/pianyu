"""T44 acceptance: every built-in point layout renders real T41 data to a PNG of the right size."""
import json
import struct
from pathlib import Path

import pytest

from vtx.layouts_points import POINTS
from vtx.poster import footer_text, render_png
from vtx.poster_templates import SAMPLE, SAMPLE_META

pytestmark = pytest.mark.integration
REAL = Path("work/jobs/9735302125f3/ai/b7b5c8f9964f.json")
DATA = json.loads(REAL.read_text(encoding="utf-8"))["data"] if REAL.exists() else SAMPLE
LONG = {**DATA, "title": "一个特别特别长的标题，" * 6, "conclusion": "很长的结论。" * 30,
        "points": [{**p, "heading": p["heading"] * 4} for p in DATA["points"]] * 2}


@pytest.mark.parametrize("tpl", POINTS, ids=[t["id"] for t in POINTS])
@pytest.mark.parametrize("data", [DATA, LONG], ids=["real", "overlong"])
def test_layout_renders(tmp_path, tpl, data):
    meta = {**SAMPLE_META, "title": data["title"]}
    png = tmp_path / "x.png"
    render_png(data, meta, tpl, png, tmp_path / "x.html", footer=footer_text(meta))
    w, h = struct.unpack(">II", png.read_bytes()[16:24])
    assert w == tpl["width"]
    if tpl["height_mode"].startswith("fixed:"):
        assert h == int(tpl["height_mode"].split(":")[1])            # covers never grow, text is clamped
    else:
        assert 600 < h <= 8000
    html = (tmp_path / "x.html").read_text(encoding="utf-8")
    assert "<script" not in html and "示例UP" in html
