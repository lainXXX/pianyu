"""Navigation retains real in-flight jobs; all providers use isolated stubs."""
import threading
from pathlib import Path

import pytest
from playwright.sync_api import expect

import test_web_ui as ui
from test_web_ui import browser, server, page

pytestmark = pytest.mark.integration


def capture(page, name):
    out = Path('acceptance/pending-history'); out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / (name + '.png')), full_page=True)


@pytest.mark.parametrize('width', [1440, 390])
def test_back_during_recognition_history_refresh_and_reopen(page, server, monkeypatch, width):
    page.set_viewport_size({'width': width, 'height': 900})
    entered, release = threading.Event(), threading.Event()

    class BlockedRun(list):
        def append(self, engine):
            entered.set()
            assert release.wait(30), 'test did not release recognition'

    monkeypatch.setattr(ui, 'USED_ENGINES', BlockedRun())
    posts = []
    page.on('request', lambda r: posts.append(r.url) if r.method == 'POST' and r.url.endswith('/api/jobs') else None)
    try:
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/jobs')) as response:
            ui.submit(page, 'https://www.douyin.com/video/9801')
        job_id = response.value.json()['id']
        assert entered.wait(3)
        page.click('#home-back')
        expect(page.locator('body')).to_have_attribute('data-state', 'home')
        active = page.locator(f'#active-jobs button[data-job-id="{job_id}"]')
        expect(active).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        capture(page, f'recognition-home-{width}')
        active.click()
        expect(page.locator('body')).to_have_attribute('data-state', 'proc')
        page.click('#home-back')
        page.click('#history-btn')
        record = page.locator(f'#history-list li[data-job-id="{job_id}"]')
        expect(record).to_be_visible(timeout=2500)
        expect(record.locator('.history-state')).to_contain_text('中')
        expect(record.locator('.history-delete')).to_be_disabled()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        capture(page, f'recognition-history-{width}')
        page.reload()
        expect(record).to_be_visible()
        record.locator('.history-title').click()
        expect(page.locator('body')).to_have_attribute('data-state', 'proc')
        assert len(posts) == 1
        release.set()
        expect(page.locator('#result')).to_be_visible(timeout=15000)
        page.click('#history-btn')
        expect(record.locator('.history-state')).to_have_count(0)
        assert len(posts) == 1
    finally:
        release.set()


def test_failed_recognition_can_reopen_original_error(page, server):
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/jobs')) as response:
        ui.submit(page, 'https://www.douyin.com/video/1')
    job_id = response.value.json()['id']
    expect(page.locator('#error')).to_be_visible()
    page.click('#home-back'); page.click('#history-btn')
    record = page.locator(f'#history-list li[data-job-id="{job_id}"]')
    expect(record.locator('.history-state')).to_have_text('识别失败')
    record.locator('.history-title').click()
    expect(page.locator('#error')).to_have_attribute('data-category', 'login_required')
    expect(page.locator('#err-title')).to_contain_text('需要登录')


def test_manual_ai_return_refresh_and_resume_from_home(page, server, monkeypatch):
    ui.add_llm(page, server)
    page.reload()
    page.wait_for_selector('body[data-ready="1"]')
    page.select_option('#style', 'raw')
    with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/jobs')) as response:
        ui.submit(page, 'https://www.douyin.com/video/9803')
    job_id = response.value.json()['id']
    expect(page.locator('#result')).to_be_visible()
    entered, release = threading.Event(), threading.Event()
    original = ui.FakeLLM.chat
    calls = []

    def chat(self, messages):
        if any('SOURCE_BACKED_PROOFREAD_V1' in message['content'] for message in messages):
            return original(self, messages)
        calls.append(messages)
        entered.set()
        assert release.wait(30), 'test did not release manual model'
        return original(self, messages)

    monkeypatch.setattr(ui.FakeLLM, 'chat', chat)
    try:
        page.click('#drawer-open'); page.click('#ai-manual summary')
        page.select_option('#ai-node', 'notes'); page.click('#ai-run')
        assert entered.wait(4)
        page.click('#home-back')
        page.reload()
        page.set_viewport_size({'width': 390, 'height': 844})
        active = page.locator(f'#active-jobs button[data-job-id="{job_id}"]')
        expect(active).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        capture(page, 'manual-ai-home-mobile')
        active.click()
        expect(page.locator('#ai-progress')).to_have_attribute('aria-busy', 'true')
        expect(page.locator('#ai-failed')).not_to_be_visible()
        release.set()
        expect(page.locator('#ai-output')).to_be_visible(timeout=20000)
        assert len(calls) == 1
    finally:
        release.set()


