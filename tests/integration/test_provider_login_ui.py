"""Provider navigation and platform login states, driven through installed Edge.

Login endpoints are synthetic: tests never open a third-party login or read cookies.
"""
from pathlib import Path

import pytest
from test_web_ui import browser, server

pytestmark = pytest.mark.integration


@pytest.fixture
def page(browser, server):
    context = browser.new_context(viewport={"width": 1440, "height": 1050})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/api/capabilities", lambda route: route.fulfill(json={
        "reveal_keys": True, "browser_login": True, "diagnostics": True,
    }))
    page.route("**/api/diagnostics", lambda route: route.fulfill(json={
        "paths": {"config": r"D:\demo\config", "work": r"D:\demo\workspace", "logs": r"D:\demo\logs"},
        "log_files": ["app.jsonl", "errors.jsonl"],
    }))
    yield page
    context.close()
    assert errors == []


def open_settings(page, server, section):
    page.goto(server[0] + "/settings#" + section)
    page.wait_for_selector('body[data-ready="1"]')


def new_editor(page, form):
    page.locator("details", has=page.locator("#" + form)).locator(":scope > summary").click()


def test_same_document_hash_navigation_and_history(page, server):
    open_settings(page, server, "cookie")
    page.fill("#proxy", "http://127.0.0.1:7898")
    page.evaluate("window.hashEvents = 0; addEventListener('hashchange', () => hashEvents++)")
    page.goto(server[0] + "/settings#project")
    page.wait_for_selector("#panel-project:not([hidden])")
    assert page.locator("#tab-project").get_attribute("aria-selected") == "true"
    assert page.locator("#panel-cookie").is_hidden()
    page.go_back()
    page.wait_for_selector("#panel-cookie:not([hidden])")
    assert page.input_value("#proxy") == "http://127.0.0.1:7898"
    page.go_forward()
    page.wait_for_selector("#panel-project:not([hidden])")
    assert page.evaluate("hashEvents") == 3
    page.goto(server[0] + "/settings#unknown")
    page.wait_for_selector("#panel-asr:not([hidden])")
    assert page.url.endswith("#asr")
    assert page.evaluate("hashEvents") == 4


def test_provider_links_change_with_preset_and_custom(page, server):
    open_settings(page, server, "asr")
    new_editor(page, "engine-form")
    page.select_option("#ef-preset", "openai-transcribe")
    assert page.input_value("#ef-model") == "gpt-4o-mini-transcribe"
    assert page.get_by_role("navigation", name="识别厂商链接").get_by_role("link", name="API Key").get_attribute("href") == "https://platform.openai.com/api-keys"
    assert page.locator("#ef-provider-links a").count() == 3
    for link in page.locator("#ef-provider-links a").all():
        assert link.get_attribute("target") == "_blank"
        assert "noopener" in link.get_attribute("rel")
    page.select_option("#ef-preset", "custom")
    assert page.locator("#ef-provider-links").is_hidden()
    assert page.input_value("#ef-url") == ""
    page.click("#tab-llm"); new_editor(page, "llm-form")
    page.select_option("#lf-preset", "minimax")
    assert page.input_value("#lf-url") == "https://api.minimax.io/v1"
    assert page.locator("#lf-provider-links a").count() == 3
    page.context.route("https://platform.minimax.io/**", lambda route: route.fulfill(body="Provider key console", content_type="text/plain"))
    with page.expect_popup() as opened:
        page.locator("#lf-provider-links").get_by_role("link", name="API Key").click()
    popup = opened.value
    popup.wait_for_load_state()
    assert popup.url.startswith("https://platform.minimax.io/")
    assert popup.locator("body").inner_text() == "Provider key console"
    popup.close()
    page.select_option("#lf-preset", "siliconflow")
    assert "siliconflow.cn" in page.locator("#lf-provider-links a").first.get_attribute("href")
    page.select_option("#lf-preset", "custom")
    assert page.locator("#lf-provider-links").is_hidden()


