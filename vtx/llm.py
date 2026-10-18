"""LLM post-processing: service registry, OpenAI-compatible chat client, long-text chunking.

Results are a separate output (design 4.9); transcripts are never modified here.
"""
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from vtx.configuration import atomic_write
from typing import Callable, Dict, List, Optional

from vtx.engines import EngineError, _resolve, validate_base_url
from vtx.prompts import render
from vtx.redact import redact

INPUT_SHARE = 0.6        # of the model context, for the prompt + text of one call
DEFAULT_CONTEXT = 32000
TIMEOUT_S = 300
MAX_MERGE_LAYERS = 3
MAX_MERGE_CALLS = 8

from vtx.providers import LLM_PRESETS as PRESETS

EDITABLE = ("label", "base_url", "model", "context", "allow_local", "key_from")


class LLMError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"[{code}] {redact(message)}")


# ---------------------------------------------------------------- registry

class LLMRegistry:
    def __init__(self, config_file: Path, keys_file: Path,
                 fallback_keys: Optional[Dict[str, Callable[[], str]]] = None,
                 resolve: Callable[[str], List[str]] = _resolve):
        self.config_file, self.keys_file = config_file, keys_file
        self.fallback_keys, self._resolve = fallback_keys or {}, resolve

    def _load(self) -> dict:
        return json.loads(self.config_file.read_text(encoding="utf-8")) if self.config_file.exists() \
            else {"services": [], "last_used": None}

    def _save(self, data: dict) -> None:
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.config_file, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))

    def _keys(self) -> dict:
        return json.loads(self.keys_file.read_text(encoding="utf-8")) if self.keys_file.exists() else {}

    def _save_keys(self, keys: dict) -> None:
        self.keys_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.keys_file, json.dumps(keys).encode("utf-8"))

    def _find(self, data: dict, sid: str) -> dict:
        svc = next((s for s in data["services"] if s["id"] == sid), None)
        if not svc:
            raise EngineError(f"大模型服务不存在：{sid}", "ENGINE_NOT_FOUND")
        return svc

    def key(self, sid: str) -> str:
        svc = self._find(self._load(), sid)
        own = self._keys().get(sid, "")
        if own:
            return own
        source = self.fallback_keys.get(svc.get("key_from") or "")
        return source() if source else ""

    def _public(self, svc: dict) -> dict:
        return {**svc, "configured": bool(self.key(svc["id"]))}

    def list(self) -> List[dict]:
        return [self._public(s) for s in self._load()["services"]]

    def get(self, sid: str) -> dict:
        return self._public(self._find(self._load(), sid))

    def _check(self, svc: dict) -> None:
        if not (svc.get("label") or "").strip():
            raise EngineError("请填写服务名称")
        if not (svc.get("model") or "").strip():
            raise EngineError("请填写模型名")
        context = int(svc.get("context") or DEFAULT_CONTEXT)
        if not 2000 <= context <= 10_000_000:
            raise EngineError("上下文长度应在 2000 到 10000000 之间")
        svc["context"] = context
        if svc.get("key_from") and svc["key_from"] not in self.fallback_keys:
            raise EngineError(f"无法复用的 key 来源：{svc['key_from']}")
        svc["base_url"] = validate_base_url(svc.get("base_url", ""), bool(svc.get("allow_local")), self._resolve)

    def add(self, fields: dict, key: str) -> str:
        svc = {k: fields[k] for k in EDITABLE if fields.get(k) not in (None, "")}
        self._check(svc)
        if not key and not svc.get("key_from"):
            raise EngineError("请填写 API key")
        svc["id"] = "llm-" + uuid.uuid4().hex[:8]
        data = self._load()
        data["services"].append(svc)
        self._save(data)
        if key:
            keys = self._keys()
            keys[svc["id"]] = key
            self._save_keys(keys)
        return svc["id"]

    def update(self, sid: str, fields: dict, key: Optional[str] = None) -> None:
        if key and set(key) <= {"*", "•"}:
            key = None
        data = self._load()
        svc = self._find(data, sid)
        candidate = {**svc, **{k: v for k, v in fields.items() if k in EDITABLE}}
        self._check(candidate)
        svc.clear()
        svc.update(candidate)
        self._save(data)
        if key:
            keys = self._keys()
            keys[sid] = key
            self._save_keys(keys)

    def delete(self, sid: str) -> None:
        data = self._load()
        data["services"].remove(self._find(data, sid))
        if data.get("last_used") == sid:
            data["last_used"] = None
        if data.get("default") == sid:
            data["default"] = None
        self._save(data)
        keys = self._keys()
        keys.pop(sid, None)
        self._save_keys(keys)

    def mark_used(self, sid: str) -> None:
        data = self._load()
        data["last_used"] = sid
        self._save(data)

    def set_default(self, sid: str) -> None:
        svc = next((s for s in self._load()["services"] if s["id"] == sid), None)
        if not svc:
            raise EngineError(f"大模型服务不存在：{sid}", "ENGINE_NOT_FOUND")
        if not self.key(sid):
            raise EngineError(f"「{svc['label']}」还没有可用的 API key，无法设为默认", "LLM_NOT_CONFIGURED")
        data = self._load()
        data["default"] = sid
        self._save(data)

    def default(self) -> Optional[str]:
        data = self._load()
        ready = [s["id"] for s in data["services"] if self.key(s["id"])]
        if data.get("default") in ready:
            return data["default"]
        return data["last_used"] if data.get("last_used") in ready else (ready[0] if ready else None)

    def client(self, sid: str) -> "ChatClient":
        svc = self.get(sid)
        key = self.key(sid)
        if not key:
            raise EngineError(f"“{svc['label']}”还没有可用的 API key", "LLM_NOT_CONFIGURED")
        validate_base_url(svc["base_url"], bool(svc.get("allow_local")), self._resolve)
        return ChatClient(svc["base_url"], svc["model"], key, context=svc["context"])

    def preview_client(self, fields: dict, key: Optional[str] = None,
                       sid: Optional[str] = None) -> "ChatClient":
        svc = dict(self._find(self._load(), sid)) if sid else {}
        svc.update({k: v for k, v in fields.items() if k in EDITABLE})
        self._check(svc)
        key = (key or "").strip()
        if key and set(key) <= {"*", "•"}:
            key = ""
        key = key or (self._keys().get(sid, "") if sid else "")
        if not key:
            source = self.fallback_keys.get(svc.get("key_from") or "")
            key = source() if source else ""
        if not key:
            raise EngineError("请填写 API key", "LLM_NOT_CONFIGURED")
        return ChatClient(svc["base_url"], svc["model"], key, context=svc["context"])


