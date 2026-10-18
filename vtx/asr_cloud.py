"""Cloud ASR: Alibaba DashScope file transcription (paraformer-v2 by default).

Same contract as vtx.asr.LocalEngine: transcribe(wav) -> list[Segment] on the video timeline.
The provider's disfluency removal stays OFF; filler cleanup is done by our own rules.
"""
import os
import time
from pathlib import Path
from typing import List, Optional

from vtx.asr import Segment
from vtx.config import ROOT
from vtx.redact import redact

API = "https://dashscope.aliyuncs.com/api/v1"
POLL_S = 2
TASK_BUDGET_S = 1800
NO_SPEECH = "SUCCESS_WITH_NO_VALID_FRAGMENT"  # provider's "processed fine, nothing spoken"


class _NoSpeech(Exception):
    pass


class CloudASRError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {redact(message)}")


def classify(http_status: int, provider_code: str) -> str:
    code = provider_code or ""
    if code == "SUBMIT_UNKNOWN":
        return "ASR_SUBMIT_UNKNOWN"
    if code in ("Arrearage",) or code.startswith("isv.OUT_OF_SERVICE"):
        return "ASR_BILLING_UNAVAILABLE"
    if http_status == 429 or code.startswith("Throttling"):
        return "ASR_RATE_LIMITED"
    if http_status in (401, 403) or code in ("InvalidApiKey", "AccessDenied"):
        return "ASR_ACCESS_DENIED"
    return "ASR_SERVICE_ERROR"


def build_request(model: str, file_url: str) -> dict:
    return {
        "model": model,
        "input": {"file_urls": [file_url]},
        "parameters": {
            "language_hints": ["zh", "en"],
            "disfluency_removal_enabled": False,
            "timestamp_alignment_enabled": True,
        },
    }


def parse_transcription(tr: dict) -> List[Segment]:
    segments = []
    index = 0
    for transcript in tr.get("transcripts", []):
        for sentence in transcript.get("sentences", []):
            text = (sentence.get("text") or "").strip()
            if text:
                segments.append(Segment(
                    index, sentence["begin_time"] / 1000, sentence["end_time"] / 1000, text,
                    [w["begin_time"] / 1000 for w in sentence.get("words") or []]))
            index += 1  # keep provider order; dropped empties leave gaps
    return segments


def _read_env_key(env_file: Optional[Path]) -> Optional[str]:
    if not env_file or not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("DASHSCOPE_API_KEY="):
            return line.split("=", 1)[1].strip() or None
    return None


class DashScopeEngine:
    def __init__(self, model: str = "paraformer-v2", api_key: Optional[str] = None,
                 env_file: Optional[Path] = ROOT / ".env"):
        key = api_key or os.environ.get("DASHSCOPE_API_KEY") or _read_env_key(env_file)
        if not key:
            raise CloudASRError("ASR_NOT_CONFIGURED", "云端识别未配置：请在设置或 .env 中填写 DASHSCOPE_API_KEY")
        self._headers = {"Authorization": f"Bearer {key}"}
        self.model = model
        self.name = f"dashscope/{model}"
        self.last_billed_seconds = 0

    # --- HTTP steps (each one small so tests can stub it)

    def _http(self):
        import httpx

        return httpx

    def _check(self, resp):
        if resp.status_code >= 400:
            try:
                code = resp.json().get("code", "")
                message = resp.json().get("message", "")
            except ValueError:
                code, message = "", resp.text[:200]
            raise CloudASRError(classify(resp.status_code, code), f"HTTP {resp.status_code} {code} {message}")
        return resp.json()

    def _upload(self, wav: Path) -> str:
        httpx = self._http()
        policy = self._check(httpx.get(f"{API}/uploads", params={"action": "getPolicy", "model": self.model},
                                       headers=self._headers, timeout=20))["data"]
        key = f"{policy['upload_dir']}/{Path(wav).name}"
        resp = httpx.post(policy["upload_host"], data={
            "OSSAccessKeyId": policy["oss_access_key_id"], "Signature": policy["signature"],
            "policy": policy["policy"], "key": key, "x-oss-object-acl": policy["x_oss_object_acl"],
            "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"], "success_action_status": "200",
        }, files={"file": (Path(wav).name, Path(wav).read_bytes())}, timeout=600)
        if resp.status_code != 200:
            raise CloudASRError("ASR_SERVICE_ERROR", f"上传失败 HTTP {resp.status_code}")
        return f"oss://{key}"

    def _submit(self, file_url: str) -> str:
        httpx = self._http()
        try:
            resp = httpx.post(f"{API}/services/audio/asr/transcription", json=build_request(self.model, file_url),
                              headers={**self._headers, "X-DashScope-Async": "enable",
                                       "X-DashScope-OssResourceResolve": "enable"}, timeout=30)
        except httpx.TimeoutException:
            # the request may have been accepted: do not resubmit (would bill twice)
            raise CloudASRError("ASR_SUBMIT_UNKNOWN", "提交后未收到响应，任务状态未知，请勿立即重试")
        return self._check(resp)["output"]["task_id"]

    def _wait(self, task_id: str) -> dict:
        httpx = self._http()
        deadline = time.monotonic() + TASK_BUDGET_S
        while time.monotonic() < deadline:
            data = self._check(httpx.get(f"{API}/tasks/{task_id}", headers=self._headers, timeout=20))
            status = data["output"]["task_status"]
            if status == "SUCCEEDED":
                return data
            if status in ("FAILED", "CANCELED", "UNKNOWN"):
                out = data["output"]
                codes = {out.get("code")} | {r.get("code") for r in out.get("results") or []}
                if NO_SPEECH in codes:
                    self.last_billed_seconds = (data.get("usage") or {}).get("duration", 0)
                    raise _NoSpeech()
                raise CloudASRError(classify(200, out.get("code", "")), f"任务失败 {out.get('code')} {out.get('message')}")
            time.sleep(POLL_S)
        raise CloudASRError("ASR_SERVICE_ERROR", f"任务 {TASK_BUDGET_S}s 内未完成")

    def transcribe(self, wav) -> List[Segment]:
        self.last_billed_seconds = 0
        file_url = self._upload(wav)
        try:
            data = self._wait(self._submit(file_url))
        except _NoSpeech:
            return []
        self.last_billed_seconds = (data.get("usage") or {}).get("duration", 0)
        result = data["output"]["results"][0]
        if result.get("code") == NO_SPEECH:
            return []
        if result.get("subtask_status") != "SUCCEEDED":
            raise CloudASRError(classify(200, result.get("code", "")), f"子任务失败 {result.get('code')} {result.get('message')}")
        tr = self._check(self._http().get(result["transcription_url"], timeout=60))
        return parse_transcription(tr)
