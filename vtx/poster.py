"""Knowledge posters (design 4.10 / 4.11): one image holding the whole structure of a video.

Layouts are templates (vtx.poster_templates): built-in A = tall card (1080 wide), B = landscape mind map
(1600 wide), plus user copies. Output is self-contained HTML (inline CSS, no scripts, no external
resources), all text escaped. No timestamps, no model names, no dates.
"""
from pathlib import Path
from typing import Optional

from vtx import frame

A_WIDTH, A_MAX_HEIGHT, B_WIDTH = 1080, 8000, 1600
FOOTER_TITLE_MAX = 28   # long titles were clipping the disclaimer off the one-line footer (T31)


def footer_text(meta: dict, template: Optional[str] = None) -> str:
    """Poster footer from the (editable) template; long titles are shortened so the line still fits."""
    title = meta.get("title", "")
    if len(title) > FOOTER_TITLE_MAX:
        title = title[:FOOTER_TITLE_MAX - 1] + "…"
    return frame.render(template if template is not None else frame.DEFAULTS["poster_footer"], {**meta, "title": title})


def _weight(p: dict) -> int:
    """Rough rendered height of a B card: header + text lines."""
    text = len(p.get("body", "")) + sum(len(d) + 12 for d in p.get("detail") or [])
    return 60 + text


def balanced_split(points: list) -> int:
    """Index splitting points (order kept) into two columns of similar height."""
    if len(points) < 2:
        return len(points)
    total = sum(_weight(p) for p in points)
    best, best_gap, acc = 1, None, 0
    for i in range(1, len(points)):
        acc += _weight(points[i - 1])
        gap = abs(total - 2 * acc)
        if best_gap is None or gap < best_gap:
            best, best_gap = i, gap
    return best


def _template(layout) -> dict:
    """layout: a template dict, or "A" / "B" for the built-in defaults."""
    from vtx.poster_templates import _builtins

    if isinstance(layout, dict):
        return layout
    t = next((b for b in _builtins() if b["id"] == layout), None)
    if not t:
        raise ValueError(f"unknown layout: {layout}")
    return t


def render_html(data: dict, meta: dict, layout="A", max_points: Optional[int] = None,
                footer: Optional[str] = None) -> str:
    """footer: already-rendered footer text (one line per row); default = the built-in poster footer."""
    from vtx import poster_templates

    footer = footer_text(meta) if footer is None else footer
    return poster_templates.render(_template(layout)["html"], poster_templates.context(data, meta, footer, max_points))


def render_png(data: dict, meta: dict, layout, out_png: Path, out_html: Optional[Path] = None,
               footer: Optional[str] = None) -> dict:
    """Screenshot with headless Edge/Chromium. Auto layouts preserve every point."""
    from playwright.sync_api import sync_playwright

    from vtx.poster_templates import parse_height

    t = _template(layout)
    width, fixed = t["width"], parse_height(t["height_mode"])
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page(viewport={"width": width, "height": fixed or 1}, device_scale_factor=1)
            html = render_html(data, meta, t, footer=footer)
            page.set_content(html, wait_until="load")
            height = page.evaluate("document.documentElement.scrollHeight")
            shown = len(data["points"])
            out_h = fixed or height
            page.screenshot(path=str(out_png), clip={"x": 0, "y": 0, "width": width, "height": out_h}, full_page=True)
        finally:
            browser.close()
    if out_html:
        out_html.write_text(html, encoding="utf-8", newline="\n")
    return {"width": width, "height": round(out_h), "points": shown}


def _launch(p):
    for kwargs in ({"channel": "msedge"}, {}):
        try:
            return p.chromium.launch(headless=True, **kwargs)
        except Exception:
            continue
    raise RuntimeError("找不到可用的 Edge/Chromium，无法生成 PNG")
