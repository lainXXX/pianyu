"""T43 acceptance (logic): Markdown / structure -> blocks, safe inline HTML, pagination rules."""
from vtx.article import (markdown_blocks, paginate, paragraph_blocks, split_block, structure_blocks)


def test_markdown_blocks_title_lists_quote_tasks():
    md = """# 标题
> 一句话总结

## 小节
第一句。**关键句。**第三句。

- 要点一
- 要点 **二**

1. 步骤一
2. 步骤二

- [ ] 待办
- [x] 已办

---
<script>alert(1)</script> 结尾"""
    title, blocks = markdown_blocks(md)
    assert title == "标题"
    kinds = [b["type"] for b in blocks]
    assert kinds == ["quote", "h2", "p", "ul", "ol", "tasks", "hr", "p"]
    assert blocks[2]["sentences"][1] == "<b>关键句。</b>"            # bold never cut across sentences
    assert "<b>二</b>" in blocks[3]["html"] and "<ol>" in blocks[4]["html"]
    assert "☐" in blocks[5]["html"] and "☑" in blocks[5]["html"]
    assert "<script>" not in blocks[-1]["html"] and "&lt;script&gt;" in blocks[-1]["html"]


def test_structure_and_paragraph_blocks():
    doc = {"sections": [{"heading": "盐<b>", "level": 2, "blocks": [
        {"type": "p", "items": [[{"text": "一。", "key": False}, {"text": "二。", "key": True}]]},
        {"type": "list", "items": [[{"text": "甲。", "key": False}]]}]}]}
    b = structure_blocks(doc)
    assert [x["type"] for x in b] == ["h2", "p", "ul"] and "盐&lt;b&gt;" in b[0]["html"]
    assert b[1]["sentences"] == ["一。", "<b>二。</b>"]
    p = paragraph_blocks([{"text": "第一段一句。第二句。\n第二段。"}])
    assert len(p) == 2 and p[0]["sentences"] == ["第一段一句。", "第二句。"]


def test_split_block_continues_without_indent():
    _, [b] = markdown_blocks("一。二。三。")
    head, tail = split_block(b, 2)
    assert head["sentences"] == ["一。", "二。"] and tail["sentences"] == ["三。"]
    assert str(tail["html"]).startswith('<p class="cont">')


def _blocks(spec):
    """spec: [(type, top, bottom, [sentence bottoms])] -> blocks, measured."""
    blocks = [{"type": t, "sentences": ["x"] * len(s) if t == "p" else []} for t, *_ , s in spec]
    measured = [{"top": top, "bottom": bot, "sents": s} for _, top, bot, s in spec]
    return blocks, measured


def test_paginate_keeps_heading_with_next_and_splits_paragraph():
    blocks, m = _blocks([("p", 0, 400, [400]), ("h2", 400, 460, []), ("p", 460, 1500, [700, 1000, 1300, 1500])])
    pages = paginate(blocks, m, 1500, 900, 900)
    # page 1: first paragraph, heading and the first sentence of the long paragraph (cut at 700)
    assert [b["type"] for b in pages[0]] == ["p", "h2", "p"] and pages[0][2]["sentences"] == ["x"]
    assert len(pages) == 2 and len(pages[1][0]["sentences"]) == 3


def test_paginate_heading_moves_when_no_room_for_text():
    blocks, m = _blocks([("p", 0, 850, [850]), ("h2", 850, 900, []), ("ul", 900, 1300, [])])
    pages = paginate(blocks, m, 1300, 900, 900)
    assert [b["type"] for b in pages[0]] == ["p"] and [b["type"] for b in pages[1]] == ["h2", "ul"]


def test_paginate_oversized_unsplittable_block_gets_own_page():
    blocks, m = _blocks([("p", 0, 100, [100]), ("ul", 100, 3000, []), ("p", 3000, 3100, [3100])])
    pages = paginate(blocks, m, 3100, 900, 900)
    assert [[b["type"] for b in p] for p in pages] == [["p"], ["ul"], ["p"]]


def test_long_list_splits_between_items_and_keeps_numbering():
    _, [ol] = markdown_blocks("1. 一\n2. 二\n3. 三\n4. 四")
    head, tail = split_block(ol, 3)
    assert head["items"] == ["一", "二", "三"] and tail["items"] == ["四"]
    assert 'start="4"' in str(tail["html"]) and "start=" not in str(head["html"])
    m = [{"top": 0, "bottom": 1200, "sents": [300, 600, 900, 1200]}]
    pages = paginate([ol], m, 1200, 700, 700)
    assert [p[0]["items"] for p in pages] == [["一", "二"], ["三", "四"]]
