"""Compact settings and focus behavior through real browser interactions."""
import json
from pathlib import Path

import pytest

from test_web_ui import add_llm, browser, page, server

pytestmark = pytest.mark.integration


def same_line(locators):
    boxes = [locator.bounding_box() for locator in locators]
    centers = [box["y"] + box["height"] / 2 for box in boxes]
    assert max(centers) - min(centers) <= 1, boxes


def check_rows(page, selector):
    rows = page.locator(selector)
    assert rows.count()
    for row in rows.all():
        buttons = row.locator("button")
        same_line(buttons.all())
        assert row.bounding_box()["height"] <= 90
        boxes = [button.bounding_box() for button in buttons.all()]
        for left, right in zip(boxes, boxes[1:]):
            assert left["x"] + left["width"] <= right["x"] + 1
        info = row.locator(".setting-item-info")
        if info.count():
            assert info.bounding_box()["x"] + info.bounding_box()["width"] <= boxes[0]["x"] + 1


@pytest.mark.parametrize("width", [1440, 390, 320])
def test_compact_controls_and_settings(page, server, width):
    page.set_viewport_size({"width": width, "height": 900})
    evidence = Path("acceptance/compact-settings")
    evidence.mkdir(parents=True, exist_ok=True)
    page.fill("#url", "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    assert page.locator("#url").evaluate("el => getComputedStyle(el).outlineStyle") == "none"
    same_line([page.locator("#url"), page.locator("#style"), page.locator("#go")])
    page.select_option("#style", "notes")
    assert page.input_value("#style") == "notes"
    page.screenshot(path=str(evidence / f"home-{width}.png"))
    add_llm(page, server)
    document = json.loads(Path("docs/style-package.example.json").read_text(encoding="utf-8"))
    response = page.request.post(server[0] + "/api/style-packages/import", data=document)
    assert response.ok
    imported = response.json()
    page.goto(server[0] + "/settings#outputs")
    page.wait_for_selector('body[data-ready="1"]')
    check_rows(page, "#out-list > li")
    page.screenshot(path=str(evidence / f"outputs-{width}.png"))
    row = page.locator(f'#out-list li[data-output="{imported["output"]}"]')
    row.get_by_role("button", name="编辑：主题笔记", exact=True).click()
    page.fill("#of-name", "独立风格")
    assert page.locator("#of-name").evaluate("el => getComputedStyle(el).outlineStyle") == "none"
    page.get_by_role("button", name="关闭", exact=True).click()
    page.click("#tab-layouts")
    check_rows(page, "#lay-list-article > li, #lay-list-points > li")
    page.screenshot(path=str(evidence / f"layouts-{width}.png"))
    layout = page.locator("#lay-list-article li").filter(has=page.locator(f'button[data-id="{imported["layouts"][0]}"]'))
    layout.get_by_role("button", name="预览：纸页", exact=True).click()
    page.wait_for_function("document.getElementById('tpl-preview-img').naturalWidth > 0")
    page.get_by_role("button", name="关闭预览", exact=True).click()
    layout.get_by_role("button", name="编辑：纸页", exact=True).click()
    choices = page.locator("#tf-output-boxes label")
    assert choices.count() >= 2
    same_line(choices.all()[:2])
    choices.first.click()
    page.screenshot(path=str(evidence / f"bindings-{width}.png"))
    page.get_by_role("button", name="关闭", exact=True).click()
    page.on("dialog", lambda dialog: dialog.accept())
    page.click("#tab-outputs")
    row.get_by_role("button", name="删除：主题笔记", exact=True).click()
    row.wait_for(state="detached")
    page.click("#tab-layouts")
    layout.get_by_role("button", name="删除：纸页", exact=True).click()
    layout.wait_for(state="detached")
    for tab, selector in [("asr", "#engines tbody tr"), ("llm", "#llms tbody tr")]:
        page.click("#tab-" + tab)
        check_rows(page, selector)
        page.screenshot(path=str(evidence / f"{tab}-{width}.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
