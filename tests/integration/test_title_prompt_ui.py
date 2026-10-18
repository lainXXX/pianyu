"""Real Edge clicks against isolated storage and a captured model substitute."""
import json
from pathlib import Path

import pytest

from test_web_ui import (META, FakeLLM, add_llm, browser, page, server, settings,
                         submit, wait_ai_done, wf_set)
from vtx.system_prompt import DEFAULT_SYSTEM_PROMPT

pytestmark = pytest.mark.integration


def test_rules_and_title_through_visible_generation(page, server, monkeypatch):
    seen = []
    original_chat = FakeLLM.chat

    def capture(self, messages):
        seen.append(messages)
        return original_chat(self, messages)

    monkeypatch.setattr(FakeLLM, 'chat', capture)
    settings(page, server, 'system')
    assert page.input_value('#system-prompt') == DEFAULT_SYSTEM_PROMPT
    page.fill('#system-prompt', DEFAULT_SYSTEM_PROMPT + '\n保持简洁。')
    page.locator('#system-form button[type=submit]').click()
    page.wait_for_selector('#system-msg:has-text("已保存")')
    page.reload()
    assert page.input_value('#system-prompt').endswith('保持简洁。')
    output = Path(__file__).resolve().parents[2] / 'acceptance' / 'title-grounding'
    output.mkdir(parents=True, exist_ok=True)
    for width, height in [(1440, 1000), (390, 844)]:
        page.set_viewport_size({'width': width, 'height': height})
        page.screenshot(path=str(output / f'rules-{width}.png'), full_page=True)
        assert page.locator('#system-prompt').is_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    add_llm(page, server)
    page.goto(server[0] + '/')
    wf_set(page, 'notes')
    submit(page, 'https://www.bilibili.com/video/BV1wFZ8YBEt4')
    wait_ai_done(page, 1)
    assert len(seen) == 2  # generate then proofread
    for messages in seen:
        assert messages[0]['content'].endswith('保持简洁。')
        assert '<video_title>' + META['title'] + '</video_title>' in messages[1]['content']
    payload = json.loads(seen[-1][-1]['content'].split('输入JSON：\n', 1)[1])
    assert payload['sources']['title'] == [META['title']]
    page.wait_for_selector('#ai-pages img')
    page.wait_for_function("document.querySelector('#ai-pages img').naturalWidth > 0")
