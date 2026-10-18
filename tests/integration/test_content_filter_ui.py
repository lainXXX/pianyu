"""Explicit off-topic omissions survive the real reader/render/export pipeline."""
import json
from pathlib import Path

import pytest
import test_web_ui as ui
from test_web_ui import browser, page, server
from vtx.asr import Segment

pytestmark = pytest.mark.integration

MAIN = ["这个模型生成的程序化3D城市结构清晰。", "同样设置下生成耗时12秒，不适合实时交互。"]
OUTRO = ["大家好，欢迎来到我的频道。",
         "因为这期可能会比较有流量，所以我也会选择这期发在大号上面。",
         "后面这种视频我可能会放在我的小号，因为大号还是要保证数据稳定，才能稳定地接商单变现。",
         "大家想及时沟通的话，我可能就在小号随手录个视频发给你们看了。",
         "大家也可以去关注一下我们的小号，谢谢观看，下期再见。"]


@pytest.mark.parametrize('style', ['structure', 'refine'])
def test_long_channel_outro_hidden_but_source_retained(page, server, monkeypatch, style):
    # Real browser and local processing; model decisions are supplied by a substitute.
    sentences = MAIN + OUTRO
    monkeypatch.setattr(ui, 'SEGS', [Segment(i, i, i + 1, text) for i, text in enumerate(sentences)])
    monkeypatch.setitem(ui.META, 'title', '程序化3D城市模型实测')

    def chat(self, messages):
        assert '不按关键词机械删除' in messages[0]['content']
        prompt = messages[-1]['content']
        values = json.loads(prompt.rsplit('<transcript>\n', 1)[1].split('\n</transcript>', 1)[0])
        if style == 'structure':
            removed = [x['i'] for x in values if x['s'] in OUTRO]
            ids = [x['i'] for x in values if x['s'] not in OUTRO]
            result = {'sections': [{'heading': '生成效果', 'blocks': [{'type': 'p', 'ids': ids}]}],
                      'removed': removed}
        else:
            result = []
            for item in values:
                text = item['text']
                removed = [sentence for sentence in OUTRO if sentence in text]
                for sentence in removed:
                    text = text.replace(sentence, '', 1)
                result.append({'n': item['n'], 'text': text, 'removed': removed})
        return json.dumps(result, ensure_ascii=False), {}

    monkeypatch.setattr(ui.FakeLLM, 'chat', chat)
    ui.add_llm(page, server)
    page.goto(server[0] + '/')
    ui.wf_set(page, style)
    ui.submit(page, 'https://www.bilibili.com/video/BV1wFZ8YBEt4')
    page.wait_for_selector('#ai-pages img')
    page.wait_for_function("document.querySelector('#ai-pages img').naturalWidth > 0")
    page.click('#ai-tab-text')
    text = page.inner_text('#ai-text')
    assert all(sentence in text for sentence in MAIN)
    assert not any(word in text for word in ['小号', '商单', '欢迎', '谢谢观看'])
    page.click('#ai-copy')
    page.wait_for_selector('#ai-copy:has-text("已复制")')
    copied = page.evaluate('navigator.clipboard.readText()')
    assert all(sentence in copied for sentence in MAIN)
    assert '小号' not in copied
    directory = Path(__file__).resolve().parents[2] / 'acceptance' / 'content-filter'
    directory.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(directory / f'{style}-reader.png'), full_page=True)
    page.click('#drawer-open')
    page.click('.recognition-info > summary')
    page.click('#ai-change-details summary')
    assert '小号' in page.inner_text('#ai-refine-stats')
