"""OpenAI-compatible `/v1/audio/transcriptions` (OpenAI, Groq, SiliconFlow, self-hosted, ...).

Timestamps: ask for `verbose_json` first and use its segments when present. Services that
return only text (or reject verbose_json) are handled by VAD-chunking the audio and sending
each chunk separately; each chunk's VAD span becomes the sentence time ("approx").
Whisper-family models may smooth sentences and drop fillers; this cannot be switched off.
"""
import io
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import soundfile as sf

from vtx.asr import MAX_SPEECH_S, SR, Segment, split_long_spans, strip_tags
from vtx.asr_cloud import CloudASRError

TIMEOUT_S = 600


def _code(status: int, message: str) -> str:
    low = (message or "").lower()
    if status in (401, 403):
        return "ASR_ACCESS_DENIED"
    if status == 429:
        return "ASR_RATE_LIMITED"
    if status == 402 or "insufficient" in low or "quota" in low or "balance" in low:
        return "ASR_BILLING_UNAVAILABLE"
    return "ASR_SERVICE_ERROR"


class _NoSegments(Exception):
    pass


class OpenAICompatEngine:
    def __init__(self, service_id: str, base_url: str, model: str, api_key: str, timestamps: str = "auto",
                 vad: Optional[Callable[[Path], Sequence[Tuple[int, int]]]] = None):
        self.base_url, self.model, self._key = base_url.rstrip("/"), model, api_key
        self.timestamps = timestamps          # auto | approx
        self.name = f"openai:{service_id}/{model}"
        self.timestamps_mode = None           # set after transcribe: native | approx
        self.last_billed_seconds = None       # unknown for these APIs
        self._vad = vad

    def _post(self, url, **kw):
        import httpx

        return httpx.post(url, timeout=TIMEOUT_S, **kw)

    def _request(self, filename: str, payload: bytes, response_format: str) -> dict:
        resp = self._post(f"{self.base_url}/audio/transcriptions",
                          headers={"Authorization": f"Bearer {self._key}"},
                          data={"model": self.model, "response_format": response_format,
                                **({"timestamp_granularities[]": "segment"} if response_format == "verbose_json" else {})},
                          files={"file": (filename, payload, "audio/wav")})
        try:
            body = resp.json()
        except ValueError:
            body = {"error": {"message": resp.text[:200]}}
        if resp.status_code >= 400:
            message = (body.get("error") or {}).get("message", "") if isinstance(body, dict) else ""
            if response_format == "verbose_json" and resp.status_code in (400, 422) and "format" in message.lower():
                raise _NoSegments()
            raise CloudASRError(_code(resp.status_code, message), f"HTTP {resp.status_code} {message}")
        return body

    def _native(self, wav: Path) -> List[Segment]:
        body = self._request(wav.name, wav.read_bytes(), "verbose_json")
        raw = body.get("segments")
        if not raw:
            raise _NoSegments()
        out = []
        for i, s in enumerate(raw):
            text = strip_tags((s.get("text") or "").strip())
            if text:
                out.append(Segment(i, float(s["start"]), float(s["end"]), text))
        return out

    def _approx(self, wav: Path) -> List[Segment]:
        if self._vad is None:
            from vtx.asr import silero_spans as vad
        else:
            vad = self._vad
        read = lambda a, b: sf.read(str(wav), start=a, stop=b, dtype="float32")[0]
        out = []
        for i, (a, b) in enumerate(split_long_spans(list(vad(wav)), int(MAX_SPEECH_S * SR), read)):
            buf = io.BytesIO()
            sf.write(buf, read(a, b), SR, format="WAV", subtype="PCM_16")
            text = strip_tags((self._request(f"chunk{i}.wav", buf.getvalue(), "json").get("text") or "").strip())
            if text:
                out.append(Segment(i, a / SR, b / SR, text))
        return out

    def transcribe(self, wav) -> List[Segment]:
        wav = Path(wav)
        if self.timestamps != "approx":
            try:
                segments = self._native(wav)
                self.timestamps_mode = "native"
                return segments
            except _NoSegments:
                pass
        self.timestamps_mode = "approx"
        return self._approx(wav)
