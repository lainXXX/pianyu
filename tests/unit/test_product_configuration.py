import json
from pathlib import Path

from fastapi.testclient import TestClient

from vtx import config
from vtx.configuration import AppPaths
from vtx.server import create_app


def test_app_runtime_json_and_environment_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.delenv('VTX_CONFIG_DIR', raising=False)
    monkeypatch.setenv('VTX_WORK_DIR', str(tmp_path / 'work'))
    paths = AppPaths(tmp_path, tmp_path / 'work')
    paths.app.parent.mkdir()
    paths.app.write_text(json.dumps({'browser': 'custom-browser', 'ffmpeg': 'custom-ffmpeg',
                                    'proxy_youtube': 'http://proxy.invalid', 'cdp_port': 9444}), encoding='utf-8')
    for name in ['VTX_BROWSER', 'VTX_FFMPEG', 'VTX_PROXY_YOUTUBE', 'VTX_CDP_PORT', 'VTX_COOKIES_DIR']:
        monkeypatch.delenv(name, raising=False)
    settings = config.load()
    assert settings.browser == 'custom-browser' and settings.cdp_port == 9444
    assert settings.cookies_dir == paths.cookies_dir
    monkeypatch.setenv('VTX_CDP_PORT', '9555')
    assert config.load().cdp_port == 9555


def test_real_application_splits_config_reopens_and_preserves_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(config, 'ROOT', tmp_path)
    monkeypatch.delenv('VTX_CONFIG_DIR', raising=False)
    monkeypatch.delenv('DASHSCOPE_API_KEY', raising=False)
    work = tmp_path / 'work'
    work.mkdir()
    old = {'system_prompt': '自定义规则', 'proxy_youtube': ''}
    (work / 'settings.json').write_text(json.dumps(old), encoding='utf-8')
    cookie = '# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tsynthetic-only\n'
    (tmp_path / 'cookies-bilibili.txt').write_text(cookie, encoding='utf-8')
    with TestClient(create_app(work, reveal_keys=True)) as client:
        assert client.get('/api/llm/settings').json()['system_prompt'] == '自定义规则'
        assert client.get('/api/settings').json()['cookies']['bilibili']
        client.put('/api/llm/settings', json={'system_prompt': '# 新规则'})
        client.post('/api/settings', json={'proxy_youtube': 'http://proxy.invalid'})
        result = client.post('/api/engines', json={'kind': 'openai', 'label': '测试',
                             'base_url': 'https://1.1.1.1/v1', 'model': 'test-asr', 'key': 'synthetic-asr'})
        assert result.status_code == 200, result.text
        result = client.post('/api/llm/services', json={'label': '测试', 'base_url': 'https://1.1.1.1/v1',
                             'model': 'test-llm', 'key': 'synthetic-llm'})
        assert result.status_code == 200, result.text
        diagnostics = client.get('/api/diagnostics').json()
        assert diagnostics['paths']['config'] == str((tmp_path / 'config').resolve())
    paths = AppPaths(tmp_path, work)
    assert 'system_prompt' not in paths.app.read_text(encoding='utf-8')
    assert paths.system_prompt.read_text(encoding='utf-8') == '# 新规则'
    assert 'synthetic-asr' in paths.asr_secrets.read_text(encoding='utf-8')
    assert 'synthetic-asr' not in paths.asr_services.read_text(encoding='utf-8')
    assert 'synthetic-llm' in paths.llm_secrets.read_text(encoding='utf-8')
    assert 'synthetic-llm' not in paths.llm_services.read_text(encoding='utf-8')
    assert json.loads((work / 'settings.json').read_text(encoding='utf-8')) == old
    with TestClient(create_app(work, reveal_keys=False)) as reopened:
        assert reopened.get('/api/llm/settings').json()['system_prompt'] == '# 新规则'
        assert reopened.get('/api/diagnostics').status_code == 403
        assert reopened.post('/api/settings/cookies/bilibili/login').status_code == 403
        assert len(reopened.get('/api/llm/services').json()['services']) == 1
    content = (paths.logs / 'app.jsonl').read_text(encoding='utf-8')
    assert all(value not in content for value in ['synthetic-asr', 'synthetic-llm', 'synthetic-only', '# 新规则'])
