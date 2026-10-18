"""T23 acceptance 1-3: LLM service registry, chat adapter, long-text chunking (HTTP stubbed)."""
import json

import pytest

from vtx.engines import EngineError
from vtx.llm import ChatClient, LLMError, LLMRegistry, PRESETS, estimate_tokens, process, split_text

RESOLVE = lambda host: {"api.example.com": ["93.184.216.34"]}[host]


@pytest.fixture
def reg(tmp_path):
    return LLMRegistry(tmp_path / "llm.json", tmp_path / "llm_keys.json",
                       fallback_keys={"dashscope": lambda: "sk-dashscope-shared-1"}, resolve=RESOLVE)


# ---------- registry

def test_presets_cover_mainstream_providers():
    names = {p["preset"] for p in PRESETS}
    assert {"deepseek", "qwen", "kimi", "zhipu", "doubao", "openai", "gemini", "custom"} <= names
    assert next(p for p in PRESETS if p["preset"] == "qwen")["key_from"] == "dashscope"


def test_crud_and_keys_never_returned(reg):
    sid = reg.add({"label": "DeepSeek", "base_url": "https://api.example.com/v1", "model": "deepseek-chat",
                   "context": 64000}, key="sk-deepseek-secret-42")
    assert reg.get(sid)["configured"] and reg.get(sid)["context"] == 64000
    reg.update(sid, {"model": "deepseek-reasoner"})
    assert reg.get(sid)["model"] == "deepseek-reasoner"
    dumped = json.dumps(reg.list()) + reg.config_file.read_text(encoding="utf-8")
    assert "deepseek-secret" not in dumped
    with pytest.raises(EngineError) as info:
        reg.update(sid, {"base_url": "http://10.0.0.1/v1"}, key="sk-leak-this-key-1")
    assert "leak-this" not in str(info.value)
    reg.delete(sid)
    with pytest.raises(EngineError):
        reg.get(sid)


def test_reuse_dashscope_key(reg):
    sid = reg.add({"label": "通义千问", "base_url": "https://api.example.com/v1", "model": "qwen-plus",
                   "key_from": "dashscope"}, key="")
    assert reg.get(sid)["configured"] and reg.key(sid) == "sk-dashscope-shared-1"
    assert "shared" not in json.dumps(reg.list())


def test_add_requires_key_unless_reused(reg):
    with pytest.raises(EngineError):
        reg.add({"label": "x", "base_url": "https://api.example.com/v1", "model": "m"}, key="")


def test_private_domain_opt_in_survives_edit_restart_and_client_build(tmp_path):
    def resolve(host):
        assert host == 'ai.internal.example.com'
        return ['10.0.0.77']

    reg = LLMRegistry(tmp_path / 'llm.json', tmp_path / 'keys.json', resolve=resolve)
    fields = {'label': 'GPT', 'base_url': 'https://ai.internal.example.com/v1',
              'model': 'gpt-6.1-sol', 'context': 1000000, 'allow_local': False}
    with pytest.raises(EngineError, match='允许内网地址'):
        reg.add(fields, key='fake-private-service-key')
    assert reg.list() == []
    sid = reg.add(dict(fields, allow_local=True), key='fake-private-service-key')
    reg.update(sid, {'label': 'GPT 内网'})
    restored = LLMRegistry(reg.config_file, reg.keys_file, resolve=resolve)
    assert restored.get(sid)['allow_local'] is True
    client = restored.client(sid)
    assert client.base_url == fields['base_url'] and client.context == 1000000
    with pytest.raises(EngineError):
        restored.update(sid, {'allow_local': False})
    assert restored.get(sid)['allow_local'] is True


def test_default_and_last_used(reg):
    assert reg.default() is None
    a = reg.add({"label": "a", "base_url": "https://api.example.com/v1", "model": "m"}, key="sk-aaaaaaaa")
    b = reg.add({"label": "b", "base_url": "https://api.example.com/v1", "model": "m"}, key="sk-bbbbbbbb")
    assert reg.default() == a
    reg.mark_used(b)
    assert reg.default() == b


