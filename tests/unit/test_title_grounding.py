import json

import pytest

from vtx.llm import process
from vtx.proofread import proofread
from vtx.system_prompt import EditorialClient


class Capture:
    context = 32000

    def __init__(self, reply='结果'):
        self.calls = []
        self.reply = reply

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply, {}


def test_title_reaches_custom_prompt_without_title_variable_and_every_chunk_merge():
    raw = Capture()
    raw.context = 4000
    client = EditorialClient(raw, '规则', title='GLM 5.3 Flash <评测>')
    result = process(client, '整理：{text}', '内容。' * 5000)
    assert result.chunks > 1
    assert len(raw.calls) > result.chunks
    for messages in raw.calls:
        assert messages[0] == {'role': 'system', 'content': '规则'}
        assert '<video_title>GLM 5.3 Flash &lt;评测&gt;</video_title>' in messages[1]['content']
        assert '校准' in messages[1]['content']


def test_proofread_can_cite_title_as_independent_named_source():
    title = 'GLM 5.3 Flash 模型实测'
    raw = Capture(json.dumps({'patches': [{
        'id': '/markdown_body', 'old': 'GLM 5.2 Flash', 'new': 'GLM 5.3 Flash',
        'evidence': [{'source': 'title', 'quote': 'GLM 5.3 Flash'}],
    }]}))
    result = proofread(EditorialClient(raw, '规则', title=title),
                       {'markdown_body': 'GLM 5.2 Flash 的生成效果。'},
                       'GLM五点三flash的生成效果。', title=title)
    assert result.doc['markdown_body'] == 'GLM 5.3 Flash 的生成效果。'
    assert result.audit['sources']['title'] == [title]
    payload = json.loads(raw.calls[0][-1]['content'].split('输入JSON：\n')[1])
    assert payload['sources']['title'] == [title]
    assert payload['sources']['reference'] == []


def test_every_proofread_batch_keeps_title_within_budget():
    from vtx.llm import estimate_tokens
    from vtx.proofread import _batches, _leaves, _prompt

    text = '\n\n'.join(f'第{i}段说明模型的生成效果。' * 15 for i in range(8))
    leaves = {pointer: (path, value) for pointer, path, value in _leaves({'markdown_body': text})}
    plans = _batches(leaves, text * 4, '', 2500,
                     title='GLM 5.3 Flash 实测')
    assert len(plans) > 1
    assert ''.join(item['text'] for draft, _ in plans for item in draft) == text
    for draft, sources in plans:
        assert sources['title'] == ['GLM 5.3 Flash 实测']
        assert estimate_tokens(_prompt(draft, sources)) <= 2500


def test_fabricated_title_evidence_is_rejected():
    from vtx.llm import LLMError

    raw = Capture(json.dumps({'patches': [{
        'id': '/markdown_body', 'old': 'GLM 5.2', 'new': 'GLM 5.4',
        'evidence': [{'source': 'title', 'quote': 'GLM 5.4'}],
    }]}))
    with pytest.raises(LLMError):
        proofread(raw, {'markdown_body': 'GLM 5.2 的效果。'},
                  'GLM五点二的效果。', title='GLM 5.3 实测')


def test_previous_default_upgrades_but_custom_rules_survive(tmp_path, monkeypatch):
    import hashlib
    from vtx import system_prompt
    from vtx.server import SettingsStore

    old = '旧版默认规则'
    monkeypatch.setattr(system_prompt, 'LEGACY_SYSTEM_PROMPT_HASHES',
                        {hashlib.sha256(old.encode()).hexdigest()})
    store = SettingsStore(tmp_path / 'unused.env', tmp_path, tmp_path / 'settings.json')
    store.set_system_prompt(old)
    assert store.system_prompt() == system_prompt.DEFAULT_SYSTEM_PROMPT
    store.set_system_prompt('我的规则')
    assert store.system_prompt() == '我的规则'
