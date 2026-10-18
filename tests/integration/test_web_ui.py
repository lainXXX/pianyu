"""T14 acceptance 1-5: the full browser flow with Playwright driving the installed Edge.

The pipeline is stubbed (no network, no billing); it writes real output files via
vtx.output.write_outputs so the page renders the same data shapes as production.
"""
import json
import socket
import threading
import time
from zipfile import ZipFile
from pathlib import Path

import pytest
import uvicorn

from vtx.asr import Segment
from vtx.fetch import FetchError
from vtx.output import write_outputs
from vtx.pipeline import RunResult
from vtx.qc import Issue
from vtx.asr_cloud import CloudASRError
from vtx.engines import Registry
from vtx.llm import LLMRegistry
from vtx.server import SettingsStore, create_app
from vtx.target import UnsupportedURL

pytestmark = pytest.mark.integration
playwright = pytest.importorskip("playwright.sync_api")

SEGS = [Segment(0, 0.0, 3.0, "呃，大家好啊。"), Segment(1, 3.5, 7.0, "今天讲一个问题，那那块草不能吃。"),
        Segment(2, 7.5, 12.0, "每次服用1.5毫升，不要超过三次。")]
META = {"platform": "bilibili", "video_id": "BV1wFZ8YBEt4", "part": 1, "title": "为什么人类需要吃盐？",
        "url": "https://www.bilibili.com/video/BV1wFZ8YBEt4", "duration": 12.0, "author": "示例UP",
        "engine": "dashscope/paraformer-v2", "billed_seconds": 11}
ISSUES = [Issue("number_mismatch", 7.5, 12.0, "数字不一致：字幕 15 / 识别 1.5")]
FAILS = {
    "https://www.bilibili.com/video/BV1zW411t7YE": UnsupportedURL("B站视频不可用: BV1zW411t7YE（啥都木有）"),
    "https://www.douyin.com/video/1": FetchError("login_required", "需要登录"),
    "https://www.douyin.com/video/2": FetchError("api_failed", "所有播放地址都失败"),
}
SILENT = "https://www.douyin.com/video/3"


USED_ENGINES = []


def fake_run(url, out_dir, progress, engine=None):
    USED_ENGINES.append(engine)
    progress("resolve")
    time.sleep(0.3)
    progress("fetch")
    if url in FAILS:
        raise FAILS[url]
    progress("asr", {k: META[k] for k in ("title", "platform", "video_id", "part", "duration")})
    time.sleep(0.3)
    progress("qc")
    progress("output")
    segs, issues = ([], []) if url == SILENT else (SEGS, ISSUES)
    files = write_outputs(out_dir, segs, issues, META)
    edits = len(json.loads(files["edits"].read_text(encoding="utf-8")))
    return RunResult(files, dict(META, url=url), len(issues), edits, cached_asr=False)


def _no_host(host):
    raise OSError(host)


class _StubEngine:
    def __init__(self, ok):
        self.ok = ok

    def transcribe(self, wav):
        if not self.ok:
            raise CloudASRError("ASR_ACCESS_DENIED", "HTTP 401 invalid key")
        return [Segment(0, 9.9, 13.8, "每次服用1.5毫升，不要超过三次。")]


LLM_PROMPTS = []


STRUCTURED = {
    "title": "为什么人类需要吃盐", "conclusion": "所有生物都离不开电解质。",
    "points": [
        {"heading": "开场", "body": "从一个问题讲起。", "key": False, "quotes": [
            {"quote": "人一天必须吃十克盐。", "source": "人一天必须吃十克盐。"}]},          # not in transcript -> dropped
        {"heading": "盐在身体里做什么", "body": "钠离子很重要。", "key": True, "detail": ["每次服用要适量"],
         "quotes": [{"quote": "每次服用1.5毫升，不要超过三次。", "source": "每次服用1.5毫升，不要超过三次。"}]},
    ],
}
USAGE = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
REFINE_REWRITE = {"on": False}
FAIL_NEXT = {"on": False}       # the next points call fails once with "余额不足"


