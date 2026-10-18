"""Style packages through real Edge file selection and browser downloads."""
import json
from pathlib import Path

import pytest

from test_web_ui import server, browser

pytestmark = pytest.mark.integration


@pytest.fixture
def settings_page(browser, server):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(server[0] + "/settings#outputs")
    page.wait_for_selector('body[data-ready="1"]')
    yield page
    context.close()
    assert errors == []


def package(name):
    html = ('<!doctype html><html><head><meta charset="utf-8"><style>'
            'body{margin:0;padding:60px;background:#fffdf8;font:26px/1.8 sans-serif}'
            '</style></head><body><h1>{{ title }}</h1><main class="content">'
            '{% for b in blocks %}{{ b.html|safe }}{% endfor %}</main></body></html>')
    return {"format": "pianyu-style", "version": 1,
            "style": {"name": name, "kind": "free", "prompt": "# 整理\n保留论证与例子。\n{text}",
                      "layouts": ["note", "paper"]},
            "layouts": [{"id": lid, "name": label, "group": "article", "html": html,
                         "width": 1080, "height_mode": "fixed:1440"}
                        for lid, label in [("paper", "纸上"), ("note", "简笺")]]}


def upload(page, path):
    with page.expect_file_chooser() as chooser:
        page.get_by_role("button", name="导入", exact=True).click()
    chooser.value.set_files(str(path))


@pytest.mark.parametrize("width", [1280, 390])
def test_import_export_independent_ids_and_preview(settings_page, server, tmp_path, width):
    p = settings_page
    p.set_viewport_size({"width": width, "height": 900})
    name = f"自带版式 {width}"
    document = package(name)
    source = tmp_path / "style.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    before = p.request.get(server[0] + "/api/outputs?hidden=true").json()["outputs"]
    upload(p, source)
    p.wait_for_selector('#out-list-msg:has-text("已导入")')
    created = p.locator("#out-list > li", has_text=name)
    assert created.count() == 1
    output_id = created.get_attribute("data-output")
    assert p.evaluate("document.activeElement.dataset.output") == output_id
    assert p.evaluate("document.documentElement.scrollWidth <= innerWidth")
    after = p.request.get(server[0] + "/api/outputs?hidden=true").json()["outputs"]
    assert all(output in after for output in before)
    imported = next(output for output in after if output["id"] == output_id)
    assert len(imported["layouts"]) == 2
    assert not set(imported["layouts"]) & {"note", "paper"}

    with p.expect_download() as download:
        created.get_by_role("button", name=f"导出：{name}", exact=True).press("Enter")
    result = json.loads(Path(download.value.path()).read_text(encoding="utf-8"))
    assert result["format"] == "pianyu-style"
    assert result["style"]["name"] == name
    assert result["style"]["prompt"] == document["style"]["prompt"]
    assert len(result["layouts"]) == 2
    assert result["style"]["layouts"][0] == next(layout["id"] for layout in result["layouts"] if layout["name"] == "简笺")

    evidence = Path("acceptance/style-packages")
    evidence.mkdir(parents=True, exist_ok=True)
    p.evaluate("window.scrollTo(0, 0)")
    p.screenshot(path=str(evidence / f"settings-{width}.png"))
    created.get_by_role("button", name=f"编辑：{name}", exact=True).click()
    assert p.input_value("#of-name") == name
    selected = p.locator("#of-layout-boxes input:checked")
    assert selected.first.get_attribute("value") == imported["layouts"][0]
    p.screenshot(path=str(evidence / f"imported-editor-{width}.png"))
    p.get_by_role("button", name="关闭", exact=True).click()

    # A second import must add a fresh style and fresh layouts, even with the same names.
    upload(p, source)
    p.wait_for_function("name => [...document.querySelectorAll('#out-list > li')].filter(li => li.textContent.includes(name)).length === 2", arg=name)
    outputs = p.request.get(server[0] + "/api/outputs?hidden=true").json()["outputs"]
    copies = [output for output in outputs if output["name"] == name]
    assert len(copies) == 2 and copies[0]["id"] != copies[1]["id"]
    assert not set(copies[0]["layouts"]) & set(copies[1]["layouts"])
    p.click("#tab-layouts")
    layout_row = p.locator("#lay-list-article li").filter(has=p.locator(f'button[data-id="{imported["layouts"][0]}"]'))
    layout_row.get_by_role("button", name="预览：简笺", exact=True).click()
    p.wait_for_selector("#tpl-preview-img:not([hidden])")
    p.wait_for_function("document.getElementById('tpl-preview-img').naturalWidth > 0")
    p.screenshot(path=str(evidence / f"imported-preview-{width}.png"))
    assert not p.locator("#tpl-form").is_visible()


def test_import_failures_leave_existing_data_and_can_retry(settings_page, server, tmp_path):
    p = settings_page
    before_outputs = p.request.get(server[0] + "/api/outputs?hidden=true").json()
    before_layouts = p.request.get(server[0] + "/api/layouts").json()
    malformed = tmp_path / "bad.json"
    malformed.write_text("not JSON", encoding="utf-8")
    upload(p, malformed)
    p.wait_for_selector('#out-list-msg:has-text("有效的 JSON")')
    assert p.input_value("#out-package-file") == ""
    oversized = tmp_path / "large.json"
    oversized.write_text(" " * (1024 * 1024 + 1), encoding="utf-8")
    upload(p, oversized)
    p.wait_for_selector('#out-list-msg:has-text("1 MB")')
    invalid = package("无效风格")
    invalid["style"]["layouts"] = ["missing"]
    malformed.write_text(json.dumps(invalid), encoding="utf-8")
    with p.expect_response("**/api/style-packages/import") as response:
        upload(p, malformed)
    assert response.value.status == 422
    p.wait_for_function("!document.getElementById('out-import').disabled")
    assert p.request.get(server[0] + "/api/outputs?hidden=true").json() == before_outputs
    assert p.request.get(server[0] + "/api/layouts").json() == before_layouts
    assert not p.locator("#out-form").is_visible()
    # Import failures leave independent new-style creation ready to use.
    p.get_by_role("button", name="新建输出风格", exact=True).click()
    assert p.input_value("#of-name") == "" and p.input_value("#of-prompt") == ""


def test_export_failure_and_project_links(settings_page):
    p = settings_page
    github = p.locator("header .project-link")
    assert github.get_attribute("href") == "https://github.com/lainXXX/pianyu"
    assert github.get_attribute("target") == "_blank"
    assert "noopener" in github.get_attribute("rel")
    assert p.get_by_role("link", name="指南 ↗").get_attribute("href").endswith("/docs/style-guide.md")
    p.route("**/api/outputs/*/package", lambda route: route.fulfill(status=500, json={"detail": "导出失败"}))
    export = p.locator("#out-list [data-action=export]").first
    export.click()
    p.wait_for_selector('#out-list-msg:has-text("导出失败")')
    assert export.is_enabled()
