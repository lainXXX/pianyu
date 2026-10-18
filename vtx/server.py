"""Local web service: submit a URL, poll progress, fetch result files.

Default bind is 127.0.0.1. Binding anywhere else requires an access token (>= 16 chars);
every /api request must then carry `Authorization: Bearer <token>`.
Jobs run one at a time in a background thread so status polls never wait on a job.
"""
import hmac
import ipaddress
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from vtx.api.models import AIRunIn, JobIn, OutputIn, TemplateIn, WorkflowIn
from vtx.api.settings import engine_error as _engine_error

from vtx import history
from vtx.configuration import AppPaths, SettingsStore, atomic_write
from vtx.observability import EventLog, install_request_logging
from vtx.asr_cloud import CloudASRError
from vtx.fetch import FetchError
from vtx.redact import redact
from vtx.target import UnsupportedURL

MIN_TOKEN = 16
RENDER_VERSION = 3
FILE_KINDS = ("reading", "original", "srt", "edits", "issues")


class BindError(RuntimeError):
    pass


def is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def check_bind(host: str, token: str) -> None:
    if not is_loopback(host) and len(token or "") < MIN_TOKEN:
        raise BindError(f"监听 {host} 会暴露到网络，必须设置至少 {MIN_TOKEN} 位的访问令牌（VTX_TOKEN）")


def _error(exc: Exception) -> dict:
    if isinstance(exc, FetchError):
        return {"category": exc.category, "message": str(exc)}
    if isinstance(exc, UnsupportedURL):
        category = "dead" if "不可用" in str(exc) else "parse"
        return {"category": category, "message": redact(str(exc))}
    if isinstance(exc, CloudASRError):
        return {"category": "asr", "code": exc.code, "message": str(exc)}
    from vtx.engines import EngineError

    if isinstance(exc, EngineError):
        return {"category": "asr", "code": exc.code, "message": str(exc)}
    return {"category": "internal", "message": redact(f"{type(exc).__name__}: {exc}")}


def _default_run(store: SettingsStore, registry) -> Callable:
    """Production pipeline wired to the current settings (read fresh for every job)."""
    from dataclasses import replace

    from vtx import config
    from vtx.pipeline import run

    def go(url, out_dir, progress, engine=None):
        sid = engine or registry.default()
        if not sid:
            from vtx.engines import EngineError

            raise EngineError("还没有配置任何识别服务，请在“设置 → 识别服务”里添加", "ASR_NOT_CONFIGURED")
        built = registry.build(sid)
        settings = replace(config.load(), proxy_youtube=store.proxy(), cookies_dir=store.cookies_dir)
        result = run(url, out_dir, engine=built, settings=settings, progress=progress)
        svc = registry.get(sid)
        result.meta.update(service_id=sid, service_label=svc["label"], verbatim=svc["verbatim"],
                           timestamps_mode=getattr(built, "timestamps_mode", "native"))
        return result

    return go


