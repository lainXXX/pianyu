"""Registry of speech-recognition services the user has configured.

Config (no secrets) lives in engines.json; keys live in a separate engine_keys.json and are
never returned by list()/get(). The built-in DashScope entry also accepts the key from .env
or DASHSCOPE_API_KEY, so existing setups keep working.
"""
import ipaddress
import json
import os
import socket
import uuid
from pathlib import Path
from vtx.configuration import atomic_write
from typing import Callable, List, Optional
from urllib.parse import urlparse

from vtx.redact import redact

DASHSCOPE_MODELS = ("paraformer-v2", "fun-asr")
API_KEY = [{"name": "key", "label": "API key"}]
KINDS = {
    "dashscope": {"label": "阿里云百炼", "needs_url": False, "verbatim": True, "timestamps": "native",
                  "secrets": API_KEY},
    "openai": {"label": "OpenAI 兼容接口", "needs_url": True, "verbatim": False, "timestamps": "native_or_approx",
               "secrets": API_KEY},
    "tencent": {"label": "腾讯云录音文件识别", "needs_url": False, "verbatim": True, "timestamps": "native",
                "secrets": [{"name": "secret_id", "label": "SecretId"}, {"name": "secret_key", "label": "SecretKey"}]},
}
EDITABLE = ("label", "base_url", "model", "allow_local", "app_id", "region", "timestamps")
LOCAL_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in
                       ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))

from vtx.providers import ASR_PRESETS as PRESETS


class EngineError(Exception):
    def __init__(self, message: str, code: str = "ENGINE_CONFIG"):
        self.code = code
        super().__init__(redact(message))


