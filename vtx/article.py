"""Article images (design 4.12): 原文类 and 自由类 results as readable, paginated pages.

Content becomes a list of blocks; each block is exactly one HTML element (h2 / h3 / p / ul / ol /
blockquote / hr) with its text already escaped. An article template (Jinja2, sandboxed like posters)
must place them inside an element with class "content":
    <main class="content">{% for b in blocks %}{{ b.html }}{% endfor %}</main>
and may use: title, kicker, blocks, footer_lines, page, pages, first, last.
Pagination measures the blocks once, then fills pages greedily: a block is never split, and a
heading is never left at the bottom of a page.
"""
import re
from html import escape
from pathlib import Path
from typing import List, Optional

from markupsafe import Markup

_HEAD = re.compile(r"^(#{1,6})\s+(.*)$")
_TASK = re.compile(r"^[-*]\s+\[( |x|X)\]\s+(.*)$")
_BULLET = re.compile(r"^[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\d+[.)、]\s+(.*)$")


def inline(text: str) -> str:
    """Escape, then the few inline marks models use: **bold**, *italic*, `code`."""
    s = escape(text, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])", r"<i>\1</i>", s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def _block(kind: str, html: str) -> dict:
    return {"type": kind, "html": Markup(html)}


_SENT = re.compile(r"(?<=[。！？!?；;])")


def _para(sentences_html: List[str]) -> dict:
    """A paragraph whose sentences can be split across pages (see split_block)."""
    return {"type": "p", "sentences": [Markup(h) for h in sentences_html],
            "html": Markup("<p>" + "".join(f'<span class="s">{h}</span>' for h in sentences_html) + "</p>")}


def _list(kind: str, items_html: List[str], start: int = 1) -> dict:
    """ul / ol / tasks list whose items can be split across pages."""
    tag = "ol" if kind == "ol" else "ul"
    attrs = (' class="tasks"' if kind == "tasks" else "") + (f' start="{start}"' if tag == "ol" and start > 1 else "")
    return {"type": kind, "items": [Markup(h) for h in items_html], "start": start,
            "html": Markup(f"<{tag}{attrs}>" + "".join(f"<li>{h}</li>" for h in items_html) + f"</{tag}>")}


def pieces(block: dict) -> list:
    return block.get("sentences") or block.get("items") or []


def split_block(block: dict, k: int) -> tuple:
    """Paragraph or list -> (first k pieces, the rest). A paragraph continues without indent,
    an ordered list keeps its numbering."""
    if "items" in block:
        items = [str(x) for x in block["items"]]
        start = block.get("start", 1)
        return _list(block["type"], items[:k], start), _list(block["type"], items[k:], start + k)
    sents = block["sentences"]
    head = _para([str(x) for x in sents[:k]])
    tail = _para([str(x) for x in sents[k:]])
    tail["html"] = Markup(str(tail["html"]).replace("<p>", '<p class="cont">', 1))
    return head, tail


def _md_sentences(text: str) -> List[str]:
    """Sentence pieces of a Markdown paragraph, never cutting inside **bold**."""
    pieces, buf = [], ""
    for k, seg in enumerate(re.split(r"(\*\*.+?\*\*)", text)):
        parts = [seg] if k % 2 else _SENT.split(seg)       # odd segments are **bold** spans: never cut
        for part in parts:
            buf += part
            if re.search(r"[。！？!?；;](\*\*)?$", buf):
                pieces.append(buf)
                buf = ""
    if buf:
        pieces.append(buf)
    return [p for p in pieces if p]


