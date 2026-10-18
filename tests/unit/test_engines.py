"""T18 acceptance 1-3: service registry, secrets never returned, URL validation, cache identity."""
import ipaddress
import json

import pytest

from vtx.engines import EngineError, Registry, validate_base_url
from vtx.pipeline import asr_cache_key


def fake_resolve(table):
    def resolve(host):
        if host not in table:
            raise OSError("unknown host")
        return table[host]
    return resolve


PUBLIC = fake_resolve({"api.example.com": ["93.184.216.34"], "inner.corp": ["10.1.2.3"],
                       "localhost": ["127.0.0.1"], "mixed.example": ["93.184.216.34", "192.168.1.9"]})


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    return Registry(tmp_path / "engines.json", tmp_path / "engine_keys.json", env_file=tmp_path / ".env",
                    resolve=PUBLIC)


def test_initial_dashscope_is_an_ordinary_service(reg):
    ids = [s["id"] for s in reg.list()]
    assert ids[0] == "dashscope"
    ds = reg.list()[0]
    assert not ds["builtin"] and ds["model"] == "paraformer-v2" and not ds["configured"]


def test_add_update_delete_custom(reg):
    sid = reg.add({"kind": "openai", "label": "我的服务", "base_url": "https://api.example.com/v1",
                   "model": "whisper-1"}, key="sk-secret-123456")
    svc = reg.get(sid)
    assert svc["configured"] and svc["label"] == "我的服务"
    reg.update(sid, {"label": "改名", "model": "whisper-large-v3"})
    assert reg.get(sid)["label"] == "改名" and reg.get(sid)["model"] == "whisper-large-v3"
    reg.update(sid, {}, key="sk-new-key-654321")
    assert reg.key(sid) == "sk-new-key-654321"
    reg.delete(sid)
    with pytest.raises(EngineError):
        reg.get(sid)
    assert sid not in json.loads((reg.keys_file).read_text(encoding="utf-8"))


def test_initial_service_can_be_renamed_and_deleted_permanently(reg):
    reg.update("dashscope", {"model": "fun-asr", "label": "语音"})
    assert reg.get("dashscope")["model"] == "fun-asr" and reg.get("dashscope")["label"] == "语音"
    with pytest.raises(EngineError):
        reg.update("dashscope", {"model": "not-a-model"})
    reg.delete("dashscope")
    restarted = Registry(reg.config_file, reg.keys_file, resolve=PUBLIC)
    assert restarted.list() == []


def test_listing_and_files_never_contain_keys(reg):
    sid = reg.add({"kind": "openai", "label": "x", "base_url": "https://api.example.com/v1", "model": "m"},
                  key="sk-topsecret-abcdef")
    reg.update("dashscope", {}, key="sk-dashscope-xyz987")
    dumped = json.dumps(reg.list()) + json.dumps(reg.get(sid)) + reg.config_file.read_text(encoding="utf-8")
    assert "topsecret" not in dumped and "xyz987" not in dumped
    with pytest.raises(EngineError) as info:
        reg.update(sid, {"base_url": "http://10.0.0.1/v1"}, key="sk-should-not-leak-1")
    assert "sk-should-not-leak-1" not in str(info.value)


def test_dashscope_key_falls_back_to_env_file(reg, tmp_path):
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=sk-from-env-file-123\n", encoding="utf-8")
    assert reg.get("dashscope")["configured"] and reg.key("dashscope") == "sk-from-env-file-123"


def test_default_and_last_used(reg):
    assert reg.default() is None                        # nothing configured
    a = reg.add({"kind": "openai", "label": "a", "base_url": "https://api.example.com/v1", "model": "m"}, key="sk-aaaaaaaaaa")
    b = reg.add({"kind": "openai", "label": "b", "base_url": "https://api.example.com/v1", "model": "m"}, key="sk-bbbbbbbbbb")
    assert reg.default() == a                           # first configured
    reg.mark_used(b)
    assert reg.default() == b
    reg.delete(b)
    assert reg.default() == a                           # falls back when last-used disappears


def test_set_default(reg):
    a = reg.add({"kind": "openai", "label": "a", "base_url": "https://api.example.com/v1", "model": "m"}, key="sk-aaaaaaaaaa")
    b = reg.add({"kind": "openai", "label": "b", "base_url": "https://api.example.com/v1", "model": "m"}, key="sk-bbbbbbbbbb")
    reg.set_default(b)
    assert reg.default() == b
    reg.mark_used(a)
    assert reg.default() == b                           # set_default wins over mark_used
    with pytest.raises(EngineError):
        reg.set_default("nonexistent-id")
    with pytest.raises(EngineError):
        reg.set_default("dashscope")                    # builtin but not configured