class FakeLLM:
    context = 32000

    def chat(self, messages):
        if any("SOURCE_BACKED_PROOFREAD_V1" in message["content"] for message in messages):
            return '{"patches": []}', {}
        prompt = messages[-1]["content"]
        LLM_PROMPTS.append(prompt)
        time.sleep(0.3)
        if "# 精校原文" in prompt:                                        # refine
            paras = json.loads(prompt.rsplit("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0])
            out = []
            for p in paras:
                t = p["text"].replace("呃，", "").replace("那那块", "那块")
                if REFINE_REWRITE["on"]:
                    t = "建议大家适量服用即可。"                          # over-rewritten -> reverted
                out.append({"n": p["n"], "text": t})
            return json.dumps(out, ensure_ascii=False), USAGE
        if "按句编号" in prompt:                                          # 结构化原文: one section, drop the greeting
            ids = [x["i"] for x in json.loads(prompt.rsplit("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0])]
            return json.dumps({"sections": [{"heading": "盐的知识", "blocks": [{"type": "p", "ids": ids[1:]}]}],
                               "removed": ids[:1]}, ensure_ascii=False), USAGE
        if "JSON" in prompt and FAIL_NEXT["on"]:
            FAIL_NEXT["on"] = False
            from vtx.llm import LLMError

            raise LLMError("LLM_BILLING_UNAVAILABLE", "余额不足")
        if "JSON" in prompt:                                            # structured
            return json.dumps(STRUCTURED, ensure_ascii=False), USAGE
        return "## 补盐要点\n\n**要点一**：人需要补盐\n\n- 要点二：动物也需要\n\n| 项目 | 说明 |\n| --- | --- |\n| 盐 | 电解质 |", USAGE


def fake_build(sid):
    return _StubEngine(ok=not sid.startswith("tencent"))


def fake_parts(bvid):
    return [{"page": 1, "title": "规则说明", "duration": 1947}, {"page": 3, "title": "基本概念", "duration": 3000}]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("web")
    store = SettingsStore(tmp / ".env", tmp / "cookies", tmp / "settings.json")
    registry = Registry(tmp / "engines.json", tmp / "engine_keys.json", env_file=tmp / ".env",
                        resolve=lambda host: {"api.example.com": ["93.184.216.34"], "ai.internal.example.com": ["10.0.0.77"]}.get(host) or _no_host(host))
    registry.update("dashscope", {}, key="fake-ui-only")
    registry.build = fake_build      # "test connection" never reaches a real provider
    llm = LLMRegistry(tmp / "llm.json", tmp / "llm_keys.json",
                      resolve=lambda host: {"api.example.com": ["93.184.216.34"], "ai.internal.example.com": ["10.0.0.77"]}.get(host) or _no_host(host))
    llm.client = lambda sid: FakeLLM()
    preview_client = llm.preview_client

    def fake_preview(fields, key=None, sid=None):
        client = preview_client(fields, key=key, sid=sid)
        client.chat = FakeLLM().chat
        return client

    llm.preview_client = fake_preview
    app = create_app(work_dir=tmp, run=fake_run, store=store, parts=fake_parts, registry=registry,
                     llm_registry=llm, reveal_keys=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", tmp
    srv.should_exit = True


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        yield b
        b.close()


@pytest.fixture
def page(browser, server):
    ctx = browser.new_context(accept_downloads=True, permissions=["clipboard-read", "clipboard-write"])
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(server[0] + "/")
    assert not errors, errors
    pg.wait_for_selector("body[data-ready='1']")
    pg.wait_for_selector("#style option[value=structure]", state="attached")
    pg.wait_for_function("document.getElementById('engine').value !== ''")
    pg.select_option("#style", "raw")
    yield pg
    ctx.close()
    assert not errors, errors


def submit(page, url):
    if not page.is_visible("#url"):
        page.click("#home-back")
    page.fill("#url", url)
    page.click("#go")


def test_full_flow(page):
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    # title / platform / duration shown while processing, progress stages visible
    page.wait_for_selector("#v-title:has-text('为什么人类需要吃盐？')", state="attached")
    assert "B站" in page.inner_text("#v-meta") and "00:00:12" in page.inner_text("#v-meta")
    assert page.locator("#stages li").count() == 5
    page.wait_for_selector("#result:not([hidden])")
    assert page.is_hidden("#source-drawer")
    page.click("#drawer-open")
    page.click(".recognition-info > summary")
    assert "计费 11 秒" in page.inner_text("#summary")

    # reading view is cleaned
    reading = page.inner_text("#view-reading")
    assert "呃" not in reading and "那块草不能吃" in reading and "那那块" not in reading

    # original view shows deletions; restoring one puts it back into the reading text
    page.click("#tab-original")
    assert page.is_visible("#view-original") and not page.is_visible("#view-reading")
    assert page.locator("#view-original del").count() == 2
    page.locator("#view-original button.restore").first.click()
    assert page.locator("#view-original del.restored").count() == 1
    page.click("#tab-reading")
    assert page.inner_text("#view-reading").startswith("呃，大家好啊。")

    # issue links jump to the original video time in a new tab
    page.click("#drawer-issues-tab")
    link = page.locator("#issues a").first
    assert link.get_attribute("href").endswith("t=7") and link.get_attribute("target") == "_blank"

    page.click("#tab-reading")

    # copy
    page.click("#copy")
    page.wait_for_selector("#copy:has-text('已复制')")
    assert page.evaluate("navigator.clipboard.readText()").startswith("呃，大家好啊。")

    # export TXT / Markdown / SRT
    for button, suffix, needle in [("#export-txt", ".txt", "呃，大家好啊。"), ("#export-md", ".md", "# 为什么人类需要吃盐？"),
                                   ("#export-srt", ".srt", "00:00:03,500 --> 00:00:07,000")]:
        with page.expect_download() as d:
            page.click(button)
        dl = d.value
        assert dl.suggested_filename == f"BV1wFZ8YBEt4-p1{suffix}"
        body = Path(dl.path()).read_text(encoding="utf-8")
        assert needle in body
        if suffix == ".txt":
            assert body.startswith("标题：为什么人类需要吃盐？\n作者：示例UP\n链接：https://www.bilibili.com/video/BV1wFZ8YBEt4\n")


def test_multi_part_picker(page):
    page.fill("#url", "https://www.bilibili.com/video/BV1Wv411h7kN")
    page.click("#go")
    page.wait_for_selector("#part-dialog[open]")
    assert page.locator("#part-select option").count() == 2
    page.select_option("#part-select", "3")
    page.click("#part-ok")
    page.wait_for_selector("#result:not([hidden])")
    assert "p=3" in page.input_value("#url")


@pytest.mark.parametrize("url,category,text", [
    ("https://www.bilibili.com/video/BV1zW411t7YE", "dead", "链接失效"),
    ("https://www.douyin.com/video/1", "login_required", "需要登录"),
])
def test_failure_messages(page, url, category, text):
    submit(page, url)
    page.wait_for_selector("#error:not([hidden])")
    assert page.get_attribute("#error", "data-category") == category
    assert text in page.inner_text("#err-title")


def test_no_speech_message(page):
    submit(page, SILENT)
    page.wait_for_selector("#result:not([hidden])")
    assert page.inner_text("#view-reading").strip() == ""
    assert page.get_attribute("#error", "data-category") == "no_speech"
    assert "未识别到人声" in page.inner_text("#err-title")


def test_new_job_clears_previous_notice(page):
    submit(page, SILENT)
    page.wait_for_selector("#error[data-category=no_speech]")
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    page.wait_for_selector("#result:not([hidden])")
    assert page.is_hidden("#error")


# ---------------------------------------------------------------- settings page (T34 / T39)

def settings(page, server, tab=None):
    page.goto(server[0] + "/settings" + (f"#{tab}" if tab else ""))
    page.wait_for_selector("body[data-ready='1']")
    for summary in page.locator(".setting-editor:not([open]) > summary").all():
        if summary.is_visible():
            summary.click()


def test_main_page_keyboard_tabs(page):
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    page.wait_for_selector("#result:not([hidden])")
    page.click("#drawer-open")
    page.focus("#tab-reading")
    page.keyboard.press("ArrowRight")
    assert page.get_attribute("#tab-original", "aria-selected") == "true"
    for el_id in ["url", "style", "result-style", "part-select", "ai-node", "ai-service"]:
        assert page.locator(f"label[for={el_id}]").count() == 1, el_id


def add_llm(page, server):
    """Seed a model for reader tests; settings interactions have their own suite."""
    services = page.request.get(server[0] + "/api/llm/services").json()["services"]
    if any(s["label"] == "测试模型" for s in services):
        return
    response = page.request.post(server[0] + "/api/llm/services", data={
        "label": "测试模型", "base_url": "https://api.example.com/v1", "model": "chat-1",
        "key": "sk-llm-ui-secret-4455",
    })
    assert response.ok, response.text()


# ---------------------------------------------------------------- workflow / AI tab / history (T38, T46, T47)

def wf_set(page, *ids):
    """Choose the output through the same visible selector as the user."""
    assert len(ids) <= 1
    if not ids and not page.is_visible("#style"):
        page.click("#home-back")
    selector = "#style" if page.is_visible("#style") else "#result-style"
    page.wait_for_selector(selector + " option[value=structure]", state="attached")
    page.select_option(selector, ids[0] if ids else "raw")


def wait_ai_done(page, n):
    page.wait_for_function(f"document.querySelectorAll('#ai-nodes button:not([disabled])').length === {n}", timeout=30000)


def test_workflow_text_and_image_and_switch_layout(page, server):
    add_llm(page, server)
    page.goto(server[0] + "/")
    wf_set(page, "structure")


    before = len(LLM_PROMPTS)
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")      # no confirm dialog: the page has no handler
    page.wait_for_selector("#result:not([hidden])")
    wait_ai_done(page, 1)
    assert len(LLM_PROMPTS) == before + 1
    assert page.locator("#ai-nodes button").all_inner_texts() == ["结构化原文"]

    # Images are immediately available; text is readable HTML, with source details tucked away.
    page.select_option("#result-style", "structure")
    page.wait_for_selector("#ai-title:has-text('结构化原文')", state="attached")
    page.wait_for_function("document.querySelector('#ai-pages img') && document.querySelector('#ai-pages img').naturalWidth === 1080")
    assert page.input_value("#ai-layout") == "book"
    assert page.is_hidden("#source-drawer") and page.locator("#tab-ai").count() == 0
    assert page.evaluate("document.querySelector('#page-count').parentElement === document.querySelector('#doc-meta').parentElement")
    assert page.inner_text("#page-count").strip() == f"· {page.locator('#ai-pages img').count()} 页"
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("#ai-pages img").first.click()
    page.wait_for_selector("#image-viewer[open]")
    page.click("#image-zoom")
    assert page.locator("#image-large").evaluate("img => img.clientWidth === img.naturalWidth")
    assert page.locator("#image-viewer").evaluate("d => d.scrollWidth > d.clientWidth")
    page.keyboard.press("Escape")
    assert not page.locator("#image-viewer").is_visible()
    assert page.locator("#ai-pages img").first.evaluate("img => img === document.activeElement")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.click("#ai-tab-text")
    text = page.inner_text("#ai-text")
    assert "##" not in text and "盐的知识" in text and "今天讲一个问题，那块草不能吃。" in text
    assert page.locator("#ai-text h2", has_text="盐的知识").count() == 1
    page.click("#ai-copy")
    page.wait_for_selector("#ai-copy:has-text('已复制')")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert "盐的知识" in copied and "今天讲一个问题，那块草不能吃。" in copied and "##" not in copied
    assert page.is_hidden("#ai-refine-stats")
    page.click("#drawer-open")
    page.click(".recognition-info > summary")
    page.click("#ai-change-details summary")
    assert "删掉的客套话" in page.inner_text("#ai-refine-stats")
    page.click("#drawer-close")
    page.click(".export-menu > summary")
    with page.expect_download() as d:
        page.click("#ai-export")
    assert d.value.suggested_filename == "BV1wFZ8YBEt4-p1-结构化原文.md"

    # A second output is available from the result selector without retranscribing.
    page.select_option("#result-style", "structured")
    page.click("#ai-generate")
    wait_ai_done(page, 2)
    page.select_option("#result-style", "structured")
    page.wait_for_function("document.querySelector('#ai-pages img') && document.querySelector('#ai-pages img').naturalWidth === 1080")
    calls = len(LLM_PROMPTS)
    page.select_option("#ai-layout", "cover-qa")
    page.wait_for_function("document.querySelector('#ai-pages img') && document.querySelector('#ai-pages img').naturalHeight === 1440")
    page.select_option("#ai-layout", "B")
    page.wait_for_function("document.querySelector('#ai-pages img') && document.querySelector('#ai-pages img').naturalWidth === 1600")
    assert len(LLM_PROMPTS) == calls
    page.click("#ai-tab-text")
    page.click("#drawer-open")
    page.click("#ai-change-details summary")
    assert "原话校验：删去 1 句" in page.inner_text("#ai-refine-stats")
    page.click("#drawer-close")
    page.click("#ai-tab-img")
    with page.expect_download() as d:
        page.click("#ai-png")
    assert d.value.suggested_filename == "BV1wFZ8YBEt4-p1-结构化要点-横版思维导图.png"
    page.click(".export-menu > summary")
    with page.expect_download() as d:
        page.click("#ai-html")
    html = Path(d.value.path()).read_text(encoding="utf-8")
    assert "<script" not in html and "版权" in html and "@示例UP" in html
    wf_set(page)        # leave the workflow empty for other tests



def test_multipage_image_download_is_complete_archive(page, server, monkeypatch):
    import sys

    long_source = [Segment(i, i * 3., (i + 1) * 3.,
                          f"第{i + 1}段说明盐在身体中的作用。" + "钠离子维持电解质平衡，摄入需要适量。" * 5)
                   for i in range(60)]
    monkeypatch.setattr(sys.modules[__name__], "SEGS", long_source)
    add_llm(page, server)
    page.goto(server[0] + "/")
    wf_set(page, "structure")
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    wait_ai_done(page, 1)
    page.wait_for_function("document.querySelectorAll('#ai-pages img').length > 1 && "
                           "[...document.querySelectorAll('#ai-pages img')].every(i => i.complete && i.naturalWidth)")
    rid, layout, count = page.evaluate("[shownRun.id, document.getElementById('ai-layout').value, document.querySelectorAll('#ai-pages img').length]")
    with page.expect_download() as download:
        page.click("#ai-png")
    assert download.value.suggested_filename.endswith(".zip")
    with ZipFile(download.value.path()) as archive:
        assert archive.namelist() == [f"{i:02d}.png" for i in range(1, count + 1)]
        for index in range(1, count + 1):
            expected = page.request.get(f"{server[0]}/api/llm/runs/{rid}/files/{layout}/{index}").body()
            assert archive.read(f"{index:02d}.png") == expected
    wf_set(page)

def test_failed_node_shows_reason_and_retry(page, server):
    add_llm(page, server)
    page.goto(server[0] + "/")
    wf_set(page, "cards")
    FAIL_NEXT["on"] = True
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    page.wait_for_selector("#result:not([hidden])")
    page.wait_for_selector("#ai-nodes button.failed", state="attached", timeout=30000)
    page.wait_for_selector("#ai-failed:not([hidden])")
    assert "余额不足" in page.inner_text("#ai-error")
    page.click("#ai-retry")
    page.wait_for_function("document.querySelector('#ai-pages img') && document.querySelector('#ai-pages img').naturalWidth === 1080",
                           timeout=30000)
    assert page.locator("#ai-nodes button.failed").count() == 0 and page.input_value("#ai-layout") == "cards"
    wf_set(page)


def test_manual_run_free_template_one_off_prompt(page, server):
    add_llm(page, server)
    page.goto(server[0] + "/")
    wf_set(page)
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    page.wait_for_selector("#result:not([hidden])")
    assert page.is_hidden("#ai-results-box")
    page.click("#drawer-open")
    page.click("#ai-manual summary")
    page.select_option("#ai-node", "notes")
    assert "学习笔记" in page.input_value("#ai-template") and page.is_visible("#ai-custom")
    page.fill("#ai-template", "只要两条要点：{text}")
    page.select_option("#ai-source", "original")
    page.click("#ai-run")
    wait_ai_done(page, 1)
    page.wait_for_selector("#ai-output:not([hidden])")
    if page.is_visible("#source-drawer"):
        page.click("#drawer-close")
    assert LLM_PROMPTS[-1].startswith("只要两条要点：") and "呃，大家好啊" in LLM_PROMPTS[-1]
    page.click("#drawer-open")
    page.click(".recognition-info > summary")
    assert "150 tokens" in page.inner_text("#ai-meta")
    page.click("#drawer-close")
    page.wait_for_function("document.querySelector('#ai-pages img') && document.querySelector('#ai-pages img').naturalWidth === 1080")
    page.click("#ai-tab-text")
    assert "要点一" in page.inner_text("#ai-text")
    assert page.locator("#ai-text strong", has_text="要点一").count() == 1
    assert page.locator("#ai-text li", has_text="要点二").count() == 1
    assert page.locator("#ai-text td", has_text="电解质").count() == 1
    assert "**" not in page.inner_text("#ai-text") and "| --- |" not in page.inner_text("#ai-text")
    page.click("#ai-copy")
    page.wait_for_selector("#ai-copy:has-text('已复制')")
    copied = page.evaluate("navigator.clipboard.readText()")
    assert "要点一" in copied and "动物也需要" in copied and "电解质" in copied
    assert "**" not in copied and "| --- |" not in copied and "##" not in copied
    page.click(".export-menu > summary")
    with page.expect_download() as d:
        page.click("#ai-export")
    body = Path(d.value.path()).read_text(encoding="utf-8")
    assert d.value.suggested_filename == "BV1wFZ8YBEt4-p1-学习笔记.md" and "要点一" in body
    assert "来源：B站 @示例UP 《为什么人类需要吃盐？》 https://www.bilibili.com/video/BV1wFZ8YBEt4" in body


def test_refine_reverts_overrewrite(page, server):
    add_llm(page, server)
    page.goto(server[0] + "/")
    wf_set(page)
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    page.wait_for_selector("#result:not([hidden])")
    page.click("#drawer-open")
    page.click("#ai-manual summary")
    page.select_option("#ai-node", "refine")
    REFINE_REWRITE["on"] = True
    try:
        page.click("#ai-run")
        wait_ai_done(page, 1)
        page.wait_for_selector("#ai-output:not([hidden])")
    finally:
        REFINE_REWRITE["on"] = False
    if page.is_visible("#source-drawer"):
        page.click("#drawer-close")
    page.click("#ai-tab-text")
    assert "建议大家适量服用" not in page.inner_text("#ai-text")
    page.click("#drawer-open")
    page.click(".recognition-info > summary")
    page.click("#ai-change-details summary")
    assert "建议大家适量服用" not in page.inner_text("#ai-text") and "改动过大" in page.inner_text("#ai-refine-stats")
    page.uncheck("#ai-show-diff")


def test_history_reopen_and_delete(page, server):
    add_llm(page, server)
    page.goto(server[0] + "/")
    wf_set(page, "refine")
    submit(page, "https://www.douyin.com/video/77")
    page.wait_for_selector("#result:not([hidden])")
    wait_ai_done(page, 1)
    wf_set(page)
    before = len(LLM_PROMPTS)

    page.goto(server[0] + "/")
    page.click("#history-btn")
    first = page.locator("#history-list > li").first
    assert "为什么人类需要吃盐？" in first.inner_text()
    first.locator(".ai-runs button", has_text="精校原文").click()
    page.wait_for_selector("#ai-meta:has-text('历史结果')", state="attached")
    page.wait_for_function("document.querySelector('#ai-pages img') && document.querySelector('#ai-pages img').naturalWidth === 1080")
    page.click("#ai-tab-text")
    assert "那块草不能吃" in page.inner_text("#ai-text")
    assert len(LLM_PROMPTS) == before                           # reopening never calls the model
    assert page.input_value("#url") == "https://www.douyin.com/video/77"

    page.click("#history-btn")
    count = page.locator("#history-list > li").count()
    page.once("dialog", lambda d: d.dismiss())
    first.locator("button", has_text="删除").click()
    assert page.locator("#history-list > li").count() == count  # cancelled
    page.once("dialog", lambda d: (("不能恢复" in d.message) or pytest.fail(d.message), d.accept()))
    first.locator("button", has_text="删除").click()
    page.wait_for_function("fetch('/api/history').then(r => r.text()).then(t => !t.includes('douyin.com/video/77'))")
    left = len(page.evaluate("fetch('/api/history').then(r => r.json())")["items"])
    page.wait_for_function(f"document.querySelectorAll('#history-list > li').length === {max(left, 1)}")
    assert page.is_hidden("#result")


@pytest.mark.integration
def test_set_default_api(server):
    import httpx

    # add two engines via API
    with httpx.Client(base_url=server[0]) as cli:
        payload = {"kind": "openai", "model": "whisper-1", "base_url": "http://127.0.0.1:9999/v1", "allow_local": True}
        r1 = cli.post("/api/engines", json={**payload, "label": "服务A", "key": "sk-a"})
        assert r1.status_code == 200, f"add engine A failed: {r1.status_code} {r1.text}"
        r2 = cli.post("/api/engines", json={**payload, "label": "服务B", "key": "sk-b"})
        assert r2.status_code == 200, f"add engine B failed: {r2.status_code} {r2.text}"

        r = cli.get("/api/engines")
        svcs = {s["label"]: s["id"] for s in r.json()["services"]}
        a, b = svcs["服务A"], svcs["服务B"]

        # set b as default
        r = cli.post(f"/api/engines/{b}/set-default")
        assert r.status_code == 200 and r.json()["default"] == b
        r = cli.get("/api/engines")
        assert r.json()["default"] == b

        # set a as default
        r = cli.post(f"/api/engines/{a}/set-default")
        assert r.status_code == 200 and r.json()["default"] == a

        # nonexistent service
        r = cli.post("/api/engines/nonexistent/set-default")
        assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"

        # same for LLM
        llm_payload = {"model": "gpt-4", "base_url": "http://127.0.0.1:9999/v1", "allow_local": True}
        r3 = cli.post("/api/llm/services", json={**llm_payload, "label": "模型C", "key": "sk-c"})
        assert r3.status_code == 200, f"add LLM C failed: {r3.status_code} {r3.text}"
        r4 = cli.post("/api/llm/services", json={**llm_payload, "label": "模型D", "key": "sk-d"})
        assert r4.status_code == 200, f"add LLM D failed: {r4.status_code} {r4.text}"

        r = cli.get("/api/llm/services")
        llm_svcs = {s["label"]: s["id"] for s in r.json()["services"]}
        c, d = llm_svcs["模型C"], llm_svcs["模型D"]

        r = cli.post(f"/api/llm/services/{d}/set-default")
        assert r.status_code == 200 and r.json()["default"] == d
        r = cli.get("/api/llm/services")
        assert r.json()["default"] == d

        r = cli.post(f"/api/llm/services/{c}/set-default")
        assert r.status_code == 200 and r.json()["default"] == c

        r = cli.post("/api/llm/services/nonexistent/set-default")
        assert r.status_code == 404


def test_reader_navigation_drawer_and_mobile_layout(page, server):
    assert page.get_attribute("body", "data-state") == "home"
    submit(page, "https://www.bilibili.com/video/BV1wFZ8YBEt4")
    page.wait_for_selector("body[data-state=done]")
    page.click("#drawer-open")
    assert page.locator("#source-drawer").is_visible()
    page.click("#drawer-issues-tab")
    assert "数字不一致" in page.inner_text("#issues")
    page.keyboard.press("Escape")
    assert page.locator("#source-drawer").is_hidden()
    assert page.evaluate("document.activeElement.id") == "drawer-open"

    page.set_viewport_size({"width": 375, "height": 812})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.click("#drawer-open")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.keyboard.press("Escape")
    page.click("#history-btn")
    assert page.get_attribute("body", "data-state") == "hist"
    assert page.is_hidden("#result") and page.is_visible("#history")
    page.fill("#history-search", "不存在的历史记录")
    assert page.locator("#history-list button").count() == 0
    page.fill("#history-search", "吃盐")
    assert page.locator("#history-list .history-title").count() > 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.click("#home-back")
    assert page.get_attribute("body", "data-state") == "home" and page.is_visible("#url")
    assert page.is_hidden("#home-back")
    page.goto(server[0] + "/")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    settings(page, server)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


@pytest.mark.parametrize("style", ["raw", "notes", "review", "custom-reader"])
def test_style_submits_actual_output_id_and_configured_engine(page, server, style):
    outputs = page.request.get(server[0] + "/api/outputs").json()
    custom = dict(next(o for o in outputs["outputs"] if o["id"] == "notes"),
                  id="custom-reader", name="自定义阅读风格")
    outputs["outputs"].append(custom)
    page.route("**/api/outputs", lambda route: route.fulfill(json=outputs))
    page.route("**/api/llm/services", lambda route: route.fulfill(json={
        "default": "fake-model", "services": [{"id": "fake-model", "configured": True, "label": "测试模型", "model": "test"}],
    }))
    # An old default may be stale; only a configured service can receive a new job.
    page.route("**/api/engines", lambda route: route.fulfill(json={
        "default": "stale", "services": [
            {"id": "stale", "configured": False},
            {"id": "ready", "configured": True},
        ], "presets": [],
    }))
    page.route("**/api/jobs", lambda route: route.fulfill(
        status=400, json={"detail": "测试已拦截提交"}))
    page.reload()
    page.wait_for_selector("body[data-ready='1']")
    page.wait_for_selector("#style option[value=custom-reader]", state="attached")
    page.wait_for_function("document.getElementById('engine').value === 'ready'")
    page.select_option("#style", style)
    with page.expect_request(lambda r: r.url.endswith("/api/jobs") and r.method == "POST") as request:
        submit(page, "https://www.douyin.com/video/55")
    payload = request.value.post_data_json
    assert payload["engine"] == "ready"
    assert payload["workflow"] == ([] if style == "raw" else [style])
    page.wait_for_selector("#error:not([hidden])")


def test_no_configured_engine_shows_actionable_error(page):
    page.route("**/api/engines", lambda route: route.fulfill(json={
        "default": "stale", "services": [{"id": "stale", "configured": False}], "presets": [],
    }))
    page.reload()
    page.wait_for_selector("body[data-ready='1']")
    sent = []
    page.on("request", lambda r: sent.append(r.url) if r.method == "POST" and r.url.endswith("/api/jobs") else None)
    submit(page, "https://www.douyin.com/video/55")
    page.wait_for_selector("#error:not([hidden])")
    assert "设置" in page.inner_text("#error") and "识别服务" in page.inner_text("#error")
    assert not sent


def test_finished_job_does_not_replace_home_after_navigation(page, server):
    with page.expect_response(lambda r: r.url.endswith("/api/jobs") and r.request.method == "POST") as response:
        submit(page, "https://www.douyin.com/video/901")
    job_id = response.value.json()["id"]
    page.wait_for_selector("body[data-state=proc]")
    page.click("#home-back")
    page.wait_for_function(
        "id => fetch('/api/jobs/' + id).then(r => r.json()).then(j => j.status === 'done')",
        arg=job_id,
    )
    # Allow the pending poll to return after the user has left the processing screen.
    page.wait_for_timeout(1200)
    assert page.get_attribute("body", "data-state") == "home"
    assert page.is_visible("#url") and page.is_hidden("#result")


def test_delayed_history_files_do_not_replace_home_after_navigation(page):
    submit(page, "https://www.douyin.com/video/902")
    page.wait_for_selector("body[data-state=done]")
    page.click("#history-btn")
    page.wait_for_selector("#history-list .history-title")
    pending = []
    page.route("**/files/reading", lambda route: pending.append(route))
    with page.expect_request(lambda r: r.url.endswith("/files/reading")):
        page.locator("#history-list .history-title").first.click()
    page.click("#home-back")
    assert pending, "The history reading request should remain paused until navigation completes"
    for route in pending:
        route.fulfill(response=route.fetch())
    page.wait_for_timeout(250)
    assert page.get_attribute("body", "data-state") == "home"
    assert page.is_visible("#url") and page.is_hidden("#result")


def test_history_reopens_latest_generation_of_same_style(page, server, monkeypatch):
    add_llm(page, server)
    answer = {"text": "首次生成的笔记"}

    def chat(self, messages):
        if any("SOURCE_BACKED_PROOFREAD_V1" in message["content"] for message in messages):
            return '{"patches": []}', {}
        return answer["text"], USAGE

    monkeypatch.setattr(FakeLLM, "chat", chat)
    page.goto(server[0] + "/")
    wf_set(page, "notes")
    page.select_option("#style", "notes")
    submit(page, "https://www.douyin.com/video/903")
    page.wait_for_selector("#ai-output:not([hidden])")
    page.click("#ai-tab-text")
    page.wait_for_selector("#ai-text:has-text('首次生成的笔记')")
    answer["text"] = "重新生成的最新笔记"
    page.click("#drawer-open")
    page.click("#ai-manual summary")
    page.select_option("#ai-node", "notes")
    page.click("#ai-run")
    page.wait_for_selector("#ai-text:has-text('重新生成的最新笔记')", state="attached")
    if page.is_visible("#drawer-close"):
        page.click("#drawer-close")
    page.click("#history-btn")
    page.locator("#history-list .history-title").first.click()
    page.wait_for_selector("#ai-output:not([hidden])")
    page.click("#ai-tab-text")
    page.wait_for_selector("#ai-text:has-text('重新生成的最新笔记')")
    assert "首次生成的笔记" not in page.inner_text("#ai-text")
    wf_set(page)