def test_set_default(reg):
    a = reg.add({"label": "a", "base_url": "https://api.example.com/v1", "model": "m"}, key="sk-aaaaaaaa")
    b = reg.add({"label": "b", "base_url": "https://api.example.com/v1", "model": "m"}, key="sk-bbbbbbbb")
    reg.set_default(b)
    assert reg.default() == b
    reg.mark_used(a)
    assert reg.default() == b                           # set_default wins over mark_used
    with pytest.raises(EngineError):
        reg.set_default("nonexistent-id")
    # remove key from b
    keys = reg._keys()
    keys[b] = ""
    reg._save_keys(keys)
    with pytest.raises(EngineError):
        reg.set_default(b)                              # not configured


def test_context_must_be_reasonable(reg):
    with pytest.raises(EngineError):
        reg.add({"label": "x", "base_url": "https://api.example.com/v1", "model": "m", "context": 100}, key="sk-x123456")


def test_preview_client_does_not_persist_and_supports_shared_key(reg):
    fields = {"label": "新服务", "base_url": "https://api.example.com/v1/", "model": "new-model",
              "key_from": "dashscope", "context": 64000}
    client = reg.preview_client(fields)
    assert client.model == "new-model" and client._key == "sk-dashscope-shared-1"
    assert client.base_url == "https://api.example.com/v1" and client.context == 64000
    assert not reg.config_file.exists() and not reg.keys_file.exists()


@pytest.mark.parametrize("key", [None, "", "********"])
def test_preview_edit_uses_current_fields_and_saved_key_without_changes(reg, key):
    sid = reg.add({"label": "旧服务", "base_url": "https://api.example.com/v1", "model": "old"}, "saved-test-key")
    reg.set_default(sid)
    before = (reg.config_file.read_bytes(), reg.keys_file.read_bytes())
    client = reg.preview_client({"base_url": "https://api.example.com/v2", "model": "edited"}, key=key, sid=sid)
    assert client.base_url.endswith("/v2") and client.model == "edited" and client._key == "saved-test-key"
    assert (reg.config_file.read_bytes(), reg.keys_file.read_bytes()) == before


def test_preview_validates_private_addresses_and_key(reg):
    reg._resolve = lambda host: ["10.0.0.77"]
    fields = {"label": "内网", "base_url": "https://api.example.com/v1", "model": "m"}
    with pytest.raises(EngineError, match="允许内网地址"):
        reg.preview_client(fields, key="draft-test-key")
    assert reg.preview_client(dict(fields, allow_local=True), key="draft-test-key").model == "m"
    with pytest.raises(EngineError, match="API key"):
        reg.preview_client(dict(fields, allow_local=True))
    assert not reg.config_file.exists() and not reg.keys_file.exists()


# ---------- chat adapter

class Resp:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body, ensure_ascii=False)

    def json(self):
        return self._body


def ok(content, prompt=10, completion=5):
    return Resp(200, {"choices": [{"message": {"content": content}}],
                      "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}})


def test_chat_request_and_response():
    seen = {}

    def post(url, **kw):
        seen.update(url=url, **kw)
        return ok("结果文本")

    client = ChatClient("https://api.example.com/v1", "deepseek-chat", "sk-secret-chat-1", post=post)
    text, usage = client.chat([{"role": "user", "content": "你好"}])
    assert text == "结果文本" and usage == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer sk-secret-chat-1"
    assert seen["json"]["model"] == "deepseek-chat" and seen["json"]["messages"][0]["content"] == "你好"
    assert "stream" not in seen["json"] or seen["json"]["stream"] is False


@pytest.mark.parametrize("status,message,code", [
    (401, "invalid api key", "LLM_ACCESS_DENIED"),
    (403, "forbidden", "LLM_ACCESS_DENIED"),
    (429, "rate limit", "LLM_RATE_LIMITED"),
    (402, "insufficient balance", "LLM_BILLING_UNAVAILABLE"),
    (400, "This model's maximum context length is 32768 tokens", "LLM_TOO_LONG"),
    (500, "internal", "LLM_SERVICE_ERROR"),
])
def test_chat_errors(status, message, code):
    client = ChatClient("https://api.example.com/v1", "m", "sk-secret-chat-1",
                        post=lambda url, **kw: Resp(status, {"error": {"message": message + " sk-secret-chat-1"}}))
    with pytest.raises(LLMError) as info:
        client.chat([{"role": "user", "content": "x"}])
    assert info.value.code == code and "sk-secret-chat-1" not in str(info.value)


def test_chat_empty_choice_is_error():
    client = ChatClient("https://api.example.com/v1", "m", "k", post=lambda url, **kw: Resp(200, {"choices": []}))
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])