def test_unknown_kind_and_missing_fields_rejected(reg):
    with pytest.raises(EngineError):
        reg.add({"kind": "nope", "label": "x", "model": "m"}, key="k")
    with pytest.raises(EngineError):
        reg.add({"kind": "openai", "label": "x", "model": "m"}, key="sk-xxxxxxxxx")   # no base_url
    with pytest.raises(EngineError):
        reg.add({"kind": "openai", "label": "", "base_url": "https://api.example.com/v1", "model": "m"}, key="k")


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://api.example.com/v1", "https:///v1", "https://user:pw@api.example.com/v1",
    "http://10.0.0.1/v1", "http://172.16.5.4/v1", "http://192.168.1.1/v1", "http://169.254.169.254/latest",
    "http://[::1]/v1", "http://[fd00::1]/v1", "http://0.0.0.0/v1", "https://inner.corp/v1", "https://mixed.example/v1",
    "http://127.0.0.1:8000/v1", "http://localhost:8000/v1", "https://no-such-host.invalid/v1",
])
def test_bad_base_urls_rejected(url):
    with pytest.raises(EngineError):
        validate_base_url(url, allow_local=False, resolve=PUBLIC)


def test_public_url_ok_and_local_only_with_flag():
    validate_base_url("https://api.example.com/v1", allow_local=False, resolve=PUBLIC)
    validate_base_url("http://127.0.0.1:8000/v1", allow_local=True, resolve=PUBLIC)
    validate_base_url("http://localhost:8000/v1", allow_local=True, resolve=PUBLIC)


@pytest.mark.parametrize('host', ['10.0.0.77', '172.16.5.4', '192.168.1.1', '[fd00::1]',
                                  '[::ffff:10.0.0.77]'])
def test_private_service_requires_explicit_opt_in(host):
    url = f'http://{host}:8000/v1'
    with pytest.raises(EngineError):
        validate_base_url(url, allow_local=False, resolve=PUBLIC)
    assert validate_base_url(url, allow_local=True, resolve=PUBLIC) == url


def test_private_domain_requires_explicit_opt_in():
    url = 'https://ai.internal.example.com/v1'
    resolve = fake_resolve({'ai.internal.example.com': ['10.0.0.77']})
    with pytest.raises(EngineError, match='允许内网地址'):
        validate_base_url(url, allow_local=False, resolve=resolve)
    assert validate_base_url(url, allow_local=True, resolve=resolve) == url


@pytest.mark.parametrize('host', ['169.254.169.254', '0.0.0.0', '224.0.0.1', '192.0.2.1',
                                  '[fe80::1]', '[::]', '[ff02::1]', '[::ffff:169.254.169.254]'])
def test_local_opt_in_does_not_allow_special_addresses(host):
    with pytest.raises(EngineError):
        validate_base_url(f'http://{host}/v1', allow_local=True, resolve=PUBLIC)


def test_cache_key_depends_on_engine_and_model():
    audio = "a" * 64
    same = asr_cache_key("dashscope/paraformer-v2", audio)
    assert same == asr_cache_key("dashscope/paraformer-v2", audio)
    assert same != asr_cache_key("dashscope/fun-asr", audio)
    assert same != asr_cache_key("openai:svc1/whisper-1", audio)
    assert same != asr_cache_key("dashscope/paraformer-v2", "b" * 64)


def test_build_dashscope_uses_selected_model(reg):
    reg.update("dashscope", {"model": "fun-asr"}, key="sk-dashscope-test-123")
    engine = reg.build("dashscope")
    assert engine.name == "dashscope/fun-asr"


def test_build_tencent_needs_both_secrets(reg):
    sid = reg.add({"kind": "tencent", "label": "腾讯", "model": "16k_zh"}, key="AKIDonly")
    with pytest.raises(EngineError) as info:
        reg.build(sid)
    assert info.value.code == "ASR_NOT_CONFIGURED" and "AKIDonly" not in str(info.value)
    reg.update(sid, {}, key="AKIDexample:secretKEY")
    engine = reg.build(sid)
    assert engine.name == f"tencent:{sid}/16k_zh"
    assert "secretKEY" not in json.dumps(reg.get(sid))


def test_build_unconfigured_is_explicit(reg):
    with pytest.raises(EngineError) as info:
        reg.build("dashscope")
    assert info.value.code == "ASR_NOT_CONFIGURED"


def test_tencent_secrets_split_and_join(reg):
    with pytest.raises(EngineError):
        reg.join_secrets("tencent", {"secret_id": "AKID1"})                       # second part missing
    with pytest.raises(EngineError):
        reg.join_secrets("tencent", {"secret_id": "A:B", "secret_key": "k"})      # colon would corrupt storage
    key = reg.join_secrets("tencent", {"secret_id": "AKID1", "secret_key": "sk1"})
    sid = reg.add({"kind": "tencent", "label": "腾讯", "model": "16k_zh"}, key=key)
    assert reg.secrets(sid) == {"secret_id": "AKID1", "secret_key": "sk1"}
    assert [f["name"] for f in reg.get(sid)["secret_fields"]] == ["secret_id", "secret_key"]
    # editing: a blank field keeps its saved value; all blank = no change
    assert reg.join_secrets("tencent", {"secret_key": "sk2"}, sid=sid) == "AKID1:sk2"
    assert reg.join_secrets("tencent", {}, sid=sid) == ""
    assert reg.build(sid).name == f"tencent:{sid}/16k_zh"


def test_single_key_secrets(reg):
    reg.update("dashscope", {}, key=reg.join_secrets("dashscope", {"key": " sk-abc-123456 "}))
    assert reg.secrets("dashscope") == {"key": "sk-abc-123456"}