def _resolve(host: str) -> List[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def validate_base_url(url: str, allow_local: bool, resolve: Callable[[str], List[str]] = _resolve) -> str:
    """Allow private LAN and loopback endpoints only with explicit opt-in.
    Link-local, reserved, unspecified and multicast addresses remain blocked."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise EngineError("接口地址必须是 http:// 或 https:// 开头的完整地址")
    if parsed.username or parsed.password:
        raise EngineError("接口地址里不能包含用户名或密码，请把 key 填在单独的输入框")
    try:
        ipaddress.ip_address(parsed.hostname)
        addresses = [parsed.hostname]          # literal IP: check it as-is, no DNS lookup
    except ValueError:
        try:
            addresses = resolve(parsed.hostname)
        except OSError:
            raise EngineError(f"无法解析接口地址的主机名：{parsed.hostname}")
    for addr in addresses:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        local = ip.is_loopback or any(ip in network for network in LOCAL_NETWORKS)
        if local and allow_local:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast:
            hint = "（可信服务请勾选“允许内网地址”）" if local else ""
            raise EngineError(f"接口地址指向内网或本机地址 {ip}，已拒绝{hint}")
    return url.rstrip("/")


class Registry:
    def __init__(self, config_file: Path, keys_file: Path, env_file: Optional[Path] = None,
                 resolve: Callable[[str], List[str]] = _resolve):
        self.config_file, self.keys_file, self.env_file, self._resolve = config_file, keys_file, env_file, resolve

    # --- storage
    def _load(self) -> dict:
        if self.config_file.exists():
            data = json.loads(self.config_file.read_text(encoding="utf-8"))
        else:
            data = {"services": [{"id": "dashscope", "kind": "dashscope", "label": "阿里云百炼",
                                   "model": "paraformer-v2"}], "last_used": None}
            self._save(data)
        for svc in data["services"]:
            svc.pop("builtin", None)
        return data

    def _save(self, data: dict) -> None:
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.config_file, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))

    def _keys(self) -> dict:
        return json.loads(self.keys_file.read_text(encoding="utf-8")) if self.keys_file.exists() else {}

    def _save_keys(self, keys: dict) -> None:
        self.keys_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.keys_file, json.dumps(keys).encode("utf-8"))

    def key(self, sid: str) -> str:
        k = self._keys().get(sid, "")
        if not k and sid == "dashscope":
            k = os.environ.get("DASHSCOPE_API_KEY", "")
            if not k and self.env_file and self.env_file.exists():
                for line in self.env_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("DASHSCOPE_API_KEY="):
                        k = line.split("=", 1)[1].strip()
        return k

    # --- public view (never contains keys)
    def _public(self, svc: dict) -> dict:
        kind = KINDS[svc["kind"]]
        out = {k: v for k, v in svc.items()}
        out.update(configured=bool(self.key(svc["id"])), kind_label=kind["label"], secret_fields=kind["secrets"],
                   verbatim=kind["verbatim"], builtin=False)
        saved = self.key(svc["id"])
        parts = saved.split(":", 1) if len(kind["secrets"]) > 1 else [saved]
        out["secret_fields"] = [{**f, "configured": bool(parts[i] if i < len(parts) else "")}
                                for i, f in enumerate(kind["secrets"])]
        return out

    def list(self) -> List[dict]:
        return [self._public(s) for s in self._load()["services"]]

    def get(self, sid: str) -> dict:
        svc = next((s for s in self._load()["services"] if s["id"] == sid), None)
        if not svc:
            raise EngineError(f"识别服务不存在：{sid}", "ENGINE_NOT_FOUND")
        return self._public(svc)

    # --- mutations
    def _check(self, svc: dict) -> None:
        if svc.get("kind") not in KINDS:
            raise EngineError(f"未知的服务类型：{svc.get('kind')}")
        if not (svc.get("label") or "").strip():
            raise EngineError("请填写服务名称")
        if not (svc.get("model") or "").strip():
            raise EngineError("请填写模型名")
        if svc["kind"] == "dashscope" and svc["model"] not in DASHSCOPE_MODELS:
            raise EngineError(f"百炼只支持这些模型：{', '.join(DASHSCOPE_MODELS)}")
        if KINDS[svc["kind"]]["needs_url"]:
            svc["base_url"] = validate_base_url(svc.get("base_url", ""), bool(svc.get("allow_local")), self._resolve)

    def add(self, fields: dict, key: str) -> str:
        svc = {k: fields[k] for k in ("kind", *EDITABLE) if k in fields}
        svc["id"] = f"{svc.get('kind', 'x')}-{uuid.uuid4().hex[:8]}"
        self._check(svc)
        if not key:
            raise EngineError("请填写 API key")
        data = self._load()
        data["services"].append(svc)
        self._save(data)
        keys = self._keys()
        keys[svc["id"]] = key
        self._save_keys(keys)
        return svc["id"]

    def update(self, sid: str, fields: dict, key: Optional[str] = None) -> None:
        data = self._load()
        svc = next((s for s in data["services"] if s["id"] == sid), None)
        if not svc:
            raise EngineError(f"识别服务不存在：{sid}", "ENGINE_NOT_FOUND")
        candidate = dict(svc)
        for k in EDITABLE:
            if k in fields:
                candidate[k] = fields[k]
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
        svc = next((s for s in data["services"] if s["id"] == sid), None)
        if not svc:
            raise EngineError(f"识别服务不存在：{sid}", "ENGINE_NOT_FOUND")
        data["services"].remove(svc)
        if data.get("last_used") == sid:
            data["last_used"] = None
        if data.get("default") == sid:
            data["default"] = None
        self._save(data)
        keys = self._keys()
        keys.pop(sid, None)
        self._save_keys(keys)

    # --- multi-part credentials. Stored as one string; a two-part secret is "first:second" (old format).
    def secrets(self, sid: str) -> dict:
        svc = self.get(sid)
        key = self.key(sid)
        names = [f["name"] for f in svc["secret_fields"]]
        if len(names) == 1:
            return {names[0]: key}
        first, _, second = key.partition(":")
        return {names[0]: first, names[1]: second}

    def join_secrets(self, kind: str, values: dict, sid: Optional[str] = None) -> str:
        """Form values -> stored key. Blank fields keep their saved value when editing; "" = nothing to change."""
        if kind not in KINDS:
            raise EngineError(f"未知的服务类型：{kind}")
        fields = KINDS[kind]["secrets"]
        values = {f["name"]: (values.get(f["name"]) or "").strip() for f in fields}
        values = {k: "" if v and set(v) <= {"*", "•"} else v for k, v in values.items()}
        if not any(values.values()):
            return ""
        if sid:
            old = self.secrets(sid)
            values = {k: v or old.get(k, "") for k, v in values.items()}
        for f in fields:
            if not values[f["name"]]:
                raise EngineError(f"请填写 {f['label']}")
            if len(fields) > 1 and ":" in values[f["name"]]:
                raise EngineError(f"{f['label']} 里不应包含冒号")
        return ":".join(values[f["name"]] for f in fields)

    def mark_used(self, sid: str) -> None:
        data = self._load()
        data["last_used"] = sid
        self._save(data)

    def set_default(self, sid: str) -> None:
        svc = next((s for s in self._load()["services"] if s["id"] == sid), None)
        if not svc:
            raise EngineError(f"识别服务不存在：{sid}", "ENGINE_NOT_FOUND")
        if not self.key(sid):
            raise EngineError(f"「{svc['label']}」还没有配置，无法设为默认", "ENGINE_NOT_CONFIGURED")
        data = self._load()
        data["default"] = sid
        self._save(data)

    def default(self) -> Optional[str]:
        data = self._load()
        configured = [s["id"] for s in data["services"] if self.key(s["id"])]
        if data.get("default") in configured:
            return data["default"]
        if data.get("last_used") in configured:
            return data["last_used"]
        return configured[0] if configured else None

    # --- engines
    def build(self, sid: str):
        svc = self.get(sid)
        key = self.key(sid)
        if not key:
            raise EngineError(f"“{svc['label']}”还没有填写 API key", "ASR_NOT_CONFIGURED")
        if svc["kind"] == "dashscope":
            from vtx.asr_cloud import DashScopeEngine

            return DashScopeEngine(model=svc["model"], api_key=key, env_file=None)
        if svc["kind"] == "openai":
            from vtx.asr_openai import OpenAICompatEngine

            # re-check at use time: DNS may have changed since the service was saved
            validate_base_url(svc["base_url"], bool(svc.get("allow_local")), self._resolve)
            return OpenAICompatEngine(sid, svc["base_url"], svc["model"], key, timestamps=svc.get("timestamps", "auto"))
        if svc["kind"] == "tencent":
            from vtx.asr_tencent import TencentEngine

            secret_id, sep, secret_key = key.partition(":")
            if not sep or not secret_id or not secret_key:
                raise EngineError("腾讯云需要 SecretId 和 SecretKey 两项", "ASR_NOT_CONFIGURED")
            return TencentEngine(sid, secret_id, secret_key, engine_model=svc["model"], region=svc.get("region", ""))
        raise EngineError(f"“{svc['kind_label']}”适配器尚未实现", "ENGINE_NOT_IMPLEMENTED")
