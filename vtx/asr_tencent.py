"""Tencent Cloud ASR 录音文件识别 (CreateRecTask + DescribeTaskStatus, API 2019-06-14).

Parameters checked against the official SDK docstrings (tencentcloud-sdk-python-asr 3.1.177):
- SourceType=1 sends audio in the body as base64; "音频数据要小于5MB（含）". A 16 kHz mono
  16-bit WAV is 32 KB/s, so audio is split at VAD silences into chunks well under that limit
  and the per-chunk sentence times are shifted back onto the video timeline.
- FilterDirty / FilterModal / FilterPunc default 0; ConvertNumMode defaults to 1 and is set to
  0 explicitly (verbatim Chinese numerals, design 4.8).
- ResTextFormat=2: sentence list (ResultDetail) with StartMs/EndMs and punctuation.
Signing: TC3-HMAC-SHA256 as documented by Tencent Cloud API 3.0.
"""
import base64
import hashlib
import hmac
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import soundfile as sf

from vtx.asr import SR, Segment, split_long_spans
from vtx.asr_cloud import CloudASRError

HOST = "asr.tencentcloudapi.com"
SERVICE = "asr"
VERSION = "2019-06-14"
MAX_CHUNK_S = 140            # 140 s * 32 KB/s = 4.5 MB < 5 MB
POLL_S = 2
TASK_BUDGET_S = 1800


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def tc3_authorization(secret_id: str, secret_key: str, payload: str, timestamp: int,
                      content_type: str = "application/json; charset=utf-8") -> str:
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    canonical = "\n".join(["POST", "/", "", f"content-type:{content_type}\nhost:{HOST}\n", "content-type;host",
                           hashlib.sha256(payload.encode("utf-8")).hexdigest()])
    scope = f"{date}/{SERVICE}/tc3_request"
    to_sign = "\n".join(["TC3-HMAC-SHA256", str(timestamp), scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    k = _hmac(_hmac(_hmac(("TC3" + secret_key).encode("utf-8"), date), SERVICE), "tc3_request")
    signature = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, SignedHeaders=content-type;host, Signature={signature}"


def classify(code: str) -> str:
    code = code or ""
    if code.startswith("AuthFailure") or code.startswith("UnauthorizedOperation"):
        return "ASR_ACCESS_DENIED"
    if code.startswith("RequestLimitExceeded") or "LimitExceeded" in code:
        return "ASR_RATE_LIMITED"
    if any(w in code for w in ("Arrears", "NoFreeAmount", "NoAmount", "Balance", "ResourceUnavailable")):
        return "ASR_BILLING_UNAVAILABLE"
    return "ASR_SERVICE_ERROR"


def group_spans(spans: Sequence[Tuple[int, int]], max_samples: int) -> List[Tuple[int, int]]:
    """Merge consecutive speech spans into chunks; cut only between spans (in silence)."""
    chunks = []
    for a, b in spans:
        if chunks and b - chunks[-1][0] <= max_samples:
            chunks[-1] = (chunks[-1][0], b)
        else:
            chunks.append((a, b))
    return chunks


class TencentEngine:
    def __init__(self, service_id: str, secret_id: str, secret_key: str, engine_model: str = "16k_zh",
                 region: str = "", vad: Optional[Callable] = None, clock: Callable[[], float] = time.time):
        self._id, self._key = secret_id, secret_key
        self.engine_model, self.region = engine_model, region
        self.name = f"tencent:{service_id}/{engine_model}"
        self.last_billed_seconds = 0
        self.timestamps_mode = "native"
        self._vad, self._clock = vad, clock

    def _post(self, payload: str, headers: dict):
        import httpx

        return httpx.post(f"https://{HOST}/", content=payload.encode("utf-8"), headers=headers, timeout=60)

    def _call(self, action: str, params: dict) -> dict:
        payload = json.dumps(params, separators=(",", ":"))
        ts = int(self._clock())
        headers = {"Content-Type": "application/json; charset=utf-8", "Host": HOST, "X-TC-Action": action,
                   "X-TC-Version": VERSION, "X-TC-Timestamp": str(ts),
                   "Authorization": tc3_authorization(self._id, self._key, payload, ts)}
        if self.region:
            headers["X-TC-Region"] = self.region
        resp = self._post(payload, headers)
        try:
            body = resp.json().get("Response", {})
        except ValueError:
            raise CloudASRError("ASR_SERVICE_ERROR", f"HTTP {resp.status_code} 非 JSON 响应")
        if "Error" in body:
            err = body["Error"]
            raise CloudASRError(classify(err.get("Code", "")), f"{err.get('Code')} {err.get('Message')}")
        return body

    def _recognize_chunk(self, wav_bytes: bytes) -> Tuple[List[dict], float]:
        data = self._call("CreateRecTask", {
            "EngineModelType": self.engine_model, "ChannelNum": 1, "ResTextFormat": 2, "SourceType": 1,
            "Data": base64.b64encode(wav_bytes).decode("ascii"), "DataLen": len(wav_bytes),
            "FilterDirty": 0, "FilterModal": 0, "FilterPunc": 0, "ConvertNumMode": 0,
        })["Data"]
        task_id = data["TaskId"]
        deadline = time.monotonic() + TASK_BUDGET_S
        while time.monotonic() < deadline:
            status = self._call("DescribeTaskStatus", {"TaskId": task_id})["Data"]
            if status["StatusStr"] == "success":
                return status.get("ResultDetail") or [], float(status.get("AudioDuration") or 0)
            if status["StatusStr"] == "failed":
                raise CloudASRError("ASR_SERVICE_ERROR", f"任务失败：{status.get('ErrorMsg')}")
            time.sleep(POLL_S)
        raise CloudASRError("ASR_SERVICE_ERROR", f"任务 {TASK_BUDGET_S}s 内未完成")

    def transcribe(self, wav) -> List[Segment]:
        wav = Path(wav)
        if self._vad is None:
            from vtx.asr import silero_spans as vad
        else:
            vad = self._vad
        read = lambda a, b: sf.read(str(wav), start=a, stop=b, dtype="float32")[0]
        limit = MAX_CHUNK_S * SR
        spans = split_long_spans(list(vad(wav)), limit, read)
        segments, index, billed = [], 0, 0.0
        for a, b in group_spans(spans, limit):
            buf = io.BytesIO()
            sf.write(buf, read(a, b), SR, format="WAV", subtype="PCM_16")
            sentences, seconds = self._recognize_chunk(buf.getvalue())
            billed += seconds
            for s in sentences:
                text = (s.get("FinalSentence") or "").strip()
                if text:
                    segments.append(Segment(index, a / SR + s["StartMs"] / 1000, a / SR + s["EndMs"] / 1000, text))
                index += 1
        self.last_billed_seconds = round(billed)
        return segments
