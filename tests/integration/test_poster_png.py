"""T29 acceptance 2-3: real PNG rendering with headless Edge.

Uses fixed structured data (no LLM call): a short video and a long-course-sized one.
Real model output is checked in T31.
"""
from pathlib import Path

import struct

import pytest

from vtx.poster import A_MAX_HEIGHT, A_WIDTH, B_WIDTH, render_png


def png_size(path: Path):
    """Width/height from the IHDR chunk; also proves the file is a PNG."""
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR"
    return struct.unpack(">II", head[16:24])

pytestmark = pytest.mark.integration

SHORT = {
    "title": "为什么人类需要不断吃盐，而动物们却不用？",
    "conclusion": "不只是人。地球上所有生物都离不开电解质，这是生命从海洋里带出来的“底层代码”。",
    "points": [
        {"heading": "动物其实更馋盐", "body": "长颈鹿舔钢管、牛圈要放舔砖；沾了尿的青草是牛最好的零食。", "key": False, "detail": [],
         "quotes": [{"quote": "对牛牛来讲那可是九九成稀罕物。", "source": "x"}]},
        {"heading": "典故：羊车望幸", "body": "嫔妃们在门口洒盐水引羊，羊车停在谁门口皇帝就去谁那里。", "key": False, "detail": [], "quotes": []},
        {"heading": "为什么骆驼出门前喂盐", "body": "出远门前直接喂盐，骆驼会大量喝水提前储水。", "key": True,
         "detail": ["做法：喂盐，让骆驼齁得受不了而大量喝水", "原因：直接喂水会冲淡体内盐分，身体反而排出更多水", "启示：人大量出汗后，补水也要补电解质"],
         "quotes": [{"quote": "吃一碗粉就得给一碗的钱，喝一份水就得喝一份的盐。", "source": "x"}]},
        {"heading": "盐在身体里做什么", "body": "钠离子参与神经信号传递、肌肉收缩和渗透压维持。", "key": True,
         "detail": ["长期缺钠会四肢无力、食欲不振", "其他生物也需要钾、钙、镁等电解质"], "quotes": []},
        {"heading": "生命起源于海洋", "body": "与海水交换电解质是单细胞时代留下的“底层代码”。", "key": False, "detail": [], "quotes": []},
    ],
}
META_SHORT = {"platform": "bilibili", "title": SHORT["title"], "duration": 197}

LONG = {
    "title": "机器学习基本概念简介（上）",
    "conclusion": "机器学习就是让机器找一个函数：先猜函数的形式，再定义损失，最后用梯度下降找最好的参数。",
    "points": [
        {"heading": f"第 {i} 步：概念 {i}", "body": "这里是对这一部分内容的一两句话概括，" * 3, "key": i in (3, 6),
         "detail": ["展开说明一：" + "具体的细节内容，" * 4, "展开说明二：" + "具体的细节内容，" * 4] if i in (3, 6) else [],
         "quotes": [{"quote": "那我们就可以让机器自动去找这个函数。", "source": "x"}] if i in (2, 5) else []}
        for i in range(1, 11)
    ],
}
META_LONG = {"platform": "bilibili", "title": LONG["title"], "duration": 2999}


@pytest.mark.parametrize("name,data,meta", [("short", SHORT, META_SHORT), ("long", LONG, META_LONG)])
@pytest.mark.parametrize("layout", ["A", "B"])
def test_png(tmp_path, name, data, meta, layout):
    png, html = tmp_path / f"{name}-{layout}.png", tmp_path / f"{name}-{layout}.html"
    info = render_png(data, meta, layout, png, html)
    w, h = png_size(png)
    assert w == (A_WIDTH if layout == "A" else B_WIDTH)
    if layout == "A":
        assert h <= A_MAX_HEIGHT
    assert info["height"] == h or abs(info["height"] - h) <= 1
    assert html.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_a_preserves_tail_when_too_tall(tmp_path):
    huge = dict(LONG, points=[dict(p, body=p["body"] * 6, detail=[d * 3 for d in p["detail"]]) for p in LONG["points"]])
    html = tmp_path / "huge.html"
    info = render_png(huge, META_LONG, "A", tmp_path / "huge.png", html)
    assert info["height"] > A_MAX_HEIGHT and info["points"] == len(huge["points"])
    assert huge["points"][-1]["heading"] in html.read_text(encoding="utf-8")


def test_save_samples_for_review():
    """Keep the short-sample posters where the user can open them."""
    out = Path(__file__).resolve().parents[2] / "work" / "poster-samples"
    out.mkdir(parents=True, exist_ok=True)
    for layout in ("A", "B"):
        render_png(SHORT, META_SHORT, layout, out / f"salt-{layout}.png", out / f"salt-{layout}.html")
    assert (out / "salt-A.png").exists() and (out / "salt-B.png").exists()