@pytest.mark.parametrize("width", [1440, 390])
def test_cookie_login_waiting_success_reload_and_visual(page, server, width):
    page.set_viewport_size({"width": width, "height": 950})
    saved = {"bilibili": False, "douyin": False, "youtube": False}
    page.route("**/api/settings", lambda route: route.fulfill(json={"cookies": dict(saved), "proxy_youtube": ""}))
    page.route("**/api/settings/cookies/bilibili/login", lambda route: route.fulfill(json={"id": "synthetic-login", "platform": "bilibili", "status": "waiting"}))
    state = {"status": "waiting"}

    def status(route):
        if state["status"] == "connected":
            saved["bilibili"] = True
        route.fulfill(json={"id": "synthetic-login", "platform": "bilibili", "status": state["status"]})

    page.route("**/api/settings/cookie-logins/synthetic-login", status)
    open_settings(page, server, "cookie")
    assert page.locator("#cookie-platforms input[type=file]").count() == 0
    row = page.locator('[data-platform="bilibili"]')
    row.get_by_role("button", name="登录 B站", exact=True).click()
    row.get_by_text("等待登录…", exact=True).wait_for()
    page.click("#tab-llm"); page.click("#tab-cookie")
    assert row.get_attribute("aria-busy") == "true"
    page.reload(); page.wait_for_selector('body[data-ready="1"]')
    row.get_by_text("等待登录…", exact=True).wait_for()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    out = Path("acceptance/product-settings"); out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / f"login-waiting-{width}.png"), full_page=True)
    state["status"] = "connected"
    row.get_by_text("已保存", exact=True).wait_for()
    assert row.get_attribute("aria-busy") == "false"
    assert row.get_by_role("button", name="重新登录 B站", exact=True).is_enabled()
    assert not page.evaluate("sessionStorage.getItem('vtx-cookie-login-bilibili')")
    page.screenshot(path=str(out / f"login-connected-{width}.png"), full_page=True)


def test_cookie_login_cancel_and_failure_are_local_to_row(page, server):
    page.route("**/api/settings/cookies/douyin/login", lambda route: route.fulfill(json={"id": "cancel-me", "platform": "douyin", "status": "waiting"}))
    page.route("**/api/settings/cookie-logins/cancel-me", lambda route: route.fulfill(json={"id": "cancel-me", "platform": "douyin", "status": "cancelled" if route.request.method == "DELETE" else "waiting"}))
    page.route("**/api/settings/cookies/youtube/login", lambda route: route.fulfill(json={"id": "failed", "platform": "youtube", "status": "failed", "error": "浏览器启动失败，请重试"}))
    open_settings(page, server, "cookie")
    row = page.locator('[data-platform="douyin"]')
    row.get_by_role("button", name="登录 抖音", exact=True).click()
    row.get_by_role("button", name="取消 抖音", exact=True).click()
    row.get_by_role("button", name="登录 抖音", exact=True).wait_for()
    assert not page.evaluate("sessionStorage.getItem('vtx-cookie-login-douyin')")
    failed = page.locator('[data-platform="youtube"]')
    failed.get_by_role("button", name="登录 YouTube", exact=True).click()
    failed.get_by_text("浏览器启动失败，请重试", exact=True).wait_for()
    assert failed.get_by_role("button", name="登录 YouTube", exact=True).is_enabled()
    assert "浏览器启动失败" not in row.inner_text()


def test_project_paths_and_local_capabilities(page, server):
    open_settings(page, server, "project")
    assert page.locator("#panel-project").is_visible()
    assert page.locator("#project-paths dd").count() == 3
    assert "app.jsonl" in page.locator("#project-logs").inner_text()
    page.route("**/api/capabilities", lambda route: route.fulfill(json={"reveal_keys": False, "browser_login": False, "diagnostics": False}))
    page.reload(); page.wait_for_selector('body[data-ready="1"]')
    assert page.locator("#tab-project").is_hidden()
    assert page.locator("#panel-asr").is_visible()
    page.click("#tab-cookie")
    assert page.get_by_role("button", name="登录 YouTube", exact=True).is_disabled()


@pytest.mark.parametrize("width", [1440, 390])
def test_provider_and_project_visual(page, server, width):
    page.set_viewport_size({"width": width, "height": 1100})
    open_settings(page, server, "llm"); new_editor(page, "llm-form")
    page.select_option("#lf-preset", "minimax")
    out = Path("acceptance/product-settings"); out.mkdir(parents=True, exist_ok=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(out / f"provider-{width}.png"), full_page=True)
    page.click("#tab-project")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(out / f"project-{width}.png"), full_page=True)
