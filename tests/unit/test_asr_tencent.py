"""T20 acceptance 1 (Tencent part): signing, verbatim params, chunking, timeline, error codes."""
import base64
import hashlib
import hmac
import json

import numpy as np
import pytest
import soundfile as sf

from vtx.asr import SR
from vtx.asr_cloud import CloudASRError
from vtx.asr_tencent import MAX_CHUNK_S, TencentEngine, group_spans, tc3_authorization


def test_tc3_signature_follows_documented_chain():
    """Rebuild the signature step by step (canonical request -> string to sign -> derived key)."""
    payload, ts = '{"TaskId":1}', 1700000000          # 2023-11-14 UTC
    auth = tc3_authorization("AKIDexample", "secretKEY", payload, ts)
    canonical = ("POST\n/\n\ncontent-type:application/json; charset=utf-8\nhost:asr.tencentcloudapi.com\n\n"
                 "content-type;host\n" + hashlib.sha256(payload.encode()).hexdigest())
    to_sign = ("TC3-HMAC-SHA256\n1700000000\n2023-11-14/asr/tc3_request\n"
               + hashlib.sha256(canonical.encode()).hexdigest())
    k = hmac.new(("TC3secretKEY").encode(), b"2023-11-14", hashlib.sha256).digest()
    k = hmac.new(k, b"asr", hashlib.sha256).digest()
    k = hmac.new(k, b"tc3_request", hashlib.sha256).digest()
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    assert auth == ("TC3-HMAC-SHA256 Credential=AKIDexample/2023-11-14/asr/tc3_request, "
                    f"SignedHeaders=content-type;host, Signature={sig}")


def test_group_spans_cuts_only_in_silence():
    limit = MAX_CHUNK_S * SR
    spans = [(0, 60 * SR), (61 * SR, 120 * SR), (121 * SR, 200 * SR), (300 * SR, 310 * SR)]
    chunks = group_spans(spans, limit)
    assert chunks == [(0, 120 * SR), (121 * SR, 200 * SR), (300 * SR, 310 * SR)]
    assert all(b - a <= limit for a, b in chunks)


class Resp:
    def __init__(self, body):
        self.status_code, self._body = 200, body

    def json(self):
        return self._body


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "a.wav"
    sf.write(str(path), np.zeros(SR * 300, dtype="float32"), SR)
    return path


def make_engine(post, spans):
    e = TencentEngine("svc", "AKIDexample", "secretKEY", vad=lambda p: spans, clock=lambda: 1700000000)
    e._post = post
    return e


def test_verbatim_params_chunks_and_timeline(wav, monkeypatch):
    monkeypatch.setattr("vtx.asr_tencent.POLL_S", 0)
    created, tasks = [], iter(range(100, 200))
    results = {}

    def post(payload, headers):
        body = json.loads(payload)
        assert headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=AKIDexample/")
        assert "secretKEY" not in json.dumps(headers)
        if headers["X-TC-Action"] == "CreateRecTask":
            created.append(body)
            tid = next(tasks)
            n = len(created)
            results[tid] = [{"FinalSentence": f"第{n}段。", "StartMs": 500, "EndMs": 2500},
                            {"FinalSentence": "", "StartMs": 2600, "EndMs": 2700}]
            return Resp({"Response": {"Data": {"TaskId": tid}}})
        return Resp({"Response": {"Data": {"StatusStr": "success", "ResultDetail": results[body["TaskId"]],
                                           "AudioDuration": 10.4}}})

    spans = [(10 * SR, 100 * SR), (150 * SR, 280 * SR)]     # 2 chunks: 90 s + 130 s
    segs = make_engine(post, spans).transcribe(wav)
    assert len(created) == 2
    for body in created:
        assert body["FilterModal"] == 0 and body["FilterDirty"] == 0 and body["FilterPunc"] == 0
        assert body["ConvertNumMode"] == 0 and body["ResTextFormat"] == 2 and body["SourceType"] == 1
        assert body["ChannelNum"] == 1 and body["EngineModelType"] == "16k_zh"
        raw = base64.b64decode(body["Data"])
        assert len(raw) == body["DataLen"] and len(raw) <= 5 * 1024 * 1024
    assert [(s.index, s.start, s.end, s.text) for s in segs] == [(0, 10.5, 12.5, "第1段。"), (2, 150.5, 152.5, "第2段。")]


@pytest.mark.parametrize("code,expected", [
    ("AuthFailure.SignatureFailure", "ASR_ACCESS_DENIED"),
    ("RequestLimitExceeded", "ASR_RATE_LIMITED"),
    ("FailedOperation.UserHasNoFreeAmount", "ASR_BILLING_UNAVAILABLE"),
    ("InternalError", "ASR_SERVICE_ERROR"),
])
def test_api_errors(wav, code, expected):
    post = lambda payload, headers: Resp({"Response": {"Error": {"Code": code, "Message": "x secretKEY"}}})
    with pytest.raises(CloudASRError) as info:
        make_engine(post, [(0, SR)]).transcribe(wav)
    assert info.value.code == expected


def test_task_failure(wav, monkeypatch):
    monkeypatch.setattr("vtx.asr_tencent.POLL_S", 0)

    def post(payload, headers):
        if headers["X-TC-Action"] == "CreateRecTask":
            return Resp({"Response": {"Data": {"TaskId": 1}}})
        return Resp({"Response": {"Data": {"StatusStr": "failed", "ErrorMsg": "音频解码失败"}}})

    with pytest.raises(CloudASRError) as info:
        make_engine(post, [(0, SR)]).transcribe(wav)
    assert "音频解码失败" in str(info.value)