def create_app(work_dir: Path, token: str = "", run: Optional[Callable] = None,
               store: Optional[SettingsStore] = None, parts: Optional[Callable] = None,
               registry=None, llm_registry=None, reveal_keys: bool = False) -> FastAPI:
    """reveal_keys: let the page show saved keys; main() enables it only when bound to loopback."""
    from vtx.config import ROOT
    from vtx.engines import EngineError, Registry

    paths = AppPaths.for_app(work_dir, ROOT)
    if store is None:
        paths.migrate_legacy()
        paths.initialize()
        store = SettingsStore(ROOT / ".env", paths.cookies_dir, paths.app, paths.system_prompt)
    registry = registry or Registry(paths.asr_services, paths.asr_secrets, env_file=store.env_file)
    run = run or _default_run(store, registry)
    from vtx.llm import LLMError, LLMRegistry, process as llm_process
    from vtx.outputs import KINDS as OUTPUT_KINDS, OutputStore
    from vtx.poster_templates import GROUPS as LAYOUT_GROUPS, TemplateStore

    if llm_registry is None:
        llm_registry = LLMRegistry(paths.llm_services, paths.llm_secrets,
                                   fallback_keys={"dashscope": lambda: registry.key("dashscope")})
    from vtx.frame import FrameStore

    frames = FrameStore(paths.frame)
    templates = TemplateStore(paths.templates)
    outputs = OutputStore(paths.outputs, templates, legacy=paths.legacy_prompts)
    ai_pending: "queue.Queue[str]" = queue.Queue()
    jobs, llm_runs = history.load(work_dir / "jobs")    # finished work survives a restart
    if parts is None:
        from vtx.target import http_bili_parts as parts
    app = FastAPI(title="片语", docs_url=None, redoc_url=None, openapi_url=None)
    events = EventLog(paths.logs)
    app.state.events = events
    app.state.paths = paths
    install_request_logging(app, events)
    events.event("app.started", config=str(paths.config_dir), work=str(work_dir), logs=str(paths.logs))
    lock = threading.Lock()
    config_lock = threading.RLock()
    pending: "queue.Queue[str]" = queue.Queue()

    def worker():
        while True:
            job_id = pending.get()
            with lock:
                job = jobs[job_id]
                job["status"] = "running"

            started = time.monotonic()
            events.event("job.started", job_id=job_id, service_id=job.get("engine"))

            def progress(stage, info=None, job=job):
                events.event("job.stage", job_id=job_id, phase=stage)
                with lock:
                    job["stage"] = stage
                    if info:
                        job["info"] = info

            try:
                extra = {"engine": job["engine"]} if job.get("engine") else {}
                result = run(job["url"], work_dir / "jobs" / job_id, progress=progress, **extra)
                if job.get("engine"):
                    registry.mark_used(job["engine"])   # becomes the default selection next time
                with lock:
                    job.update(files={k: str(v) for k, v in result.files.items()},
                               result={"meta": result.meta, "issues": result.issues, "edits": result.edits,
                                       "cached_asr": result.cached_asr})
                if job.get("workflow"):
                    queue_workflow(job_id, job["workflow"], job.get("llm_service") or llm_registry.default())
                with lock:
                    job["status"] = "done"  # publish completion only after every AI node is visible
                    history.save_job(work_dir / "jobs" / job_id, job)
                events.event("job.completed", job_id=job_id, duration_ms=round((time.monotonic()-started)*1000),
                             cached=result.cached_asr)
            except Exception as exc:  # report every failure on the job, never kill the worker
                events.failure("job.failed", exc, job_id=job_id, duration_ms=round((time.monotonic()-started)*1000))
                with lock:
                    job.update(status="failed", error=_error(exc))
                continue

    threading.Thread(target=worker, daemon=True, name="vtx-worker").start()

    def auth(request: Request):
        if not token:
            return
        header = request.headers.get("authorization", "")
        if not hmac.compare_digest(header.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "需要访问令牌")

    def public(job: dict) -> dict:
        out = {k: job[k] for k in ("id", "url", "status", "stage", "info", "result", "error") if k in job}
        out["ai"] = [{k: r.get(k) for k in ("id", "kind", "output", "name", "status", "error", "created_at", "progress")}
                     for r in llm_runs.values() if r["job"] == job["id"]]
        if job.get("status") == "done":      # header / footer for exports, from the editable templates
            out["frame"] = frames.render_all((job.get("result") or {}).get("meta", {}))
        return out

    @app.post("/api/jobs", dependencies=[Depends(auth)])
    def submit(body: JobIn):
        if body.workflow:
            sid = body.llm_service or llm_registry.default()
            if not sid:
                raise HTTPException(422, "勾选了 AI 节点，但还没有配置大模型服务")
            try:
                llm_registry.client(sid)
                for oid in body.workflow:
                    outputs.get(oid)
            except EngineError as exc:
                raise _engine_error(exc)
        if body.engine:
            try:
                svc = registry.get(body.engine)
            except EngineError as exc:
                raise HTTPException(422, str(exc))
            if not svc["configured"]:
                raise HTTPException(422, f"“{svc['label']}”还没有填写 API key")
        job_id = uuid.uuid4().hex[:12]
        with lock:
            jobs[job_id] = {"id": job_id, "url": body.url, "status": "queued", "stage": None, "engine": body.engine,
                            "created_at": time.time(),
                            "workflow": list(body.workflow or []),
                            "llm_service": body.llm_service}
        events.event("job.queued", job_id=job_id, service_id=body.engine)
        pending.put(job_id)
        return public(jobs[job_id])

    @app.get("/api/jobs/{job_id}", dependencies=[Depends(auth)])
    def status(job_id: str):
        with lock:
            job = jobs.get(job_id)
            if not job:
                raise HTTPException(404, "任务不存在")
            return public(job)

    @app.get("/api/jobs/{job_id}/files/{kind}", dependencies=[Depends(auth)])
    def file(job_id: str, kind: str):
        with lock:
            job = jobs.get(job_id)
            path = (job or {}).get("files", {}).get(kind)
        if kind not in FILE_KINDS or not path:
            raise HTTPException(404, "文件不存在")
        media = "application/json" if kind in ("edits", "issues") else "text/plain; charset=utf-8"
        return FileResponse(path, media_type=media, filename=Path(path).name)

    @app.get("/api/parts", dependencies=[Depends(auth)])
    def bili_parts(url: str):
        from vtx.target import resolve

        try:
            target = resolve(url)
            if target.platform != "bilibili" or not target.needs_choice:
                return {"needs_choice": False, "parts": []}
            return {"needs_choice": True, "video_id": target.video_id, "parts": parts(target.video_id)}
        except Exception:
            # let the job itself report the precise reason (dead / parse / network) with its category
            return {"needs_choice": False, "parts": []}

    from vtx.api.settings import register_settings
    from vtx.browser_login import register_browser_login

    register_settings(app, store, registry, llm_registry, frames, paths, auth, reveal_keys, config_lock)
    register_browser_login(app, store, work_dir / "browser-profiles", auth, enabled=reveal_keys)
    from vtx.style_packages import register_style_packages

    register_style_packages(app, outputs, templates, auth, config_lock)
    app.add_event_handler("shutdown", events.close)

    # ---- output templates (design 4.12)
    def _out(fn):
        try:
            with config_lock:
                return fn()
        except EngineError as exc:
            raise _engine_error(exc)

    @app.get("/api/outputs", dependencies=[Depends(auth)])
    def output_list(hidden: bool = False):
        return {"outputs": outputs.list(include_hidden=hidden), "kinds": {k: v["label"] for k, v in OUTPUT_KINDS.items()}}

    @app.post("/api/outputs", dependencies=[Depends(auth)])
    def output_add(body: OutputIn):
        return _out(lambda: outputs.get(outputs.add(body.fields(), copy_of=body.copy_of or "")))

    @app.put("/api/outputs/{oid}", dependencies=[Depends(auth)])
    def output_update(oid: str, body: OutputIn):
        return _out(lambda: (outputs.update(oid, body.fields()), outputs.get(oid))[1])

    @app.post("/api/outputs/{oid}/reset", dependencies=[Depends(auth)])
    def output_reset(oid: str):
        return _out(lambda: (outputs.reset(oid), outputs.get(oid))[1])

    @app.post("/api/outputs/{oid}/hide", dependencies=[Depends(auth)])
    def output_hide(oid: str, hidden: bool = True):
        return _out(lambda: (outputs.hide(oid, hidden), outputs.get(oid))[1])

    @app.delete("/api/outputs/{oid}", dependencies=[Depends(auth)])
    def output_delete(oid: str):
        _out(lambda: outputs.delete(oid))
        return {"ok": True}

    # ---- layouts (points / article)
    @app.get("/api/layouts", dependencies=[Depends(auth)])
    def layout_list(group: str = ""):
        return {"layouts": [layout_public(t) for t in templates.list(group)], "groups": LAYOUT_GROUPS}

    def layout_public(t):
        return {**t, "outputs": outputs.layout_outputs(t["id"])}

    @app.post("/api/layouts", dependencies=[Depends(auth)])
    def layout_add(body: TemplateIn):
        def add():
            with config_lock:
                tid = "t-" + uuid.uuid4().hex[:8]
                group = templates.get(body.copy_of)["group"] if body.copy_of else body.group or "points"
                bindings = outputs.prepare_bindings(tid, group, body.outputs) if body.outputs is not None else None
                templates.add(body.fields(), copy_of=body.copy_of or "", tid=tid)
                if bindings is not None:
                    outputs._save(bindings)
                return layout_public(templates.get(tid))
        return _out(add)

    @app.put("/api/layouts/{tid}", dependencies=[Depends(auth)])
    def layout_update(tid: str, body: TemplateIn):
        def update():
            with config_lock:
                group = templates.get(tid)["group"]
                bindings = outputs.prepare_bindings(tid, group, body.outputs) if body.outputs is not None else None
                templates.update(tid, body.fields())
                if bindings is not None:
                    outputs._save(bindings)
                return layout_public(templates.get(tid))
        return _out(update)

    @app.post("/api/layouts/{tid}/reset", dependencies=[Depends(auth)])
    def layout_reset(tid: str):
        return _out(lambda: (templates.reset(tid), layout_public(templates.get(tid)))[1])

    @app.delete("/api/layouts/{tid}", dependencies=[Depends(auth)])
    def layout_delete(tid: str):
        if outputs.layout_outputs(tid):
            raise HTTPException(422, "请先解除风格绑定")
        _out(lambda: templates.delete(tid))
        return {"ok": True}

    @app.post("/api/layouts/preview", dependencies=[Depends(auth)])
    def layout_preview(body: TemplateIn):
        """Render an (unsaved) layout with sample data, first page only. No model call."""
        import tempfile

        from fastapi.responses import Response

        try:
            base = templates.get(body.copy_of) if body.copy_of else {"group": body.group or "points", "html": "",
                                                                     "width": 1080, "height_mode": "auto"}
            t = {**base, **TemplateStore._clean(body.fields(), base["group"])}
            with tempfile.TemporaryDirectory() as tmp:
                pngs = render_sample(t, Path(tmp))
                return Response(Path(pngs[0]).read_bytes(), media_type="image/png")
        except EngineError as exc:
            raise _engine_error(exc)

    def render_sample(t: dict, out: Path) -> list:
        from vtx.article import markdown_blocks, render_pages
        from vtx.poster import footer_text, render_png
        from vtx.poster_templates import SAMPLE, SAMPLE_ARTICLE, SAMPLE_META

        if t["group"] == "article":
            title, blocks = markdown_blocks(SAMPLE_ARTICLE)
            return render_pages(t, title, "B站 · @示例UP · 3 分 17 秒", blocks, frames.render_all(SAMPLE_META)["footer"],
                                out, "preview")["pngs"]
        png = out / "preview.png"
        render_png(SAMPLE, SAMPLE_META, t, png, footer=footer_text(SAMPLE_META, frames.get()["poster_footer"]))
        return [str(png)]

    # ---- runs: an output template applied to a finished job (design 4.12)
    HEADER_PREFIXES = ("标题：", "作者：", "链接：", "来源：", "口语清理：")

    def job_text(job: dict, source: str) -> str:
        import re

        path = Path(job["files"][source])
        lines = path.read_text(encoding="utf-8").splitlines()
        if source == "original":
            return "".join(re.sub(r"^\[\d\d:\d\d:\d\d\] ", "", l) for l in lines if l.strip())
        while lines and (lines[0].startswith(HEADER_PREFIXES) or not lines[0].strip()):
            lines.pop(0)
        return "\n\n".join(l for l in lines if l.strip())

    def original_lines(job: dict) -> list:
        import re

        path = Path(job["files"]["original"])
        return [{"text": re.sub(r"^\[\d\d:\d\d:\d\d\] ", "", l)}
                for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def reading_lines(job: dict) -> list:
        return [l for l in job_text(job, "reading").split("\n\n") if l.strip()]

    points_cache: dict = {}       # (job_id, service_id, prompt text hash) -> knowledge result
    for r in llm_runs.values():   # restored from history: same prompt + model reuses its points
        if r.get("kind") == "points" and r.get("status") == "done" and r.get("service_id") and r.get("content"):
            points_cache[(r["job"], r["service_id"], r.get("prompt_hash"))] = {
                **r["content"], "_proofread": r.get("proofread"),
                "_chunks": (r.get("stats") or {}).get("chunks"),
                "_quote_stats": (r.get("stats") or {}).get("quotes")}

    def _hash(s: str) -> str:
        import hashlib

        return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

    def new_run(job_id: str, params: dict, sid: str) -> dict:
        """Validate and register a run (status running). params: output, prompt (one-off), source."""
        svc = llm_registry.get(sid)
        o = outputs.get(params["output"])
        rid = uuid.uuid4().hex[:12]
        from vtx.system_prompt import RULES_VERSION, reference_terms

        job = jobs[job_id]
        ref = job.get("files", {}).get("reference")
        if not ref:
            import re

            meta = (job.get("result") or {}).get("meta", {})
            vid, lang = meta.get("video_id", ""), meta.get("second_source") or ""
            if meta.get("platform") == "bilibili" and re.fullmatch(r"BV\w+", vid) and re.fullmatch(r"[\w-]+", lang):
                candidate = work_dir / f"bilibili-{vid}-p{int(meta.get('part') or 1)}" / f"sub.{lang}.srt"
                ref = candidate if candidate.is_file() else None
        reference = Path(ref).read_text(encoding="utf-8") if ref else ""
        terms = reference_terms(reference, job_text(job, "original")) if reference else ""

        rec = {"id": rid, "job": job_id, "output": o["id"], "kind": o["kind"], "name": o["name"],
               "layouts": o["layouts"], "status": "running", "created_at": time.time(),
               "service": svc["label"], "service_id": sid, "model": svc["model"], "context": svc["context"],
               "params": {k: params.get(k) for k in ("output", "prompt", "source")}, "renders": {},
               "system_prompt": store.system_prompt(), "rules_version": RULES_VERSION,
               "reference_terms": terms, "reference_text": reference}
        with lock:
            llm_runs[rid] = rec
        return rec

    def render_layout(rec: dict, lid: str) -> dict:
        """Render one layout of a finished run (no model call). -> {"pngs": [...], "html": path}."""
        from vtx.article import (kicker_text, markdown_blocks, paragraph_blocks, render_pages,
                                 structure_blocks)
        from vtx.poster import footer_text, render_png

        t = templates.get(lid)
        with lock:
            job = dict(jobs[rec["job"]])
        meta = dict((job.get("result") or {}).get("meta", {}))
        out = work_dir / "jobs" / rec["job"] / "ai"
        stem = f"{rec['id']}-{lid}"
        if rec["kind"] == "points":
            if t["group"] != "points":
                raise EngineError("要点类结果只能用要点版式")
            png, html = out / f"{stem}.png", out / f"{stem}.html"
            out.mkdir(parents=True, exist_ok=True)
            render_png(rec["content"], meta, t, png, html, footer=footer_text(meta, frames.get()["poster_footer"]))
            return {"pngs": [str(png)], "html": str(html)}
        if t["group"] != "article":
            raise EngineError("这个结果只能用文章版式")
        title = meta.get("title", "")
        if rec["kind"] == "structure":
            blocks = structure_blocks(rec["content"])
        elif rec["kind"] == "source":
            blocks = paragraph_blocks(rec["content"])
        else:
            md_title, blocks = markdown_blocks(rec["markdown_body"])
            title = md_title or title
        r = render_pages(t, title, kicker_text(meta, rec["name"]), blocks, frames.render_all(meta)["footer"], out, stem)
        return {"pngs": r["pngs"], "html": r["html"]}

    def execute(rec: dict, page_text: Optional[str] = None) -> None:
        """Run one output template to completion, then render its default layout. Never raises."""
        from vtx.knowledge import generate as knowledge_generate, to_markdown
        from vtx.refine import refine as refine_run
        from vtx.structure import structure as structure_run, to_markdown as structure_md
        from vtx.proofread import proofread

        p = rec["params"]
        usage = {}
        started = time.monotonic()
        events.event("ai.started", job_id=rec["job"], run_id=rec["id"], service_id=rec["service_id"],
                     output=p["output"])

        def progress(phase, index, total):
            events.event("ai.stage", job_id=rec["job"], run_id=rec["id"], phase=phase, index=index, total=total)
            with lock:
                rec["progress"] = {"phase": phase, "index": index, "total": total}
        try:
            with lock:
                job = dict(jobs[rec["job"]])
            sid = rec["service_id"]
            from vtx.system_prompt import EditorialClient

            from vtx.system_prompt import usable_transcript

            if not usable_transcript(job_text(job, "original")):
                raise LLMError("LLM_BAD_SOURCE", "识别内容不足，请重新识别")
            raw_client = llm_registry.client(sid)
            o = outputs.get(p["output"])
            prompt = p.get("prompt") if (p.get("prompt") or "").strip() else o["prompt"]
            meta = dict((job.get("result") or {}).get("meta", {}))
            footer = frames.render_all(meta)["footer"]
            title = meta.get("title", "")
            client = EditorialClient(raw_client, rec["system_prompt"], rec.get("reference_terms", ""), title=title)
            extra = {}

            def checked(document):
                progress("proofread", 0, 0)
                result = proofread(EditorialClient(raw_client, rec["system_prompt"], title=title), document,
                                   job_text(job, "original"), rec.get("reference_text", ""), title=title)
                from vtx.llm import _add

                _add(usage, result.usage)
                rec["proofread"] = result.audit
                return result.doc

            if o["kind"] == "points":
                reading = page_text if page_text is not None else job_text(job, "reading")
                key = (rec["job"], sid, _hash(repr((prompt, reading, rec["system_prompt"], rec.get("reference_text", ""), rec["model"], rec.get("context"), rec["rules_version"]))))
                with lock:
                    cached = points_cache.get(key)
                if cached is None:
                    r = knowledge_generate(client, reading, title, template=prompt, progress=progress)
                    usage = r.usage
                    data = checked(r.data)
                    cached = {**data, "_chunks": r.chunks, "_quote_stats": r.quote_stats,
                              "_proofread": rec["proofread"]}
                    with lock:
                        points_cache[key] = cached
                content = {k: v for k, v in cached.items() if not k.startswith("_")}
                rec["proofread"] = cached.get("_proofread")
                extra = {"content": content, "prompt_hash": key[2], "cached": not usage,
                         "stats": {"quotes": cached.get("_quote_stats"), "chunks": cached.get("_chunks")},
                         "markdown": to_markdown(content, footer)}
            elif o["kind"] == "structure":
                r = structure_run(client, reading_lines(job), title, template=prompt)
                usage = r.usage
                extra = {"content": r.doc, "stats": r.stats, "markdown": structure_md(r.doc, title, footer)}
            elif o["kind"] == "source":
                r = refine_run(client, original_lines(job), title, template=prompt)
                usage = r.usage
                extra = {"content": r.paragraphs, "stats": r.stats,
                         "markdown": f"# {title}\n\n{r.text}\n\n---\n{footer}\n"}
            else:
                src = page_text if page_text is not None else job_text(job, p.get("source") or "reading")
                if p.get("source") == "original":
                    src = job_text(job, "original")
                r = llm_process(client, prompt, src, title, progress=progress)
                usage = r.usage
                body = checked({"markdown_body": r.text.strip()})["markdown_body"]
                extra = {"markdown_body": body, "stats": {"chunks": r.chunks},
                         "markdown": (body if body.startswith("# ") else f"# {title}\n\n{body}") + f"\n\n---\n{footer}\n"}
            llm_registry.mark_used(sid)
            with lock:
                rec.update(usage=usage, error=None, **extra)
            default = rec["layouts"][0]
            progress("render", 0, 0)
            try:
                rendered = render_layout(rec, default)
                with lock:
                    rec["renders"] = {default: rendered}
                    rec["render_version"] = RENDER_VERSION
            except Exception as exc:          # the text is still useful when an image fails
                events.failure("ai.render_failed", exc, job_id=rec["job"], run_id=rec["id"])
                with lock:
                    rec["render_error"] = redact(f"{type(exc).__name__}: {exc}")
            with lock:
                rec["status"] = "done"
                history.save_run(work_dir / "jobs" / rec["job"], rec)
            events.event("ai.completed", job_id=rec["job"], run_id=rec["id"],
                         duration_ms=round((time.monotonic()-started)*1000), total_tokens=usage.get("total_tokens"))
        except (LLMError, EngineError) as exc:
            events.failure("ai.failed", exc, job_id=rec["job"], run_id=rec["id"])
            with lock:
                if getattr(exc, "audit", None):
                    from vtx.llm import _add

                    _add(usage, exc.audit.get("usage", {}))
                    rec["proofread"] = exc.audit
                rec.update(status="failed", usage=usage, error={"code": exc.code, "message": str(exc)})
                history.save_run(work_dir / "jobs" / rec["job"], rec)
        except Exception as exc:
            events.failure("ai.failed", exc, job_id=rec["job"], run_id=rec["id"])
            with lock:
                rec.update(status="failed", usage=usage,
                           error={"code": "LLM_SERVICE_ERROR", "message": redact(f"{type(exc).__name__}: {exc}")})
                history.save_run(work_dir / "jobs" / rec["job"], rec)

    def ai_worker():
        """Workflow nodes, one at a time, so they never hold up recognition of the next video."""
        while True:
            rid = ai_pending.get()
            with lock:
                rec = llm_runs.get(rid)
                if rec:
                    rec["status"] = "running"
            if rec:
                execute(rec)

    threading.Thread(target=ai_worker, daemon=True, name="vtx-ai-worker").start()

    def queue_workflow(job_id: str, nodes: list, sid: Optional[str]) -> None:
        """When a job finishes: register every chosen output (shown as queued), then hand them to ai_worker."""
        rids = []
        for oid in nodes:
            try:
                if not sid:
                    raise EngineError("还没有配置大模型服务", "LLM_NOT_CONFIGURED")
                rec = new_run(job_id, {"output": oid}, sid)
            except EngineError as exc:   # e.g. the template was deleted meanwhile: show it as a failed node
                rec = {"id": uuid.uuid4().hex[:12], "job": job_id, "output": oid, "kind": "", "name": oid,
                       "layouts": [], "status": "failed", "created_at": time.time(), "params": {"output": oid},
                       "renders": {}, "error": {"code": exc.code, "message": str(exc)}}
                with lock:
                    llm_runs[rec["id"]] = rec
            rids.append(rec["id"])
        with lock:
            jobs[job_id]["ai_runs"] = rids
            queued = [r for r in rids if llm_runs[r]["status"] == "running"]
            for r in queued:
                llm_runs[r]["status"] = "queued"
        for r in queued:
            ai_pending.put(r)

    @app.post("/api/jobs/{job_id}/ai", dependencies=[Depends(auth)])
    def ai_run(job_id: str, body: AIRunIn):
        with lock:
            job = jobs.get(job_id)
            status = job and job["status"]
        if not job:
            raise HTTPException(404, "任务不存在")
        if status != "done":
            raise HTTPException(409, "任务还没有完成")
        sid = body.service or llm_registry.default()
        if not sid:
            raise HTTPException(422, "还没有配置大模型服务，请在设置页“大模型”里添加")
        try:
            llm_registry.client(sid)
            rec = new_run(job_id, body.model_dump(), sid)
        except EngineError as exc:
            raise _engine_error(exc)
        threading.Thread(target=execute, args=(rec, body.text), daemon=True, name=f"vtx-ai-{rec['id']}").start()
        with lock:
            return dict(rec)

    @app.post("/api/llm/runs/{rid}/render/{lid}", dependencies=[Depends(auth)])
    def ai_render(rid: str, lid: str):
        """Switch a finished run to another layout: render only, no model call, no billing."""
        with lock:
            rec = llm_runs.get(rid)
            if not rec:
                raise HTTPException(404, "运行记录不存在")
            if rec["status"] != "done":
                raise HTTPException(409, "还没有生成完成")
            if lid not in outputs.get(rec["output"])["layouts"]:
                raise HTTPException(422, "版式未绑定当前风格")
            if rec.get("render_version") != RENDER_VERSION:
                rec["renders"] = {}
            done = (rec.get("renders") or {}).get(lid)
        if done is None:
            try:
                done = render_layout(rec, lid)
            except EngineError as exc:
                raise _engine_error(exc)
            with lock:
                rec.setdefault("renders", {})[lid] = done
                rec["render_version"] = RENDER_VERSION
            history.save_run(work_dir / "jobs" / rec["job"], rec)
        return {"layout": lid, "pages": len(done["pngs"])}

    @app.post("/api/llm/runs/{rid}/retry", dependencies=[Depends(auth)])
    def ai_retry(rid: str):
        with lock:
            rec = llm_runs.get(rid)
            if not rec:
                raise HTTPException(404, "运行记录不存在")
            if rec["status"] != "failed":
                raise HTTPException(409, "只有失败的节点可以重试")
            if not rec.get("service_id"):
                raise HTTPException(422, "这个节点无法重试，请在 AI 处理里手动运行")
            rec.update(status="running", error=None, created_at=time.time(), system_prompt=store.system_prompt())
        threading.Thread(target=execute, args=(rec,), daemon=True, name=f"vtx-ai-{rid}").start()
        with lock:
            return dict(rec)

    @app.get("/api/llm/runs/{rid}/files/{lid}/{name}", dependencies=[Depends(auth)])
    def ai_file(rid: str, lid: str, name: str):
        """name: "html", "zip" (all PNGs), or a page number 1..n."""
        with lock:
            r = ((llm_runs.get(rid) or {}).get("renders") or {}).get(lid)
        path = None
        if r and name == "zip":
            from io import BytesIO
            from zipfile import ZipFile

            data = BytesIO()
            with ZipFile(data, "w") as archive:
                for index, png in enumerate(r["pngs"], 1):
                    if not Path(png).is_file():
                        raise HTTPException(404, "图片不存在")
                    archive.write(png, f"{index:02d}.png")
            return Response(data.getvalue(), media_type="application/zip",
                            headers={"Content-Disposition": f'attachment; filename="{rid}-{lid}.zip"'})
        if r and name == "html":
            path = r["html"]
        elif r and name.isdigit() and 1 <= int(name) <= len(r["pngs"]):
            path = r["pngs"][int(name) - 1]
        if not path:
            raise HTTPException(404, "文件不存在")
        media = "text/html; charset=utf-8" if name == "html" else "image/png"
        return FileResponse(path, media_type=media, filename=Path(path).name)

    @app.get("/api/llm/runs/{rid}", dependencies=[Depends(auth)])
    def llm_status(rid: str):
        with lock:
            rec = llm_runs.get(rid)
            if not rec:
                raise HTTPException(404, "运行记录不存在")
            out = dict(rec)
        out["renders"] = {k: {"pages": len(v["pngs"])} for k, v in (out.get("renders") or {}).items()} \
            if out.get("render_version") == RENDER_VERSION else {}
        try:     # layouts added to the template later (or a run from before 4.12) can be switched to as well
            extra = outputs.get(out.get("output") or "")["layouts"] if out.get("status") == "done" else []
        except EngineError:
            extra = []
        out["layouts"] = extra
        from vtx.reading import render_markdown, plain_text

        out["html"] = render_markdown(out.get("markdown", ""))
        out["text"] = plain_text(out.get("markdown", ""))
        return out

    # ---- workflow choice (remembered between visits)
    workflow_file = paths.workflow

    @app.get("/api/workflow", dependencies=[Depends(auth)])
    def workflow_get():
        import json

        saved = json.loads(workflow_file.read_text(encoding="utf-8")) if workflow_file.exists() else {}
        known = {o["id"] for o in outputs.list()}
        # pre-4.12 entries were {kind, ref}: text refine / poster templates became output templates
        old = {"text": lambda r: r, "poster": lambda r: "structured", "custom": lambda r: r}
        nodes = [n if isinstance(n, str) else old.get(n.get("kind"), lambda r: "")(n.get("ref") or "")
                 for n in saved.get("nodes", [])]
        nodes = list(dict.fromkeys(n for n in nodes if n in known))
        return {"nodes": nodes, "service": llm_registry.default()}

    @app.put("/api/workflow", dependencies=[Depends(auth)])
    def workflow_put(body: WorkflowIn):
        import json

        workflow_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(workflow_file, json.dumps(body.model_dump(), ensure_ascii=False).encode("utf-8"))
        return workflow_get()

    # ---- history
    @app.get("/api/history", dependencies=[Depends(auth)])
    def history_list():
        with lock:
            items = []
            for j in sorted(jobs.values(), key=lambda j: j.get("created_at") or 0, reverse=True):
                m = (j.get("result") or {}).get("meta", {})
                ai = sorted(({"id": r["id"], "kind": r.get("kind"), "output": r.get("output"), "name": r.get("name"),
                              "model": r.get("model"), "created_at": r.get("created_at"),
                              "status": r.get("status"), "error": r.get("error"), "progress": r.get("progress")}
                             for r in llm_runs.values() if r["job"] == j["id"]),
                            key=lambda r: r["created_at"] or 0, reverse=True)
                title = m.get("title") or (j.get("info") or {}).get("title") or j.get("url", "")
                items.append({"id": j["id"], "title": title, "platform": m.get("platform", ""),
                              "author": m.get("author", ""), "part": m.get("part"), "duration": m.get("duration"),
                              "url": j.get("url", ""), "created_at": j.get("created_at"), "ai": ai,
                              "status": j["status"], "stage": j.get("stage"), "error": j.get("error")})
        return {"items": items}

    @app.delete("/api/history/{job_id}", dependencies=[Depends(auth)])
    def history_delete(job_id: str):
        import shutil

        with lock:
            job = jobs.get(job_id)
            if not job:
                raise HTTPException(404, "任务不存在")
            busy = job["status"] in ("queued", "running") or any(
                r["job"] == job_id and r["status"] in ("queued", "running") for r in llm_runs.values())
            if busy:
                raise HTTPException(409, "任务或它的 AI 节点还在运行，完成后再删除")
            jobs.pop(job_id)
            for rid in [rid for rid, r in llm_runs.items() if r["job"] == job_id]:
                llm_runs.pop(rid)
            for key in [k for k in points_cache if k[0] == job_id]:
                points_cache.pop(key)
        target = (work_dir / "jobs" / job_id).resolve()
        if target.parent == (work_dir / "jobs").resolve() and target.is_dir():
            shutil.rmtree(target)
        return {"ok": True}

    static = Path(__file__).parent / "web"

    @app.get("/")
    def index():
        return FileResponse(static / "index.html", media_type="text/html; charset=utf-8")

    @app.get("/settings")
    def settings_page():
        return FileResponse(static / "settings.html", media_type="text/html; charset=utf-8")

    STATIC_FILES = {"common.js": "text/javascript", "settings.js": "text/javascript", "app.js": "text/javascript",
                    "common.css": "text/css"}

    @app.get("/static/{name}")
    def static_file(name: str):
        if name not in STATIC_FILES:        # fixed list: no path traversal
            raise HTTPException(404, "文件不存在")
        return FileResponse(static / name, media_type=STATIC_FILES[name] + "; charset=utf-8")

    return app


def main():
    import os

    import uvicorn

    from vtx import config

    import json

    paths = AppPaths.for_app(config.load().work_dir, config.ROOT)
    paths.migrate_legacy()
    data = json.loads(paths.app.read_text(encoding="utf-8")) if paths.app.exists() else {}
    host = os.environ.get("VTX_HOST", data.get("host", "127.0.0.1"))
    port = int(os.environ.get("VTX_PORT", data.get("port", 8765)))
    token = os.environ.get("VTX_TOKEN", "")
    check_bind(host, token)
    app = create_app(config.load().work_dir, token, reveal_keys=is_loopback(host))
    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
