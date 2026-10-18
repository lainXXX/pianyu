"""T43 acceptance: real article pages with headless Edge (no LLM)."""
import struct

import pytest

from vtx.article import markdown_blocks, render_pages
from vtx.layouts_article import ARTICLE

pytestmark = pytest.mark.integration

LONG = "# 长文\n\n" + "\n\n".join(
    (f"## 第 {k} 节\n\n" if k % 3 == 0 else "") + "这是一句用来占位的原文，内容讲的是一个知识点。" * 14
    for k in range(1, 40))


def size(path):
    head = open(path, "rb").read()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


@pytest.mark.parametrize("tpl", ARTICLE, ids=[t["id"] for t in ARTICLE])
def test_long_text_paginates_within_page_height(tmp_path, tpl):
    title, blocks = markdown_blocks(LONG)
    r = render_pages(tpl, title, "B站 · 测试", blocks, "来源：测试\nhttps://example.com/v", tmp_path, "t")
    limit = int(tpl["height_mode"].split(":")[1])
    sizes = [size(p) for p in r["pngs"]]
    assert r["pages"] > 3 and all(w == tpl["width"] and h <= limit for w, h in sizes)
    assert all(h >= limit * 0.6 for _, h in sizes[:-1])            # pages are filled, not one block each
    html = open(r["html"], encoding="utf-8").read()
    assert "<script" not in html and html.count("这是一句用来占位的原文") == 39 * 14


def test_short_text_is_one_page_with_footer(tmp_path):
    title, blocks = markdown_blocks("# 短\n\n一句话。")
    r = render_pages(ARTICLE[0], title, "k", blocks, "页脚一行", tmp_path, "s")
    assert r["pages"] == 1 and "页脚一行" in open(r["html"], encoding="utf-8").read()


@pytest.mark.parametrize("tpl", ARTICLE, ids=[t["id"] for t in ARTICLE])
def test_paragraphs_and_subheadings_keep_readable_spacing(tmp_path, tpl):
    from playwright.sync_api import sync_playwright

    md = "# 阅读间距\n\n" + "第一段结束。" * 6 + "\n\n" + "第二段开始。" * 6 + "\n\n### 新章节\n\n" + "章节正文。" * 6
    title, blocks = markdown_blocks(md)
    result = render_pages(tpl, title, "来源", blocks, "页脚", tmp_path, "spacing")
    assert result["pages"] == 1
    assert size(result["pngs"][0])[1] <= int(tpl["height_mode"].split(":")[1])
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": tpl["width"], "height": 400})
        page.set_content(open(result["html"], encoding="utf-8").read())
        geometry = page.eval_on_selector(".content", """e => [...e.children].map(x => ({
          top: x.getBoundingClientRect().top, bottom: x.getBoundingClientRect().bottom,
          font: parseFloat(getComputedStyle(x).fontSize),
          padding: parseFloat(getComputedStyle(x).paddingTop)
        }))""")
        assert geometry[1]["top"] - geometry[0]["bottom"] >= geometry[0]["font"]
        assert geometry[2]["padding"] >= geometry[2]["font"] * 0.5
        assert all(b["top"] >= a["bottom"] for a, b in zip(geometry, geometry[1:]))
        browser.close()