# ---------- chunking

def test_estimate_tokens_is_conservative():
    assert estimate_tokens("你好世界") >= 4
    assert estimate_tokens("hello world " * 10) >= 20


def test_split_text_lossless_and_bounded():
    paras = [("第%d段。" % i) + "内容" * 300 for i in range(10)]
    text = "\n\n".join(paras)
    chunks = split_text(text, budget=1500)
    assert "".join(chunks) == text
    assert all(estimate_tokens(c) <= 1500 for c in chunks)
    assert len(chunks) > 1


def test_split_text_handles_one_huge_paragraph():
    text = "这是一句很长的话。" * 2000            # no paragraph breaks at all
    chunks = split_text(text, budget=1000)
    assert "".join(chunks) == text and all(estimate_tokens(c) <= 1000 for c in chunks)


class FakeClient:
    def __init__(self, context):
        self.context, self.calls = context, []

    def chat(self, messages):
        self.calls.append(messages)
        content = messages[-1]["content"]
        return f"[结果{len(self.calls)}]", {"prompt_tokens": estimate_tokens(content), "completion_tokens": 3,
                                          "total_tokens": estimate_tokens(content) + 3}


def test_short_text_single_call():
    c = FakeClient(context=32000)
    r = process(c, "请总结：{text}", "短文本。", title="标题")
    assert len(c.calls) == 1 and r.chunks == 1 and r.text == "[结果1]"
    assert c.calls[0][-1]["content"] == "请总结：短文本。"
    assert r.usage["total_tokens"] == estimate_tokens("请总结：短文本。") + 3


def test_long_text_chunked_then_merged():
    c = FakeClient(context=4000)                # input budget 2400 tokens
    text = "\n\n".join("段落%d。" % i + "字" * 900 for i in range(6))
    r = process(c, "总结要点：\n{text}", text, title="长视频")
    assert r.chunks > 1 and len(c.calls) == r.chunks + 1      # N chunk calls + 1 merge
    merge_prompt = c.calls[-1][-1]["content"]
    assert "合并" in merge_prompt and all(f"[结果{i}]" in merge_prompt for i in range(1, r.chunks + 1))
    assert r.text == f"[结果{r.chunks + 1}]"
    assert r.usage["total_tokens"] == sum(estimate_tokens(m[-1]["content"]) + 3 for m in c.calls)
    for m in c.calls[:-1]:
        assert estimate_tokens(m[-1]["content"]) <= int(4000 * 0.6)


