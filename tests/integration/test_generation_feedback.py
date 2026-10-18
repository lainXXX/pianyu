"""Generation feedback through real Edge interactions, with isolated job data."""
import time
from pathlib import Path

import pytest
from test_web_ui import META, browser, server


pytestmark = pytest.mark.integration


@pytest.fixture(params=[(1440, 900), (390, 844)], ids=["desktop", "mobile"])
def feedback_page(browser, server, request):
    context = browser.new_context(
        viewport={"width": request.param[0], "height": request.param[1]},
        permissions=["clipboard-read", "clipboard-write"],
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    response = page.request.post(server[0] + "/api/jobs", data={
        "url": "https://www.douyin.com/video/901", "engine": "dashscope", "workflow": [],
    })
    assert response.ok, response.text()
    job_id = response.json()["id"]
    for _ in range(100):
        job = page.request.get(server[0] + "/api/jobs/" + job_id).json()
        if job["status"] == "done":
            break
        time.sleep(0.05)
    assert job["status"] == "done", job
    control = {"phase": "asr", "ai_phase": "generate"}
    run = {
        "id": "feedback-run-" + job_id, "output": "notes", "kind": "points",
        "name": "学习笔记", "created_at": 1, "status": "done", "layouts": [],
        "renders": {}, "text": "盐是重要的电解质。", "html": "<p>盐是重要的电解质。</p>",
        "service": "验收模型", "service_id": "feedback-model", "model": "fake",
        "usage": {"total_tokens": 10},
    }

    def job_response(route):
        if control["phase"] == "poll-error":
            route.fulfill(status=503, json={"detail": "模拟进度读取失败"})
            return
        if control["phase"] == "asr":
            payload = {"id": job_id, "status": "running", "stage": "asr", "info": META, "ai": []}
        else:
            pending = dict(run, status="running", progress={
                "phase": control["ai_phase"], "index": 1, "total": 3,
            })
            completed = dict(run, status="failed", error={"code": "LLM_NETWORK"}) if control["phase"] == "failed" else run
            payload = dict(job, ai=[completed if control["phase"] in ("done", "failed") else pending])
        route.fulfill(json=payload)

    page.route("**/api/llm/services", lambda route: route.fulfill(json={
        "default": "feedback-model", "services": [{
            "id": "feedback-model", "configured": True, "label": "验收模型", "model": "fake",
        }],
    }))
    page.route("**/api/jobs", lambda route: route.fulfill(json={"id": job_id}))
    page.route("**/api/jobs/" + job_id, job_response)
    page.route("**/api/llm/runs/" + run["id"], lambda route: route.fulfill(json=(
        dict(run, status="failed", error={"code": "LLM_NETWORK"}) if control["phase"] == "failed" else run
    )))
    page.goto(server[0] + "/")
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#style option[value=notes]", state="attached")
    page.wait_for_function("document.getElementById('engine').value !== ''")
    page.select_option("#style", "notes")
    yield page, control, run
    context.close()
    assert not errors, errors


def start(page):
    page.fill("#url", "https://www.douyin.com/video/901")
    page.click("#go")
    page.wait_for_selector("body[data-state=proc]")
    page.wait_for_selector("#stages [aria-current=step]")


def ai_pending(page, control):
    start(page)
    control["phase"] = "ai"
    page.wait_for_selector("body[data-state=done]")
    page.wait_for_selector("#preview-source:not([hidden])")


def active_animations(page, selector):
    return page.locator(selector).evaluate("""root => [root, ...root.querySelectorAll('*')].flatMap(el =>
        [null, '::before', '::after'].map(pseudo => getComputedStyle(el, pseudo))
    ).filter(style => style.animationName !== 'none' &&
        style.animationDuration.split(',').some(duration => parseFloat(duration) > 0)).length""")


def screenshot(page, name):
    directory = Path(__file__).resolve().parents[2] / "acceptance" / "generation-feedback"
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / f"{name}-{page.viewport_size['width']}.png"), full_page=True)


