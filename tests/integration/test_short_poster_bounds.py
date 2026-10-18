"""Real browser crop verification for a minimal output."""
import pytest

from vtx.poster import render_png

pytestmark = pytest.mark.integration


def test_short_auto_poster_does_not_include_viewport_blank_area(tmp_path):
    data = {"title": "两样成品", "conclusion": "没有提供做法。", "points": []}
    result = render_png(data, {"title": "两样成品", "duration": 100}, "steps", tmp_path / "short.png")
    assert 100 < result["height"] < 800


def test_steps_details_are_readable_without_false_warning(tmp_path):
    from playwright.sync_api import sync_playwright

    data = {"title": "拆装记录", "conclusion": "第二次成功。", "points": [{
        "heading": "再次尝试", "body": "成功拆下踏板。", "key": True,
        "detail": ["第二次尝试成功拆下", "拆下后进入维修"], "quotes": [],
    }]}
    html = tmp_path / "steps.html"
    result = render_png(data, {}, "steps", tmp_path / "steps.png", html)
    assert result["height"] > 100
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html.read_text(encoding="utf-8"))
        assert "⚠" not in page.inner_text("body")
        assert page.locator(".details li").all_inner_texts() == data["points"][0]["detail"]
        assert page.locator(".details").evaluate("e => getComputedStyle(e).color") == "rgb(99, 116, 109)"
        browser.close()