def markdown_blocks(md: str) -> tuple:
    """Markdown (the subset LLMs produce) -> (title or "", blocks). The first # heading is the title."""
    title, blocks = "", []
    para, items, kind, quote = [], [], None, []

    def flush():
        nonlocal para, items, kind, quote
        if para:
            if len(para) == 1:
                blocks.append(_para([inline(c) for c in _md_sentences(para[0])]))
            else:
                blocks.append(_block("p", "<p>" + "<br>".join(inline(l) for l in para) + "</p>"))
        if items:
            blocks.append(_list(kind, items))
        if quote:
            blocks.append(_block("quote", "<blockquote>" + "<br>".join(inline(l) for l in quote) + "</blockquote>"))
        para, items, kind, quote = [], [], None, []

    for raw in (md or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        m = _HEAD.match(line)
        if m:
            flush()
            level, text = len(m.group(1)), m.group(2).strip()
            if level == 1 and not title and not blocks:
                title = text
            else:
                tag = "h2" if level <= 2 else "h3"
                blocks.append(_block(tag, f"<{tag}>{inline(text)}</{tag}>"))
            continue
        if re.fullmatch(r"[-*_]{3,}", line):
            flush()
            blocks.append(_block("hr", "<hr>"))
            continue
        if line.startswith(">"):
            if para or items:
                flush()
            quote.append(line.lstrip(">").strip())
            continue
        for pattern, k, fmt in ((_TASK, "tasks", None), (_BULLET, "ul", None), (_ORDERED, "ol", None)):
            m = pattern.match(line)
            if m:
                if para or quote or (kind and kind != k):
                    flush()
                kind = k
                if k == "tasks":
                    box = "☑" if m.group(1).lower() == "x" else "☐"
                    items.append(f'<span class="box">{box}</span>{inline(m.group(2))}')
                else:
                    items.append(inline(m.group(1)))
                break
        else:
            if items or quote:
                flush()
            para.append(line)
    flush()
    return title, blocks


def structure_blocks(doc: dict) -> List[dict]:
    """结构化原文 doc (vtx.structure) -> blocks. Key sentences are bold."""
    def render(item):
        return "".join(f"<b>{escape(s['text'])}</b>" if s.get("key") else escape(s["text"]) for s in item)

    blocks = []
    for sec in doc.get("sections") or []:
        if sec.get("heading"):
            tag = "h3" if sec.get("level") == 3 else "h2"
            blocks.append(_block(tag, f"<{tag}>{escape(sec['heading'])}</{tag}>"))
        for b in sec["blocks"]:
            if b["type"] == "list":
                blocks.append(_list("ul", [render(it) for it in b["items"]]))
            else:
                blocks += [_para([render([sent]) for sent in it]) for it in b["items"] if it]
    return blocks


def paragraph_blocks(paragraphs: List[dict]) -> List[dict]:
    """精校原文 paragraphs (vtx.refine) -> blocks; model-inserted line breaks become paragraphs."""
    out = []
    for p in paragraphs:
        for piece in p["text"].split("\n"):
            if piece.strip():
                out.append(_para([escape(x) for x in _SENT.split(piece.strip()) if x]))
    return out


def context(title: str, kicker: str, blocks: List[dict], footer: str, page: int = 1, pages: int = 1) -> dict:
    return {"title": title, "kicker": kicker, "blocks": blocks, "page": page, "pages": pages,
            "first": page == 1, "last": page == pages, "footer_lines": [l for l in footer.split("\n") if l]}


def kicker_text(meta: dict, label: str) -> str:
    from vtx.frame import PLATFORMS, duration_text

    bits = [PLATFORMS.get(meta.get("platform"), meta.get("platform") or ""),
            "@" + meta["author"] if meta.get("author") else "", duration_text(meta.get("duration")), label]
    return " · ".join(b for b in bits if b)


MEASURE_JS = """() => {
  const c = document.querySelector('.content');
  if (!c) return null;
  const top = c.getBoundingClientRect().top;
  const rel = r => r - top;
  return {blocks: [...c.children].map(k => ({top: rel(k.getBoundingClientRect().top),
                                             bottom: rel(k.getBoundingClientRect().bottom),
                                             sents: [...k.querySelectorAll(':scope > span.s, :scope > li')].map(
                                               x => rel(x.getClientRects()[x.getClientRects().length - 1].bottom))})),
          end: c.scrollHeight, height: document.documentElement.scrollHeight};
}"""


def paginate(blocks: List[dict], measured: List[dict], end: float, room_first: float, room_other: float) -> List[list]:
    """Pages of blocks. Blocks move whole, except paragraphs (cut between sentences) and lists (between items).
    A heading never ends a page; a block taller than a page and not splittable gets a page to itself."""
    tops = [m["top"] for m in measured] + [end]
    pages, cur, room, base = [], [], room_first, 0.0      # base: content offset where this page starts
    for i, b in enumerate(blocks):
        sents = measured[i]["sents"]
        part, k0 = b, 0                                   # remaining piece; sentences already placed
        while True:
            if tops[i + 1] - base <= room:
                cur.append(part)
                break
            if len(pieces(b)) - k0 > 1:
                fit = [k for k in range(k0 + 1, len(sents)) if sents[k - 1] - base <= room]
                if fit:
                    k = fit[-1]
                    head, part = split_block(part, k - k0)
                    pages.append(cur + [head])
                    cur, room, base, k0 = [], room_other, sents[k - 1], k
                    continue
            if all(x["type"] in ("h2", "h3") for x in cur):
                cur.append(part)                          # fresh page (or only a heading): take it as is
                break
            carry = []
            while cur[-1]["type"] in ("h2", "h3"):
                carry.insert(0, cur.pop())                # keep headings with the text after them
            pages.append(cur)
            start = tops[i - len(carry)] if carry else (sents[k0 - 1] if k0 else tops[i])
            cur, room, base = carry, room_other, start
    if cur:
        pages.append(cur)
    return pages or [[]]


def render_pages(template: dict, title: str, kicker: str, blocks: List[dict], footer: str,
                 out_dir: Path, stem: str) -> dict:
    """-> {"pngs": [paths], "html": path, "width", "pages"}. template: an article template dict."""
    from playwright.sync_api import sync_playwright

    from vtx.poster import _launch
    from vtx.poster_templates import parse_height, render

    width = template["width"]
    page_h = parse_height(template["height_mode"]) or 2400
    out_dir.mkdir(parents=True, exist_ok=True)
    full = render(template["html"], context(title, kicker, blocks, footer))
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(full, encoding="utf-8", newline="\n")
    pngs = []
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": width, "height": 400}, device_scale_factor=1)

            def chrome(first: bool, last: bool) -> float:
                """Height of a page with no blocks: header, footer, padding."""
                ctx = {**context(title, kicker, [], footer), "first": first, "last": last, "page": 1, "pages": 9}
                page.set_content(render(template["html"], ctx), wait_until="load")
                return page.evaluate(MEASURE_JS)["height"]

            # which page is last is unknown until we paginate, so each page reserves room for the larger case
            room_first = page_h - max(chrome(True, False), chrome(True, True))
            room_other = page_h - max(chrome(False, False), chrome(False, True))
            page.set_content(full, wait_until="load")
            m = page.evaluate(MEASURE_JS)
            if m is None:
                raise ValueError('文章模板里缺少 class="content" 的容器')
            groups = paginate(blocks, m["blocks"], m["end"], room_first, room_other)
            n = len(groups)
            for k, g in enumerate(groups, 1):
                page.set_content(render(template["html"], context(title, kicker, g, footer, k, n)), wait_until="load")
                png = out_dir / f"{stem}-{k}.png"
                page.screenshot(path=str(png), full_page=True)
                pngs.append(str(png))
        finally:
            browser.close()
    return {"pngs": pngs, "html": str(html_path), "width": width, "pages": len(pngs)}