def test_oversized_results_are_hierarchically_merged_without_dropping_source():
    import re

    class DetailedClient:
        context = 4000

        def __init__(self):
            self.calls = []

        def chat(self, messages):
            prompt = messages[-1]["content"]
            self.calls.append(prompt)
            assert estimate_tokens(prompt) <= int(self.context * 0.6)
            facts = list(dict.fromkeys(re.findall(r"FACT_\d+", prompt)))
            # Source chunks produce detailed articles; merge calls preserve their facts compactly.
            out = " ".join(facts) + ("详" * 1000 if "<parts>" not in prompt else "合" * 250)
            return out, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

    c = DetailedClient()
    source = "\n\n".join(f"FACT_{i}：" + "源" * 1200 for i in range(8))
    result = process(c, "写一篇连贯文章：{text}", source)
    assert all(f"FACT_{i}" in result.text for i in range(8))
    assert "【第" not in result.text
    assert len(c.calls) > result.chunks + 1
    assert result.usage["total_tokens"] == 30 * len(c.calls)
    source_calls = [p for p in c.calls if "<parts>" not in p]
    # Every original character is submitted; the fitting strategy never clips the transcript.
    submitted = "".join(p.split("写一篇连贯文章：", 1)[1].split("\n\n## 当前片段", 1)[0] for p in source_calls)
    assert submitted == source
    assert all("不写分段编号、处理过程或边界缺失说明" in p for p in source_calls)


def test_merge_refuses_nonshrinking_results_instead_of_returning_chunk_dump():
    class ExpandingClient:
        context = 2000

        def __init__(self):
            self.calls = 0

        def chat(self, messages):
            self.calls += 1
            assert estimate_tokens(messages[-1]["content"]) <= int(self.context * 0.6)
            return "膨" * 3000, {}

    c = ExpandingClient()
    with pytest.raises(LLMError) as error:
        process(c, "整理：{text}", "源" * 4000)
    assert error.value.code == "LLM_TOO_LONG"
    assert "合并" in str(error.value)
    assert c.calls < 30


def test_slowly_shrinking_merge_stops_at_three_layers():
    import re

    class SlowlyShrinkingClient:
        context = 8000

        def __init__(self):
            self.merge_calls = []

        def chat(self, messages):
            prompt = messages[-1]["content"]
            assert estimate_tokens(prompt) <= int(self.context * 0.6)
            if "<parts>" not in prompt:
                return "详" * 4000, {}
            self.merge_calls.append(prompt)
            payload = "".join(re.findall(r"<片段>\n(.*?)\n</片段>", prompt, re.S))
            # Each layer reduces total content by about 11%, enough to continue but
            # insufficient to fit the final prompt within three merge layers.
            return "合" * (int(len(payload) * 0.89) - 40), {}

    c = SlowlyShrinkingClient()
    with pytest.raises(LLMError) as error:
        process(c, "整理：{text}", "源" * 7000)
    assert error.value.code == "LLM_TOO_LONG"
    assert "层数上限" in str(error.value)
    assert len(c.merge_calls) == 6


def test_merge_stops_before_ninth_paid_call():
    class VerboseClient:
        context = 4000

        def __init__(self):
            self.merge_calls = 0

        def chat(self, messages):
            prompt = messages[-1]["content"]
            assert estimate_tokens(prompt) <= int(self.context * 0.6)
            if "<parts>" in prompt:
                self.merge_calls += 1
                return "合并结果", {}
            return "详" * 30000, {}

    c = VerboseClient()
    with pytest.raises(LLMError) as error:
        process(c, "整理：{text}", "源" * 4000)
    assert error.value.code == "LLM_TOO_LONG"
    assert "调用上限" in str(error.value)
    assert c.merge_calls == 8


def test_generation_progress_precedes_calls_and_reports_merge():
    events = []

    class ProgressClient(FakeClient):
        def chat(self, messages):
            events.append(("call",))
            return super().chat(messages)

    def progress(stage, current, total):
        events.append((stage, current, total))

    c = ProgressClient(context=4000)
    result = process(c, "整理：{text}", "源" * 4000, progress=progress)
    assert events[:result.chunks * 2] == [event for i in range(1, result.chunks + 1)
                                         for event in (("generate", i, result.chunks), ("call",))]
    assert events[result.chunks * 2:] == [("merge", 0, 0), ("merge", 1, 0), ("call",)]
    events.clear()
    process(c, "整理：{text}", "短文本", progress=progress)
    assert events == [("generate", 1, 1), ("call",)]
