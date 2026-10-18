"""Real browser downloads and clipboard attribution with isolated simulated services."""
from pathlib import Path

import pytest

from test_web_ui import LLM_PROMPTS, add_llm, browser, page, server, submit

pytestmark = pytest.mark.integration
PROJECT = "https://github.com/lainXXX/pianyu"


def test_project_links_copies_and_downloaded_files(page, server):
    add_llm(page, server)
    page.reload()
    page.wait_for_selector("body[data-ready='1']")
    page.context.route(PROJECT, lambda route: route.fulfill(body="GitHub project link"))
    with page.expect_popup() as popup:
        page.get_by_role("link", name="GitHub", exact=True).click()
    popup.value.wait_for_load_state()
    assert popup.value.url == PROJECT
    popup.value.close()
    page.select_option("#style", "notes")
    before = len(LLM_PROMPTS)
    submit(page, "https://www.douyin.com/video/901")
    page.wait_for_selector("#ai-output:not([hidden])", timeout=30000)
    assert all(PROJECT not in prompt for prompt in LLM_PROMPTS[before:])
    page.click("#ai-tab-text")
    page.click("#ai-copy")
    page.wait_for_selector("#ai-copy:has-text('已复制')")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert copied.count(PROJECT) == 1 and "##" not in copied
    page.click(".export-menu > summary")
    with page.expect_download() as exported:
        page.click("#ai-export")
    assert Path(exported.value.path()).read_text(encoding="utf-8").count(PROJECT) == 1
    page.click(".export-menu > summary")
    with page.expect_download() as html:
        page.click("#ai-html")
    assert PROJECT in Path(html.value.path()).read_text(encoding="utf-8")
    page.click("#drawer-open")
    for button in ("#export-txt", "#export-md"):
        with page.expect_download() as source:
            page.click(button)
        assert Path(source.value.path()).read_text(encoding="utf-8").count(PROJECT) == 1
    with page.expect_download() as subtitles:
        page.click("#export-srt")
    assert PROJECT not in Path(subtitles.value.path()).read_text(encoding="utf-8")


@pytest.mark.parametrize("width", [1440, 390])
def test_project_header_layout_and_keyboard_access(page, width):
    page.set_viewport_size({"width": width, "height": 900})
    github = page.get_by_role("link", name="GitHub", exact=True)
    assert github.is_visible() and github.get_attribute("href") == PROJECT
    assert "noopener" in github.get_attribute("rel")
    github.focus()
    assert github.evaluate("el => el === document.activeElement")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("link", name="设置", exact=True).click()
    page.wait_for_selector("body[data-ready='1']")
    assert page.get_by_role("link", name="GitHub", exact=True).is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