def test_brand_description_and_generation_stages(feedback_page):
    page, control, _ = feedback_page
    assert page.locator("header .logo").inner_text() == "片语"
    description = page.locator("header .brand-description")
    assert description.is_visible() and description.inner_text().strip()
    assert description.get_attribute("aria-hidden") != "true"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot(page, "home")
    start(page)
    assert "识别" in page.locator("#stages [aria-current=step]").inner_text()
    assert page.locator("#stages .done").count() >= 2
    assert active_animations(page, "#status") > 0
    screenshot(page, "recognition")
    page.emulate_media(reduced_motion="reduce")
    assert active_animations(page, "#status") == 0
    page.emulate_media(reduced_motion="no-preference")
    control["phase"] = "ai"
    page.wait_for_selector("#ai-progress[aria-busy=true]")
    assert page.locator("#ai-stages [aria-current=step]").count() == 1
    assert active_animations(page, "#ai-progress") > 0
    transform = page.locator("#ai-status").evaluate("el => getComputedStyle(el, '::before').transform")
    page.wait_for_timeout(150)
    assert page.locator("#ai-status").evaluate("el => getComputedStyle(el, '::before').transform") != transform
    screenshot(page, "generating")
    control["ai_phase"] = "proofread"
    page.wait_for_function("document.querySelector('#ai-stages li:nth-child(2)').getAttribute('aria-current') === 'step'")
    assert "校对" in page.locator("#ai-status").inner_text()
    page.emulate_media(reduced_motion="reduce")
    assert active_animations(page, "#ai-progress") == 0
    page.emulate_media(reduced_motion="no-preference")
    control["ai_phase"] = "render"
    page.wait_for_function("document.querySelector('#ai-stages li:nth-child(3)').getAttribute('aria-current') === 'step'")
    assert "排版" in page.locator("#ai-status").inner_text()
    screenshot(page, "rendering")
    control["phase"] = "done"
    page.wait_for_selector("#ai-output:not([hidden])")
    assert not page.locator("#preview-source").is_visible()
    assert not page.locator("#ai-progress").is_visible()


def test_preview_source_fullscreen_alignment_and_escape(feedback_page, server):
    page, control, _ = feedback_page
    ai_pending(page, control)
    assert not page.locator("#source-drawer").is_visible()
    page.click("#preview-source")
    assert page.locator("#source-drawer").is_visible()
    assert "那块草不能吃" in page.locator("#view-reading").inner_text()
    page.click("#drawer-expand")
    assert page.locator("#source-drawer.fullscreen").is_visible()
    assert page.locator("#drawer-expand").get_attribute("aria-pressed") == "true"
    box = page.locator("#source-drawer").bounding_box()
    viewport = page.viewport_size
    assert box["width"] >= viewport["width"] - 4
    assert box["height"] >= viewport["height"] - 4
    page.click("#ai-manual > summary")
    style_box = page.locator("#ai-node").bounding_box()
    model_box = page.locator("#ai-service").bounding_box()
    assert abs(style_box["x"] - model_box["x"]) <= 1
    assert abs(style_box["width"] - model_box["width"]) <= 1
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot(page, "fullscreen-fields")
    page.keyboard.press("Escape")
    assert page.locator("#source-drawer").is_visible()
    assert page.locator("#source-drawer.fullscreen").count() == 0
    assert page.locator("#drawer-expand").get_attribute("aria-pressed") == "false"
    page.keyboard.press("Escape")
    assert not page.locator("#source-drawer").is_visible()
    page.click("#preview-source")
    assert page.locator("#source-drawer.fullscreen").count() == 0


