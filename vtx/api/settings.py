"""Settings HTTP interface: service configuration, credentials and diagnostics."""
from pathlib import Path
from typing import Optional

from fastapi import Depends, HTTPException, Request

from vtx.api.models import EngineIn, LLMServiceIn, LLMSettingsIn, SettingsIn
from vtx.asr_cloud import CloudASRError
from vtx.configuration import SettingsStore
from vtx.engines import EngineError
from vtx.llm import LLMError
from vtx.providers import ASR_PRESETS as PRESETS, LLM_PRESETS
from vtx.redact import redact

TEST_CLIP = Path(__file__).resolve().parents[1] / "assets" / "test_clip.wav"


def engine_error(exc):
    status = 404 if exc.code.endswith("_NOT_FOUND") else 422
    return HTTPException(status, str(exc))


def register_settings(app, store, registry, llm_registry, frames, paths, auth, reveal_keys, config_lock):
    work_dir = paths.work_dir
    events = app.state.events

    @app.get("/api/settings", dependencies=[Depends(auth)])
    def get_settings():
        return store.public()

    @app.post("/api/settings", dependencies=[Depends(auth)])
    def put_settings(body: SettingsIn):
        if body.proxy_youtube is not None:
            proxy = body.proxy_youtube.strip()
            if proxy and not proxy.startswith(("http://", "https://", "socks5://")):
                raise HTTPException(422, "代理地址应以 http://、https:// 或 socks5:// 开头")
            store.set_proxy(proxy)
        return store.public()

    @app.post("/api/settings/cookies/{platform}", dependencies=[Depends(auth)])
    async def upload_cookies(platform: str, request: Request):
        if platform not in SettingsStore.PLATFORMS:
            raise HTTPException(404, "未知平台")
        body = await request.body()
        if len(body) > 1 << 20:
            raise HTTPException(413, "文件过大")
        try:
            rows = store.set_cookies(platform, body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(422, str(exc))
        return {"platform": platform, "rows": rows, **store.public()}

    # ---- recognition services (keys are write-only)
    @app.get("/api/engines", dependencies=[Depends(auth)])
    def list_engines():
        return {"services": registry.list(), "default": registry.default(), "presets": PRESETS}

    @app.post("/api/engines", dependencies=[Depends(auth)])
    def add_engine(body: EngineIn):
        try:
            sid = registry.add(body.fields(), key=registry.join_secrets(body.kind or "", body.secret_values()))
        except EngineError as exc:
            raise engine_error(exc)
        return registry.get(sid)

    @app.put("/api/engines/{sid}", dependencies=[Depends(auth)])
    def update_engine(sid: str, body: EngineIn):
        try:
            key = registry.join_secrets(registry.get(sid)["kind"], body.secret_values(), sid=sid)
            registry.update(sid, body.fields(), key=key or None)
        except EngineError as exc:
            raise engine_error(exc)
        return registry.get(sid)

    def need_reveal():
        if not reveal_keys:
            raise HTTPException(403, "服务对外开放时不能查看已保存的 key，只能重新填写")

    @app.get("/api/engines/{sid}/secrets", dependencies=[Depends(auth)])
    def engine_secrets(sid: str):
        need_reveal()
        try:
            return registry.secrets(sid)
        except EngineError as exc:
            raise engine_error(exc)

    @app.get("/api/llm/services/{sid}/secrets", dependencies=[Depends(auth)])
    def llm_secrets(sid: str):
        need_reveal()
        try:
            llm_registry.get(sid)
            return {"key": llm_registry.key(sid)}
        except EngineError as exc:
            raise engine_error(exc)

    @app.get("/api/frame", dependencies=[Depends(auth)])
    def frame_get():
        return frames.public()

    @app.put("/api/frame", dependencies=[Depends(auth)])
    def frame_put(body: dict):
        try:
            frames.update({k: str(v) for k, v in body.items()})
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return frames.public()

    @app.post("/api/frame/{name}/reset", dependencies=[Depends(auth)])
    def frame_reset(name: str):
        frames.reset(name)
        return frames.public()

    @app.get("/api/capabilities", dependencies=[Depends(auth)])
    def capabilities():
        return {"reveal_keys": reveal_keys, "browser_login": reveal_keys, "diagnostics": reveal_keys}

    @app.get("/api/diagnostics", dependencies=[Depends(auth)])
    def diagnostics():
        need_reveal()
        return {"paths": {"config": str(paths.config_dir.resolve()), "work": str(work_dir.resolve()),
                          "logs": str(paths.logs.resolve())}, "log_files": ["app.jsonl", "errors.jsonl"]}

    @app.delete("/api/engines/{sid}", dependencies=[Depends(auth)])
    def delete_engine(sid: str):
        try:
            registry.delete(sid)
        except EngineError as exc:
            raise engine_error(exc)
        return {"ok": True}

    @app.post("/api/engines/{sid}/test", dependencies=[Depends(auth)])
    def test_engine(sid: str):
        """Run the short built-in clip through the service; reports success or a categorized error."""
        try:
            segments = registry.build(sid).transcribe(TEST_CLIP)
        except EngineError as exc:
            events.failure("asr.connection_failed", exc, service_id=sid)
            return {"ok": False, "code": exc.code, "message": str(exc)}
        except CloudASRError as exc:
            events.failure("asr.connection_failed", exc, service_id=sid)
            return {"ok": False, "code": exc.code, "message": str(exc)}
        except Exception as exc:
            events.failure("asr.connection_failed", exc, service_id=sid)
            return {"ok": False, "code": "ASR_SERVICE_ERROR", "message": redact(f"{type(exc).__name__}: {exc}")}
        text = "".join(s.text for s in segments)
        return {"ok": bool(text), "text": text[:100],
                "message": "识别成功" if text else "服务有响应，但没有识别出文字"}

    # ---- LLM services (keys are write-only)
    @app.get("/api/llm/services", dependencies=[Depends(auth)])
    def llm_list():
        return {"services": llm_registry.list(), "default": llm_registry.default(), "presets": LLM_PRESETS}

    @app.get("/api/llm/settings", dependencies=[Depends(auth)])
    def llm_settings_get():
        return {"system_prompt": store.system_prompt()}

    @app.put("/api/llm/settings", dependencies=[Depends(auth)])
    def llm_settings_put(body: LLMSettingsIn):
        with config_lock:
            store.set_system_prompt(body.system_prompt)
        return llm_settings_get()

    @app.post("/api/llm/services/test", dependencies=[Depends(auth)])
    def llm_preview_test(body: LLMServiceIn, sid: Optional[str] = None):
        client = None
        try:
            client = llm_registry.preview_client(body.fields(), key=body.key, sid=sid)
            text, _ = client.chat([{"role": "user", "content": "仅回复 OK"}])
            return {"ok": bool(text), "message": "已连接" if text else "响应为空"}
        except Exception as exc:
            events.failure("llm.connection_failed", exc, service_id=sid)
            message = str(exc)
            for secret in (body.key, client._key if client else None):
                if secret:
                    message = message.replace(secret, "[credential removed]")
            return {"ok": False, "code": getattr(exc, "code", "LLM_SERVICE_ERROR"), "message": redact(message)}

    @app.post("/api/llm/services/{sid}/test", dependencies=[Depends(auth)])
    def llm_test(sid: str):
        try:
            text, _ = llm_registry.client(sid).chat([{"role": "user", "content": "仅回复 OK"}])
            return {"ok": bool(text), "message": "已连接" if text else "响应为空"}
        except (EngineError, LLMError) as exc:
            events.failure("llm.connection_failed", exc, service_id=sid)
            return {"ok": False, "code": exc.code, "message": str(exc)}
        except Exception as exc:
            events.failure("llm.connection_failed", exc, service_id=sid)
            return {"ok": False, "code": "LLM_SERVICE_ERROR", "message": redact(f"{type(exc).__name__}: {exc}")}

    @app.post("/api/llm/services", dependencies=[Depends(auth)])
    def llm_add(body: LLMServiceIn):
        try:
            sid = llm_registry.add(body.fields(), key=(body.key or "").strip())
        except EngineError as exc:
            raise engine_error(exc)
        return llm_registry.get(sid)

    @app.put("/api/llm/services/{sid}", dependencies=[Depends(auth)])
    def llm_update(sid: str, body: LLMServiceIn):
        try:
            llm_registry.update(sid, body.fields(), key=(body.key or "").strip() or None)
        except EngineError as exc:
            raise engine_error(exc)
        return llm_registry.get(sid)

    @app.delete("/api/llm/services/{sid}", dependencies=[Depends(auth)])
    def llm_delete(sid: str):
        try:
            llm_registry.delete(sid)
        except EngineError as exc:
            raise engine_error(exc)
        return {"ok": True}

    @app.post("/api/engines/{sid}/set-default", dependencies=[Depends(auth)])
    def set_engine_default(sid: str):
        try:
            registry.set_default(sid)
        except EngineError as exc:
            raise engine_error(exc)
        return {"ok": True, "default": registry.default()}

    @app.post("/api/llm/services/{sid}/set-default", dependencies=[Depends(auth)])
    def set_llm_default(sid: str):
        try:
            llm_registry.set_default(sid)
        except EngineError as exc:
            raise engine_error(exc)
        return {"ok": True, "default": llm_registry.default()}