def test_back_during_ai_history_reopens_running_then_finished_without_new_calls(page, server, monkeypatch):
    ui.add_llm(page, server)
    page.goto(server[0] + '/')
    page.wait_for_selector('body[data-ready="1"]')
    entered, release = threading.Event(), threading.Event()
    original = ui.FakeLLM.chat
    calls = []

    def chat(self, messages):
        calls.append(messages)
        entered.set()
        assert release.wait(30), 'test did not release model'
        return original(self, messages)

    monkeypatch.setattr(ui.FakeLLM, 'chat', chat)
    page.select_option('#style', 'structure')
    try:
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/api/jobs')) as response:
            ui.submit(page, 'https://www.douyin.com/video/9802')
        job_id = response.value.json()['id']
        assert entered.wait(4)
        page.wait_for_selector('#ai-progress[aria-busy=true]')
        page.click('#home-back'); page.click('#history-btn')
        record = page.locator(f'#history-list li[data-job-id="{job_id}"]')
        expect(record).to_be_visible(timeout=2500)
        expect(record.locator('.history-state')).to_contain_text('中')
        pending = record.locator('.ai-runs button')
        expect(pending).to_contain_text('中')
        assert len(calls) == 1
        capture(page, 'ai-history')
        pending.click()
        expect(page.locator('#ai-progress')).to_have_attribute('aria-busy', 'true')
        expect(page.locator('#ai-failed')).not_to_be_visible()
        expect(page.locator('#preview-source')).to_be_visible()
        page.click('#home-back'); page.click('#history-btn'); page.reload()
        expect(record.locator('.ai-runs button')).to_be_visible()
        release.set()
        expect(record.locator('.history-state')).to_have_count(0, timeout=20000)
        expect(page.locator('body')).to_have_attribute('data-state', 'hist')
        record.locator('.ai-runs button').click()
        expect(page.locator('#ai-output')).to_be_visible()
        assert len(calls) == 1
        capture(page, 'finished-history-result')
    finally:
        release.set()


def test_delayed_manual_submission_cannot_replace_another_record(page, server):
    ui.add_llm(page, server)
    page.reload(); page.wait_for_selector('body[data-ready="1"]')
    page.select_option('#style', 'raw')
    ui.submit(page, 'https://www.douyin.com/video/9810')
    expect(page.locator('#result')).to_be_visible()
    delayed = []

    def hold_response(route):
        response = route.fetch()
        delayed.append((route, response))

    page.route('**/api/jobs/*/ai', hold_response)
    page.click('#drawer-open'); page.click('#ai-manual summary')
    page.select_option('#ai-node', 'structure'); page.click('#ai-run')
    page.wait_for_timeout(250)
    assert len(delayed) == 1
    try:
        page.click('#home-back')
        page.select_option('#style', 'raw')
        ui.submit(page, 'https://www.douyin.com/video/9811')
        expect(page.locator('#result')).to_be_visible()
        expect(page.locator('#ai-nodes button')).to_have_count(0)
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/ai')):
            route, response = delayed.pop()
            route.fulfill(response=response)
        page.wait_for_timeout(1800)
        expect(page.locator('#ai-nodes button')).to_have_count(0)
        expect(page.locator('#ai-output')).not_to_be_visible()
        expect(page.locator('#url')).to_have_value('https://www.douyin.com/video/9811')
    finally:
        for route, response in delayed:
            route.fulfill(response=response)
