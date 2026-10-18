"""T11b acceptance 1-2: DashScope engine with mocked HTTP."""
import pytest

from vtx.asr_cloud import CloudASRError, DashScopeEngine, build_request, classify, parse_transcription


def test_request_keeps_verbatim_and_timestamps():
    body = build_request("paraformer-v2", "oss://dir/a.wav")
    params = body["parameters"]
    assert params["disfluency_removal_enabled"] is False
    assert params["timestamp_alignment_enabled"] is True
    assert body["input"]["file_urls"] == ["oss://dir/a.wav"]


def test_parse_keeps_order_and_ms_to_seconds():
    tr = {"transcripts": [{"sentences": [
        {"begin_time": 9920, "end_time": 13800, "text": "每次服用1.5毫升，",
         "words": [{"begin_time": 9920, "end_time": 10100, "text": "每"}]},
        {"begin_time": 14000, "end_time": 15000, "text": ""},
        {"begin_time": 20000, "end_time": 21500, "text": "不要超过三次。", "words": []},
    ]}]}
    segs = parse_transcription(tr)
    assert [(s.index, s.start, s.end) for s in segs] == [(0, 9.92, 13.8), (2, 20.0, 21.5)]
    assert segs[0].token_times == [9.92]


@pytest.mark.parametrize("http,code,expected", [
    (429, "Throttling.RateQuota", "ASR_RATE_LIMITED"),
    (401, "InvalidApiKey", "ASR_ACCESS_DENIED"),
    (403, "AccessDenied", "ASR_ACCESS_DENIED"),
    (400, "Arrearage", "ASR_BILLING_UNAVAILABLE"),
    (200, "SUBMIT_UNKNOWN", "ASR_SUBMIT_UNKNOWN"),
    (500, "InternalError", "ASR_SERVICE_ERROR"),
])
def test_error_codes(http, code, expected):
    assert classify(http, code) == expected


def test_submit_unknown_is_not_retried(monkeypatch):
    calls = []
    engine = DashScopeEngine(api_key="sk-test")

    def boom(*a, **k):
        calls.append(1)
        raise CloudASRError("ASR_SUBMIT_UNKNOWN", "timeout after send")

    monkeypatch.setattr(engine, "_submit", boom)
    monkeypatch.setattr(engine, "_upload", lambda wav: "oss://x")
    with pytest.raises(CloudASRError) as info:
        engine.transcribe("a.wav")
    assert info.value.code == "ASR_SUBMIT_UNKNOWN" and len(calls) == 1


@pytest.mark.parametrize("task", [
    {"output": {"task_status": "FAILED", "code": "SUCCESS_WITH_NO_VALID_FRAGMENT"}, "usage": {"duration": 0}},
    {"output": {"task_status": "FAILED", "results": [
        {"subtask_status": "FAILED", "code": "SUCCESS_WITH_NO_VALID_FRAGMENT"}]}},
])
def test_no_speech_returns_empty_not_error(monkeypatch, task):
    engine = DashScopeEngine(api_key="sk-test")
    monkeypatch.setattr(engine, "_upload", lambda wav: "oss://x")
    monkeypatch.setattr(engine, "_submit", lambda url: "task-1")
    monkeypatch.setattr(engine, "_check", lambda resp: task)
    monkeypatch.setattr(engine, "_http", lambda: type("H", (), {"get": staticmethod(lambda *a, **k: None)}))
    assert engine.transcribe("a.wav") == []


def test_real_failure_still_raises(monkeypatch):
    engine = DashScopeEngine(api_key="sk-test")
    task = {"output": {"task_status": "FAILED", "code": "InvalidFile.DecodeFailed", "message": "bad"}}
    monkeypatch.setattr(engine, "_upload", lambda wav: "oss://x")
    monkeypatch.setattr(engine, "_submit", lambda url: "task-1")
    monkeypatch.setattr(engine, "_check", lambda resp: task)
    monkeypatch.setattr(engine, "_http", lambda: type("H", (), {"get": staticmethod(lambda *a, **k: None)}))
    with pytest.raises(CloudASRError):
        engine.transcribe("a.wav")


def test_missing_key_is_explicit(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(CloudASRError) as info:
        DashScopeEngine(api_key=None, env_file=None)
    assert info.value.code == "ASR_NOT_CONFIGURED"
    assert "DASHSCOPE_API_KEY" in str(info.value)


def test_error_message_never_contains_key():
    err = CloudASRError("ASR_ACCESS_DENIED", "bad key sk-3d79abcdef1234567890 rejected")
    assert "sk-3d79abcdef" not in str(err)