# ---------------------------------------------------------------- chat client

def _code(status: int, message: str) -> str:
    low = (message or "").lower()
    if status in (401, 403):
        return "LLM_ACCESS_DENIED"
    if status == 429 and not any(w in low for w in ("balance", "quota", "insufficient")):
        return "LLM_RATE_LIMITED"
    if status == 402 or any(w in low for w in ("insufficient", "balance", "quota", "arrearage")):
        return "LLM_BILLING_UNAVAILABLE"
    if any(w in low for w in ("context length", "context_length", "too long", "maximum context", "max_tokens", "超出")):
        return "LLM_TOO_LONG"
    return "LLM_SERVICE_ERROR"


class ChatClient:
    def __init__(self, base_url: str, model: str, api_key: str, context: int = DEFAULT_CONTEXT,
                 post: Optional[Callable] = None, temperature: float = 0.3):
        self.base_url, self.model, self._key = base_url.rstrip("/"), model, api_key
        self.context, self.temperature = context, temperature
        self._post = post or self._httpx_post

    @staticmethod
    def _httpx_post(url, **kw):
        import httpx

        return httpx.post(url, timeout=TIMEOUT_S, **kw)

    def chat(self, messages: List[dict]):
        import httpx

        try:
            resp = self._post(f"{self.base_url}/chat/completions",
                              headers={"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"},
                              json={"model": self.model, "messages": messages, "temperature": self.temperature,
                                    "stream": False})
        except httpx.HTTPError as exc:
            raise LLMError("LLM_SERVICE_ERROR", f"网络错误：{type(exc).__name__}")
        try:
            body = resp.json()
        except ValueError:
            body = {"error": {"message": getattr(resp, "text", "")[:200]}}
        if resp.status_code >= 400:
            err = body.get("error") if isinstance(body, dict) else None
            message = (err.get("message") if isinstance(err, dict) else str(err or "")) or str(body)[:200]
            raise LLMError(_code(resp.status_code, message), f"HTTP {resp.status_code} {message}")
        choices = body.get("choices") or []
        content = ((choices[0].get("message") or {}).get("content") if choices else None)
        if not content:
            raise LLMError("LLM_SERVICE_ERROR", "模型没有返回内容")
        u = body.get("usage") or {}
        usage = {k: int(u.get(k) or 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
        return content.strip(), usage


# ---------------------------------------------------------------- chunked processing

_CJK = re.compile(r"[　-鿿豈-﫿＀-￯]")


def estimate_tokens(text: str) -> int:
    """Conservative: every CJK char counts as 1 token, other text ~3 chars per token."""
    cjk = len(_CJK.findall(text))
    return cjk + math.ceil((len(text) - cjk) / 3)


def _hard_split(text: str, budget: int) -> List[str]:
    """Split one oversized piece at sentence ends, then by length as a last resort."""
    pieces = re.split(r"(?<=[。！？!?；;\n])", text)
    out, cur = [], ""
    for p in pieces:
        if estimate_tokens(cur + p) <= budget:
            cur += p
            continue
        if cur:
            out.append(cur)
        while estimate_tokens(p) > budget:          # a single sentence longer than the budget
            cut = budget
            while cut > 1 and estimate_tokens(p[:cut]) > budget:
                cut = int(cut * 0.9)
            out.append(p[:cut])
            p = p[cut:]
        cur = p
    if cur:
        out.append(cur)
    return out


def split_text(text: str, budget: int) -> List[str]:
    """Lossless split ("".join(chunks) == text) into chunks of at most `budget` tokens, at paragraph breaks."""
    paras = re.split(r"(?<=\n\n)", text)
    chunks, cur = [], ""
    for p in paras:
        if estimate_tokens(cur + p) <= budget:
            cur += p
            continue
        if cur:
            chunks.append(cur)
            cur = ""
        if estimate_tokens(p) <= budget:
            cur = p
        else:
            parts = _hard_split(p, budget)
            chunks += parts[:-1]
            cur = parts[-1]
    if cur:
        chunks.append(cur)
    return chunks


MERGE_PROMPT = ("# 合并分段结果\n"
                "合并同一视频的 {n} 段结果，保持原要求与输出格式。\n"
                "- 保留独有事实、数据、条件、观点归属及叙述顺序，去掉重复内容。\n"
                "- 去掉重复标题、开场、结语和来源声明，不描述处理过程。\n"
                "- 分段边界不表示来源缺失，不据此补写缺失说明。{compression}\n\n"
                "<instructions>\n{instruction}\n</instructions>\n\n<parts>\n{parts}\n</parts>")
CHUNK_INSTRUCTION = ("\n\n## 当前片段\n"
                     "这是连续视频的一部分。只处理当前内容，整体标题、引言、结语与来源声明留待合并；"
                     "不写分段编号、处理过程或边界缺失说明。")


@dataclass
class LLMResult:
    text: str
    chunks: int
    usage: dict = field(default_factory=dict)


def _add(total: dict, usage: dict) -> None:
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[k] = total.get(k, 0) + usage.get(k, 0)


def _merge_parts(client, parts: List[str], instruction: str, budget: int, usage: dict,
                 progress: Optional[Callable[[str, int, int], None]] = None) -> str:
    def prompt(items, compact=False):
        compression = (f"这是中间合并，合并后控制在约{budget // 3}个token内，"
                       "压缩重复表述和修辞，不丢弃独有事实或把推测改成事实。") if compact else ""
        return MERGE_PROMPT.format(n=len(items), instruction=instruction, compression=compression,
                                   parts="\n\n".join(f"<片段>\n{p}\n</片段>" for p in items))

    calls = 0

    def call(message):
        nonlocal calls
        if calls >= MAX_MERGE_CALLS:
            raise LLMError("LLM_TOO_LONG", "已达分段合并调用上限，请使用更大的上下文")
        calls += 1
        if progress:
            progress("merge", calls, 0)
        out, used = client.chat([{"role": "user", "content": message}])
        _add(usage, used)
        return out

    for _ in range(MAX_MERGE_LAYERS):
        whole = prompt(parts)
        size = estimate_tokens(whole)
        if size <= budget:
            return call(whole)
        # Split every oversized result losslessly, then pack bounded adjacent batches.
        # Reserving half the payload per unit allows each batch to combine multiple units.
        room = budget - estimate_tokens(prompt([""], compact=True)) - 32
        if room < 2:
            raise LLMError("LLM_TOO_LONG", "合并提示词过长，请增大上下文或缩短提示词")
        units = [piece for part in parts for piece in split_text(part, room // 2)]
        batches, batch = [], []
        for unit in units:
            if batch and estimate_tokens(prompt(batch + [unit], compact=True)) > budget:
                batches.append(batch)
                batch = []
            batch.append(unit)
        if batch:
            batches.append(batch)
        reduced = [call(prompt(items, compact=True)) for items in batches]
        next_size = estimate_tokens(prompt(reduced))
        if next_size > budget and next_size >= size * 0.9:
            # A model ignoring the compression request must fail visibly, never dump chunks,
            # loop indefinitely, or discard a tail to manufacture a successful result.
            raise LLMError("LLM_TOO_LONG", "分段合并后内容仍过长，请使用更大的上下文")
        parts = reduced
    raise LLMError("LLM_TOO_LONG", "已达分段合并层数上限，请使用更大的上下文")


def process(client, template: str, text: str, title: str = "",
            progress: Optional[Callable[[str, int, int], None]] = None) -> LLMResult:
    budget = int(client.context * INPUT_SHARE)
    usage: dict = {}
    whole = render(template, text=text, title=title)
    if estimate_tokens(whole) <= budget:
        if progress:
            progress("generate", 1, 1)
        out, u = client.chat([{"role": "user", "content": whole}])
        _add(usage, u)
        return LLMResult(out, 1, usage)

    overhead = estimate_tokens(render(template, text="", title=title) + CHUNK_INSTRUCTION)
    if overhead >= budget // 2:
        raise LLMError("LLM_TOO_LONG", "提示词本身太长，放不下视频文本")
    parts = []
    chunks = split_text(text, budget - overhead)
    for i, chunk in enumerate(chunks, 1):
        if progress:
            progress("generate", i, len(chunks))
        out, u = client.chat([{"role": "user", "content": render(template, text=chunk, title=title) + CHUNK_INSTRUCTION}])
        _add(usage, u)
        parts.append(out)
    instruction = render(template, text="（视频文本）", title=title)
    if progress:
        progress("merge", 0, 0)
    out = _merge_parts(client, parts, instruction, budget, usage, progress)
    return LLMResult(out, len(parts), usage)