def test_copy_feedback_stays_on_clicked_button(feedback_page):
    page, control, run = feedback_page
    ai_pending(page, control)
    control["phase"] = "done"
    page.wait_for_selector("#ai-copy:not([disabled])", state="visible")
    page.click("#ai-copy")
    page.wait_for_selector("#ai-copy.copied:has-text('已复制')")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert copied.startswith(run["text"])
    assert copied.count("https://github.com/lainXXX/pianyu") == 1
    assert page.locator("#ai-copy-msg, #copy-msg").count() == 0
    assert page.get_by_text("已复制", exact=True).count() == 1
    screenshot(page, "copied")
    page.wait_for_function("document.querySelector('#ai-copy').textContent === '复制文本'", timeout=6000)
    page.click("#drawer-open")
    page.click("#copy")
    page.wait_for_selector("#copy.copied:has-text('已复制')")
    assert "那块草不能吃" in page.evaluate("navigator.clipboard.readText()")
    assert page.get_by_text("已复制", exact=True).count() == 1


def test_result_title_opens_original_video(feedback_page):
    page, control, _ = feedback_page
    ai_pending(page, control)
    title = page.locator('#doc-title')
    assert title.get_attribute('href') == 'https://www.douyin.com/video/901'
    assert title.get_attribute('target') == '_blank'
    page.context.route('https://www.douyin.com/video/901', lambda route: route.fulfill(body='原视频'))
    with page.expect_popup() as popup:
        title.click()
    popup.value.wait_for_load_state()
    assert popup.value.url == 'https://www.douyin.com/video/901'
    assert page.locator('#result').is_visible()
    popup.value.close()
    screenshot(page, "title-link")


@pytest.mark.parametrize("failure", ["failed", "poll-error"])
def test_ai_failure_stops_busy_feedback_and_preserves_source(feedback_page, failure):
    page, control, _ = feedback_page
    ai_pending(page, control)
    control["phase"] = failure
    page.wait_for_function("document.getElementById('ai-progress').getAttribute('aria-busy') === 'false'")
    if failure == "failed":
        page.wait_for_selector("#ai-failed:not([hidden])")
        assert page.locator("#ai-error").inner_text().strip()
    else:
        assert "失败" in page.locator("#ai-status").inner_text()
    assert not page.locator("#ai-stages").is_visible()
    assert page.locator("#ai-progress").evaluate("el => el.getAnimations({subtree: true}).length") == 0
    screenshot(page, failure)
    page.click("#drawer-open")
    assert "那块草不能吃" in page.locator("#view-reading").inner_text()


def test_clipboard_failure_uses_button_and_recovers(feedback_page):
    page, control, _ = feedback_page
    ai_pending(page, control)
    control["phase"] = "done"
    page.wait_for_selector("#ai-copy:not([disabled])", state="visible")
    page.evaluate("() => { navigator.clipboard.writeText = () => Promise.reject(new Error('模拟剪贴板拒绝')); }")
    page.click("#ai-copy")
    page.wait_for_selector("#ai-copy:has-text('复制失败')")
    assert page.locator("#ai-copy.copied").count() == 0
    assert page.locator("#ai-copy-msg, #copy-msg").count() == 0
    screenshot(page, "copy-failed")
    page.wait_for_function("document.querySelector('#ai-copy').textContent === '复制文本'", timeout=6000)
    page.click("#drawer-open")
    page.click("#copy")
    page.wait_for_selector("#copy:has-text('复制失败')")
    page.wait_for_function("document.querySelector('#copy').textContent === '复制'", timeout=6000)


@pytest.mark.parametrize("kind,output", [("structure", "structure"), ("source", "refine")])
def test_original_styles_show_only_their_actual_stages(feedback_page, kind, output):
    page, control, run = feedback_page
    run.update(kind=kind, output=output)
    ai_pending(page, control)
    assert page.locator("#ai-stages li").all_text_contents() == ["整理内容", "生成图片"]
    control["ai_phase"] = "render"
    page.wait_for_function("document.querySelector('#ai-stages li:last-child').getAttribute('aria-current') === 'step'")
    assert page.locator("#ai-stages .done").count() == 1
