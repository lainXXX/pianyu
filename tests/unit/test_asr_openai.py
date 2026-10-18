"""T19 acceptance 1-2: OpenAI-compatible transcription adapter (HTTP stubbed)."""
import numpy as np
import pytest
import soundfile as sf

from vtx.asr import SR
from vtx.asr_cloud import CloudASRError
from vtx.asr_openai import OpenAICompatEngine


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = str(body)

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "a.wav"
    sf.write(str(path), np.zeros(SR * 40, dtype="float32"), SR)
    return path


def engine(**kw):
    return OpenAICompatEngine("svc1", "https://api.example.com/v1", "whisper-1", "sk-secret-key-123456", **kw)


def test_native_segments_mapped_to_timeline(wav, monkeypatch):
    calls = []

    def post(url, **kw):
        calls.append((url, kw))
        return Resp(200, {"text": "你好。再见。", "segments": [
            {"start": 1.0, "end": 2.5, "text": "你好。"}, {"start": 3.0, "end": 4.0, "text": " "},
            {"start": 5.0, "end": 6.0, "text": "再见。"}]})

    e = engine()
    monkeypatch.setattr(e, "_post", post)
    segs = e.transcribe(wav)
    assert [(s.index, s.start, s.end, s.text) for s in segs] == [(0, 1.0, 2.5, "你好。"), (2, 5.0, 6.0, "再见。")]
    assert e.timestamps_mode == "native"
    url, kw = calls[0]
    assert url == "https://api.example.com/v1/audio/transcriptions"
    assert kw["data"]["response_format"] == "verbose_json" and kw["data"]["model"] == "whisper-1"
    assert kw["headers"]["Authorization"] == "Bearer sk-secret-key-123456"


def test_falls_back_to_vad_chunks_when_no_segments(wav, monkeypatch):
    spans = [(SR * 2, SR * 5), (SR * 20, SR * 23)]
    texts = iter(["第一段。", "第二段。"])
    seen = []

    def post(url, **kw):
        seen.append(kw["data"]["response_format"])
        if kw["data"]["response_format"] == "verbose_json":
            return Resp(200, {"text": "整段文字"})          # no segments -> fall back
        return Resp(200, {"text": next(texts)})

    e = engine(vad=lambda path: spans)
    monkeypatch.setattr(e, "_post", post)
    segs = e.transcribe(wav)
    assert e.timestamps_mode == "approx"
    assert [(s.start, s.end, s.text) for s in segs] == [(2.0, 5.0, "第一段。"), (20.0, 23.0, "第二段。")]
    assert seen[0] == "verbose_json" and seen[1:] == ["json", "json"]


def test_forced_approx_skips_verbose(wav, monkeypatch):
    seen = []

    def post(url, **kw):
        seen.append(kw["data"]["response_format"])
        return Resp(200, {"text": "x。"})

    e = engine(timestamps="approx", vad=lambda path: [(0, SR)])
    monkeypatch.setattr(e, "_post", post)
    e.transcribe(wav)
    assert seen == ["json"]


def test_verbose_rejected_by_server_falls_back(wav, monkeypatch):
    def post(url, **kw):
        if kw["data"]["response_format"] == "verbose_json":
            return Resp(400, {"error": {"message": "response_format verbose_json is not supported"}})
        return Resp(200, {"text": "好。"})

    e = engine(vad=lambda path: [(0, SR)])
    monkeypatch.setattr(e, "_post", post)
    assert [s.text for s in e.transcribe(wav)] == ["好。"] and e.timestamps_mode == "approx"


@pytest.mark.parametrize("status,code", [(401, "ASR_ACCESS_DENIED"), (403, "ASR_ACCESS_DENIED"),
                                         (429, "ASR_RATE_LIMITED"), (500, "ASR_SERVICE_ERROR"),
                                         (402, "ASR_BILLING_UNAVAILABLE")])
def test_error_codes_and_no_key_leak(wav, monkeypatch, status, code):
    e = engine()
    monkeypatch.setattr(e, "_post", lambda url, **kw: Resp(status, {"error": {"message": "bad sk-secret-key-123456"}}))
    with pytest.raises(CloudASRError) as info:
        e.transcribe(wav)
    assert info.value.code == code and "sk-secret-key-123456" not in str(info.value)


def test_name_identifies_service_and_model():
    assert engine().name == "openai:svc1/whisper-1"
