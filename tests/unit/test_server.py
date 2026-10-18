"""T13 acceptance 1-3: bind policy, token auth, job API, non-blocking long jobs (pipeline stubbed)."""
import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vtx.fetch import FetchError
from vtx.pipeline import RunResult
from vtx.server import BindError, check_bind, create_app


def test_loopback_without_token_is_allowed():
    check_bind("127.0.0.1", token="")


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::"])
def test_public_bind_without_token_refused(host):
    with pytest.raises(BindError):
        check_bind(host, token="")


def test_public_bind_with_short_token_refused():
    with pytest.raises(BindError):
        check_bind("0.0.0.0", token="abc")


def test_public_bind_with_token_allowed():
    check_bind("0.0.0.0", token="x" * 24)


def fake_run_factory(delay=0.0, fail=None):
    def run(url, out_dir, progress):
        for stage in ("resolve", "fetch", "asr", "qc", "output"):
            progress(stage)
            time.sleep(delay / 5)
        if fail:
            raise fail
        out_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for k, suffix in {"reading": ".reading.txt", "original": ".original.txt", "srt": ".srt",
                          "edits": ".edits.json", "issues": ".issues.json"}.items():
            p = out_dir / f"x{suffix}"
            p.write_text("[]" if suffix.endswith(".json") else f"{k} 内容", encoding="utf-8")
            files[k] = p
        meta = {"title": "示例", "platform": "bilibili", "video_id": "BV1", "part": 1, "duration": 10.0,
                "engine": "dashscope/paraformer-v2", "billed_seconds": 9, "url": url}
        return RunResult(files, meta, issues=0, edits=0, cached_asr=False)
    return run


def client(tmp_path, token="", **kw):
    app = create_app(work_dir=tmp_path, token=token, run=fake_run_factory(**kw))
    return TestClient(app)


def wait_done(c, job_id, headers=None, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = c.get(f"/api/jobs/{job_id}", headers=headers or {}).json()
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_submit_progress_result(tmp_path):
    c = client(tmp_path)
    job = c.post("/api/jobs", json={"url": "https://b23.tv/x"}).json()
    done = wait_done(c, job["id"])
    assert done["status"] == "done" and done["stage"] == "output"
    assert done["result"]["meta"]["billed_seconds"] == 9
    text = c.get(f"/api/jobs/{job['id']}/files/reading")
    assert text.status_code == 200 and "reading 内容" in text.text


def test_failed_job_reports_category(tmp_path):
    c = client(tmp_path, fail=FetchError("login_required", "需要登录"))
    job = c.post("/api/jobs", json={"url": "https://b23.tv/x"}).json()
    done = wait_done(c, job["id"])
    assert done["status"] == "failed" and done["error"]["category"] == "login_required"


def test_unknown_job_and_file(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/jobs/nope").status_code == 404
    job = c.post("/api/jobs", json={"url": "https://b23.tv/x"}).json()
    wait_done(c, job["id"])
    assert c.get(f"/api/jobs/{job['id']}/files/../../etc").status_code == 404


def test_empty_url_rejected(tmp_path):
    assert client(tmp_path).post("/api/jobs", json={"url": "  "}).status_code == 422


def test_token_required_when_set(tmp_path):
    c = client(tmp_path, token="t" * 24)
    assert c.post("/api/jobs", json={"url": "https://b23.tv/x"}).status_code == 401
    assert c.get("/api/jobs/x", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = c.post("/api/jobs", json={"url": "https://b23.tv/x"}, headers={"Authorization": "Bearer " + "t" * 24})
    assert ok.status_code == 200


def _registry(tmp_path):
    from vtx.engines import Registry

    resolve = lambda host: {"api.example.com": ["93.184.216.34"]}[host]
    return Registry(tmp_path / "engines.json", tmp_path / "engine_keys.json", env_file=tmp_path / ".env", resolve=resolve)


def test_engines_crud_never_returns_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    app = create_app(work_dir=tmp_path, run=fake_run_factory(), registry=_registry(tmp_path))
    c = TestClient(app)
    listing = c.get("/api/engines").json()
    assert listing["services"][0]["id"] == "dashscope" and listing["default"] is None
    assert {p["preset"] for p in listing["presets"]} >= {"tencent", "openai", "groq", "siliconflow", "custom"}

    r = c.post("/api/engines", json={"kind": "openai", "label": "我的", "base_url": "https://api.example.com/v1",
                                     "model": "whisper-1", "key": "sk-very-secret-000111"})
    assert r.status_code == 200 and r.json()["configured"]
    sid = r.json()["id"]
    assert "very-secret" not in r.text
    assert "very-secret" not in c.get("/api/engines").text
    assert c.get("/api/engines").json()["default"] == sid

    assert c.put(f"/api/engines/{sid}", json={"model": "whisper-large-v3"}).json()["model"] == "whisper-large-v3"
    bad = c.put(f"/api/engines/{sid}", json={"base_url": "http://192.168.1.1/v1", "key": "sk-leak-me-please-9"})
    assert bad.status_code == 422 and "leak-me" not in bad.text
    assert c.delete(f"/api/engines/{sid}").json() == {"ok": True}
    assert c.delete("/api/engines/dashscope").status_code == 200
    assert c.get("/api/engines").json()["services"] == []
    assert c.delete("/api/engines/nope").status_code == 404


def test_job_uses_selected_engine(tmp_path):
    seen = {}

    def run(url, out_dir, progress, engine=None):
        seen["engine"] = engine
        return fake_run_factory()(url, out_dir, progress)

    reg = _registry(tmp_path)
    sid = reg.add({"kind": "openai", "label": "x", "base_url": "https://api.example.com/v1", "model": "m"},
                  key="sk-aaaaaaaaaaaa")
    c = TestClient(create_app(work_dir=tmp_path, run=run, registry=reg))
    job = c.post("/api/jobs", json={"url": "https://b23.tv/x", "engine": sid}).json()
    assert wait_done(c, job["id"])["status"] == "done" and seen["engine"] == sid
    assert c.post("/api/jobs", json={"url": "https://b23.tv/x", "engine": "nope"}).status_code == 422


def test_job_with_unconfigured_engine_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    c = TestClient(create_app(work_dir=tmp_path, run=fake_run_factory(), registry=_registry(tmp_path)))
    r = c.post("/api/jobs", json={"url": "https://b23.tv/x", "engine": "dashscope"})
    assert r.status_code == 422 and "API key" in r.json()["detail"]


def test_engine_test_endpoint_reports_success_and_failure(tmp_path):
    from vtx.asr import Segment
    from vtx.asr_cloud import CloudASRError

    reg = _registry(tmp_path)
    sid = reg.add({"kind": "openai", "label": "x", "base_url": "https://api.example.com/v1", "model": "m"},
                  key="sk-bbbbbbbbbbbb")

    class Good:
        def transcribe(self, wav):
            assert wav.exists()
            return [Segment(0, 9.9, 13.8, "每次服用1.5毫升。")]

    class Bad:
        def transcribe(self, wav):
            raise CloudASRError("ASR_ACCESS_DENIED", "HTTP 401 key sk-bbbbbbbbbbbb invalid")

    c = TestClient(create_app(work_dir=tmp_path, run=fake_run_factory(), registry=reg))
    reg.build = lambda s: Good()
    ok = c.post(f"/api/engines/{sid}/test").json()
    assert ok["ok"] and "毫升" in ok["text"]
    reg.build = lambda s: Bad()
    bad = c.post(f"/api/engines/{sid}/test")
    assert not bad.json()["ok"] and bad.json()["code"] == "ASR_ACCESS_DENIED" and "bbbbbbbbbbbb" not in bad.text


def _llm_app(tmp_path, chat):
    from vtx.llm import LLMRegistry

    resolve = lambda host: {"api.example.com": ["93.184.216.34"]}[host]
    llm = LLMRegistry(tmp_path / "llm.json", tmp_path / "llm_keys.json", resolve=resolve)

    class Client:
        context = 32000

        def chat(self, messages):
            if any("SOURCE_BACKED_PROOFREAD_V1" in message["content"] for message in messages):
                return '{"patches": []}', {}
            return chat(messages)

    llm.client = lambda sid: Client()
    app = create_app(work_dir=tmp_path, run=fake_run_factory(), registry=_registry(tmp_path), llm_registry=llm)
    return TestClient(app), llm


def _finished_job(c):
    job = c.post("/api/jobs", json={"url": "https://b23.tv/x"}).json()
    wait_done(c, job["id"])
    return job["id"]


def _wait_llm(c, rid):
    deadline = time.time() + 5
    while time.time() < deadline:
        r = c.get(f"/api/llm/runs/{rid}").json()
        if r["status"] not in ("running", "queued"):
            return r
        time.sleep(0.05)
    raise AssertionError("llm run did not finish")


def test_llm_services_and_outputs_api(tmp_path):
    c, _ = _llm_app(tmp_path, lambda m: ("x", {}))
    assert {p["preset"] for p in c.get("/api/llm/services").json()["presets"]} >= {"deepseek", "qwen", "custom"}
    r = c.post("/api/llm/services", json={"label": "DS", "base_url": "https://api.example.com/v1",
                                          "model": "deepseek-chat", "key": "sk-llm-secret-123456"})
    assert r.status_code == 200 and "llm-secret" not in r.text
    assert "llm-secret" not in c.get("/api/llm/services").text
    assert c.post("/api/llm/services", json={"label": "x", "base_url": "http://10.1.1.1/v1", "model": "m",
                                             "key": "sk-zzzzzzzz"}).status_code == 422
    outs = c.get("/api/outputs").json()
    assert [o["id"] for o in outs["outputs"]][:3] == ["structure", "refine", "structured"] and len(outs["kinds"]) == 4
    oid = c.post("/api/outputs", json={"kind": "free", "name": "我的", "prompt": "三句话：{text}"}).json()["id"]
    assert c.put(f"/api/outputs/{oid}", json={"name": "我的2"}).json()["name"] == "我的2"
    assert c.post("/api/outputs", json={"kind": "source", "name": "t", "prompt": "无段落"}).status_code == 422
    assert c.delete("/api/outputs/refine").status_code == 422
    assert c.post("/api/outputs/qa/hide").json()["hidden"]
    assert "qa" not in [o["id"] for o in c.get("/api/outputs").json()["outputs"]]
    assert c.delete(f"/api/outputs/{oid}").json() == {"ok": True}


STRUCTURED_REPLY = json.dumps({
    "title": "示例", "conclusion": "一句话结论。",
    "points": [{"heading": "要点一", "body": "内容。", "key": True, "detail": ["展开"],
                "quotes": [{"quote": "原话", "source": "原话"}]}]}, ensure_ascii=False)


def _ai_app(tmp_path, reply=STRUCTURED_REPLY, fail=None):
    calls = []

    def chat(messages):
        prompt = messages[-1]["content"]
        calls.append(prompt)
        if fail and fail(prompt):
            from vtx.llm import LLMError

            raise LLMError("LLM_BILLING_UNAVAILABLE", "余额不足")
        if "# 精校原文" in prompt:          # refine prompt: echo paragraphs back unchanged
            paras = json.loads(prompt.rsplit("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0])
            return json.dumps(paras, ensure_ascii=False), {"total_tokens": 5}
        if "按句编号" in prompt:        # structure prompt: one section, original order
            ids = [x["i"] for x in json.loads(prompt.rsplit("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0])]
            return json.dumps({"sections": [{"heading": "全部", "blocks": [{"type": "p", "ids": ids}]}]}), {"total_tokens": 4}
        return reply, {"total_tokens": 9}

    c, llm = _llm_app(tmp_path, chat)
    sid = c.post("/api/llm/services", json={"label": "DS", "base_url": "https://api.example.com/v1",
                                            "model": "m", "key": "sk-ai-secret-123456"}).json()["id"]
    return c, sid, calls


@pytest.fixture
def fake_png(monkeypatch):
    """No browser in unit tests: record what would be rendered."""
    rendered = []

    def render(data, meta, layout, png, html=None, footer=None):
        png.write_bytes(b"\x89PNG")
        if html:
            html.write_text("<p>", encoding="utf-8")
        rendered.append((layout.get("id") if isinstance(layout, dict) else layout, footer))

    def pages(template, title, kicker, blocks, footer, out_dir, stem):
        out_dir.mkdir(parents=True, exist_ok=True)
        pngs = []
        for k in (1, 2):
            p = out_dir / f"{stem}-{k}.png"
            p.write_bytes(b"\x89PNG")
            pngs.append(str(p))
        (out_dir / f"{stem}.html").write_text("<p>", encoding="utf-8")
        rendered.append((template["id"], footer))
        return {"pngs": pngs, "html": str(out_dir / f"{stem}.html"), "width": template["width"], "pages": 2}

    monkeypatch.setattr("vtx.poster.render_png", render)
    monkeypatch.setattr("vtx.article.render_pages", pages)
    return rendered


def _ai(c, jid, output, sid, **kw):
    r = c.post(f"/api/jobs/{jid}/ai", json={"output": output, "service": sid, **kw})
    assert r.status_code == 200, r.text
    return _wait_llm(c, r.json()["id"])


@pytest.mark.parametrize("transcript", ["", "的的的的的的的的的的的的的的的的。"])
def test_bad_source_stops_before_paid_generation(tmp_path, transcript):
    c, sid, calls = _ai_app(tmp_path)
    jid = _finished_job(c)
    (tmp_path / "jobs" / jid / "x.original.txt").write_text(transcript, encoding="utf-8")
    run = _ai(c, jid, "notes", sid)
    assert run["status"] == "failed" and run["error"]["code"] == "LLM_BAD_SOURCE"
    assert calls == [] and not run["renders"]


def test_shared_llm_key_can_be_revealed_only_with_permission(tmp_path):
    c, llm = _llm_app(tmp_path, lambda m: ("ok", {}))
    llm.fallback_keys["dashscope"] = lambda: "test-shared-credential"
    sid = llm.add({"label": "共享", "base_url": "https://api.example.com/v1", "model": "m", "key_from": "dashscope"}, key="")
    assert c.get(f"/api/llm/services/{sid}/secrets").status_code == 403
    permitted = TestClient(create_app(work_dir=tmp_path, run=fake_run_factory(), registry=_registry(tmp_path),
                                      llm_registry=llm, reveal_keys=True))
    assert permitted.get(f"/api/llm/services/{sid}/secrets").json() == {"key": "test-shared-credential"}


def test_ai_validated_and_job_state(tmp_path):
    c, sid, _ = _ai_app(tmp_path)
    assert c.post("/api/jobs/nope/ai", json={"output": "refine"}).status_code == 404
    jid = _finished_job(c)
    assert c.post(f"/api/jobs/{jid}/ai", json={"service": sid}).status_code == 422
    assert c.post(f"/api/jobs/{jid}/ai", json={"output": "nope", "service": sid}).status_code == 404


def test_ai_job_not_finished_is_409(tmp_path):
    app = create_app(work_dir=tmp_path, run=fake_run_factory(delay=3.0), registry=_registry(tmp_path))
    c = TestClient(app)
    job = c.post("/api/jobs", json={"url": "https://b23.tv/x"}).json()
    assert c.post(f"/api/jobs/{job['id']}/ai", json={"output": "refine"}).status_code == 409
    wait_done(c, job["id"], timeout=10)


def test_points_run_renders_default_and_switches_layout_without_model(tmp_path, fake_png):
    c, sid, calls = _ai_app(tmp_path)
    jid = _finished_job(c)
    a = _ai(c, jid, "structured", sid)
    assert a["status"] == "done" and a["kind"] == "points" and a["renders"] == {"A": {"pages": 1}}
    assert a["markdown"].startswith("# 示例") and "版权" in a["markdown"] and a["content"]["points"]
    assert c.post(f"/api/llm/runs/{a['id']}/render/cover-qa").json() == {"layout": "cover-qa", "pages": 1}
    assert c.post(f"/api/llm/runs/{a['id']}/render/book").status_code == 422          # article layout on points
    assert len(calls) == 1 and [r[0] for r in fake_png] == ["A", "cover-qa"]
    assert c.get(f"/api/llm/runs/{a['id']}/files/cover-qa/1").status_code == 200
    assert c.get(f"/api/llm/runs/{a['id']}/files/cover-qa/2").status_code == 404
    again = _ai(c, jid, "structured", sid)                                             # same prompt -> reuse
    assert again["cached"] and len(calls) == 1


def test_structure_run_builds_from_original_sentences(tmp_path, fake_png):
    c, sid, calls = _ai_app(tmp_path)
    jid = _finished_job(c)
    r = _ai(c, jid, "structure", sid)
    assert r["status"] == "done" and r["kind"] == "structure" and "按句编号" in calls[-1]
    assert r["markdown"].startswith("# 示例\n\n## 全部\n\n") and r["renders"] == {"book": {"pages": 2}}
    assert c.post(f"/api/llm/runs/{r['id']}/render/night").json()["pages"] == 2
    assert c.get(f"/api/llm/runs/{r['id']}/files/night/html").status_code == 200


def test_multipage_images_download_in_one_complete_archive(tmp_path, fake_png):
    from io import BytesIO
    from zipfile import ZipFile

    c, sid, calls = _ai_app(tmp_path)
    run = _ai(c, _finished_job(c), "structure", sid)
    before = len(calls)
    response = c.get(f"/api/llm/runs/{run['id']}/files/book/zip")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    with ZipFile(BytesIO(response.content)) as archive:
        assert archive.namelist() == ["01.png", "02.png"]
        for index, filename in enumerate(archive.namelist(), 1):
            assert archive.read(filename) == c.get(f"/api/llm/runs/{run['id']}/files/book/{index}").content
    assert len(calls) == before
    assert c.get(f"/api/llm/runs/{run['id']}/files/missing/zip").status_code == 404


def test_refine_edited_and_still_guarded(tmp_path, fake_png):
    c, sid, calls = _ai_app(tmp_path)
    jid = _finished_job(c)
    tpl = c.get("/api/outputs").json()["outputs"][1]["prompt"]
    c.put("/api/outputs/refine", json={"prompt": "【我的要求】\n" + tpl})
    r = _ai(c, jid, "refine", sid)
    assert r["status"] == "done" and r["content"] and r["stats"]["ok"] == len(r["content"])
    assert calls[-1].startswith("【我的要求】") and "# 精校原文" in calls[-1]


def test_source_template_rewrite_is_reverted(tmp_path, fake_png):
    """A user 原文类 prompt cannot get past the guard: here the model ignores the paragraph format."""
    c, sid, calls = _ai_app(tmp_path)
    oid = c.post("/api/outputs", json={"kind": "source", "name": "改写", "prompt": "改写成书面语：{paragraphs}"}).json()["id"]
    jid = _finished_job(c)
    r = _ai(c, jid, oid, sid)
    assert calls[-1].startswith("改写成书面语：")
    assert r["status"] == "done" and all(p["status"] == "reverted" for p in r["content"])


def test_free_output_and_one_off_prompt_and_error_hides_key(tmp_path, fake_png):
    c, sid, calls = _ai_app(tmp_path, reply="## 问答\n\n**Q：盐？**\nA：要吃。")
    jid = _finished_job(c)
    r = _ai(c, jid, "qa", sid, prompt="只问一个问题：{text}")
    assert r["status"] == "done" and calls[-1].startswith("只问一个问题：")
    assert r["markdown"].startswith("# 示例\n\n## 问答") and r["renders"] == {"book": {"pages": 2}}
    assert "ai-secret" not in json.dumps(r)


def _wait_ai(c, jid, n, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = c.get(f"/api/jobs/{jid}").json()
        if len(job["ai"]) == n and all(a["status"] in ("done", "failed") for a in job["ai"]):
            return job["ai"]
        time.sleep(0.05)
    raise AssertionError("workflow did not finish")


def test_workflow_runs_after_recognition_and_retry(tmp_path, fake_png):
    fail = {"on": True}
    c, sid, calls = _ai_app(tmp_path, fail=lambda prompt: fail["on"] and "# 精校原文" in prompt)
    assert c.post("/api/jobs", json={"url": "https://b23.tv/x", "workflow": ["nope"],
                                     "llm_service": sid}).status_code == 404
    job = c.post("/api/jobs", json={"url": "https://b23.tv/x", "workflow": ["refine", "structured"],
                                    "llm_service": sid}).json()
    wait_done(c, job["id"])
    ai = _wait_ai(c, job["id"], 2)
    assert [a["status"] for a in ai] == ["failed", "done"]           # one failure does not stop the next node
    assert ai[0]["error"]["code"] == "LLM_BILLING_UNAVAILABLE" and ai[1]["name"] == "结构化要点"
    fail["on"] = False
    assert c.post(f"/api/llm/runs/{ai[1]['id']}/retry").status_code == 409   # only failed nodes
    c.post(f"/api/llm/runs/{ai[0]['id']}/retry")
    assert [a["status"] for a in _wait_ai(c, job["id"], 2)] == ["done", "done"]


def test_workflow_does_not_block_next_recognition(tmp_path, fake_png):
    import threading as th

    gate = th.Event()

    def chat(messages):
        gate.wait(5)                     # the AI node is stuck until the second video is recognized
        return "结果", {}

    c, _ = _llm_app(tmp_path, chat)
    sid = c.post("/api/llm/services", json={"label": "DS", "base_url": "https://api.example.com/v1",
                                            "model": "m", "key": "sk-ai-secret-123456"}).json()["id"]
    first = c.post("/api/jobs", json={"url": "https://b23.tv/1", "workflow": ["notes"], "llm_service": sid}).json()
    wait_done(c, first["id"])
    second = c.post("/api/jobs", json={"url": "https://b23.tv/2"}).json()
    assert wait_done(c, second["id"])["status"] == "done"
    assert c.get(f"/api/jobs/{first['id']}").json()["ai"][0]["status"] in ("queued", "running")
    gate.set()
    assert _wait_ai(c, first["id"], 1)[0]["status"] == "done"


def test_recognition_stays_pending_until_workflow_registered(tmp_path, fake_png, monkeypatch):
    from vtx.outputs import OutputStore

    entered, release = threading.Event(), threading.Event()
    get = OutputStore.get

    def blocked_get(self, oid):
        if threading.current_thread().name == "vtx-worker":
            entered.set()
            assert release.wait(5)
        return get(self, oid)

    c, sid, calls = _ai_app(tmp_path)
    monkeypatch.setattr(OutputStore, "get", blocked_get)
    jid = c.post("/api/jobs", json={"url": "https://b23.tv/x", "workflow": ["structured"],
                                   "llm_service": sid}).json()["id"]
    try:
        assert entered.wait(5)
        job = c.get(f"/api/jobs/{jid}").json()
        item = c.get("/api/history").json()["items"][0]
        assert job["status"] == item["status"] == "running"
        assert job["ai"] == item["ai"] == []
        assert c.delete(f"/api/history/{jid}").status_code == 409
        assert c.post(f"/api/jobs/{jid}/ai", json={"output": "structured", "service": sid}).status_code == 409
    finally:
        release.set()
    done = wait_done(c, jid)
    assert done["status"] == "done" and len(done["ai"]) == 1
    assert _wait_ai(c, jid, 1)[0]["status"] == "done"
    assert len(calls) == 1


def test_workflow_choice_remembered(tmp_path):
    c, sid, _ = _ai_app(tmp_path)
    assert c.get("/api/workflow").json() == {"nodes": [], "service": sid}
    c.put("/api/workflow", json={"nodes": ["structure", "cards"], "service": sid})
    assert c.get("/api/workflow").json()["nodes"] == ["structure", "cards"]
    assert c.put("/api/workflow", json={"nodes": [{"kind": "x"}]}).status_code == 422


def test_explicit_default_overrides_saved_workflow_service(tmp_path):
    c, a, _ = _ai_app(tmp_path)
    c.put("/api/workflow", json={"nodes": ["notes"], "service": a})
    b = c.post("/api/llm/services", json={"label": "新默认", "base_url": "https://api.example.com/v1",
                                          "model": "m", "key": "test-default-key"}).json()["id"]
    c.post(f"/api/llm/services/{b}/set-default")
    assert c.get("/api/workflow").json()["service"] == b


def test_llm_settings_cache_versions_and_history_preserved(tmp_path, fake_png):
    c, sid, calls = _ai_app(tmp_path)
    jid = _finished_job(c)
    c.put("/api/llm/settings", json={"system_prompt": "旧规则"})
    first = _ai(c, jid, "structured", sid)
    assert len(calls) == 1
    assert _ai(c, jid, "structured", sid)["cached"]
    assert c.put("/api/llm/settings", json={"system_prompt": "新规则"}).json()["system_prompt"] == "新规则"
    later = _ai(c, jid, "structured", sid)
    assert len(calls) == 2 and not later["cached"] and later["prompt_hash"] != first["prompt_hash"]
    assert c.get(f"/api/llm/runs/{first['id']}").json()["system_prompt"] == "旧规则"
    assert c.get("/api/llm/settings").json()["system_prompt"] == "新规则"
    assert c.put("/api/llm/settings", json={"system_prompt": " "}).status_code == 422
    assert c.put("/api/llm/settings", json={"system_prompt": "x" * 20001}).status_code == 422


def test_running_generation_keeps_rule_snapshot(tmp_path, fake_png):
    entered, release = threading.Event(), threading.Event()
    seen = []

    def chat(messages):
        seen.append(messages[0])
        entered.set()
        release.wait(3)
        return "正文", {}

    c, llm = _llm_app(tmp_path, chat)
    sid = llm.add({"label": "测试", "base_url": "https://api.example.com/v1", "model": "m"}, "test-key")
    jid = _finished_job(c)
    c.put("/api/llm/settings", json={"system_prompt": "任务规则"})
    rid = c.post(f"/api/jobs/{jid}/ai", json={"output": "notes", "service": sid}).json()["id"]
    assert entered.wait(2)
    c.put("/api/llm/settings", json={"system_prompt": "后改规则"})
    release.set()
    assert _wait_llm(c, rid)["status"] == "done"
    assert seen == [{"role": "system", "content": "任务规则"}]


def test_layout_reverse_binding_and_new_article_skeleton(tmp_path):
    c = client(tmp_path)
    r = c.post("/api/layouts", json={"name": "新文章", "group": "article", "outputs": ["notes", "qa"]})
    assert r.status_code == 200, r.text
    layout = r.json()
    assert layout["group"] == "article" and set(layout["outputs"]) == {"notes", "qa"}
    assert 'class="content"' in layout["html"] and layout["height_mode"].startswith("fixed:")
    oid = c.post("/api/outputs", json={"name": "笔记", "kind": "free", "prompt": "{text}",
                                      "layouts": [layout["id"], "book"]}).json()["id"]
    lid = layout["id"]
    assert oid in next(t for t in c.get("/api/layouts").json()["layouts"] if t["id"] == lid)["outputs"]
    assert c.put(f"/api/layouts/{lid}", json={"name": "更新", "outputs": [oid]}).status_code == 200
    outs = {o["id"]: o for o in c.get("/api/outputs?hidden=true").json()["outputs"]}
    assert lid not in outs["notes"]["layouts"] and lid in outs[oid]["layouts"]


def test_invalid_bindings_leave_both_configs_intact(tmp_path):
    c = client(tmp_path)
    layout = c.post("/api/layouts", json={"name": "独占", "group": "article"}).json()
    lid = layout["id"]
    oid = c.post("/api/outputs", json={"name": "仅此版式", "kind": "free", "prompt": "{text}",
                                      "layouts": [lid]}).json()["id"]
    layouts_before = c.get("/api/layouts").json()
    outputs_before = c.get("/api/outputs?hidden=true").json()
    assert c.put(f"/api/layouts/{lid}", json={"name": "不应保存", "outputs": []}).status_code == 422
    assert c.put(f"/api/layouts/{lid}", json={"outputs": [oid, "structured"]}).status_code == 422
    assert c.post("/api/layouts", json={"name": "不应创建", "group": "points", "outputs": ["notes"]}).status_code == 422
    assert c.get("/api/layouts").json() == layouts_before
    assert c.get("/api/outputs?hidden=true").json() == outputs_before
    assert c.delete(f"/api/layouts/{lid}").status_code == 422


def test_changed_bindings_are_authoritative_for_old_result(tmp_path, fake_png):
    c, sid, _ = _ai_app(tmp_path)
    result = _ai(c, _finished_job(c), "structured", sid)
    c.put("/api/outputs/structured", json={"layouts": ["B"]})
    reopened = c.get(f"/api/llm/runs/{result['id']}").json()
    assert reopened["layouts"] == ["B"]
    assert c.post(f"/api/llm/runs/{result['id']}/render/A").status_code == 422
    assert c.get(f"/api/llm/runs/{result['id']}/files/A/1").status_code == 200


def test_proofread_shared_result_cached_through_restart(tmp_path, fake_png, monkeypatch):
    from copy import deepcopy
    from vtx.proofread import ProofreadResult

    corrections = []

    def correct(client, document, source, reference="", title=""):
        assert title == "示例"
        corrections.append(source)
        final = deepcopy(document)
        final["conclusion"] = "经来源核对的结论。"
        return ProofreadResult(final, {"total_tokens": 3}, {"status": "passed", "accepted": ["test"]})

    monkeypatch.setattr("vtx.proofread.proofread", correct)
    c, sid, calls = _ai_app(tmp_path)
    jid = _finished_job(c)
    original = (tmp_path / "jobs" / jid / "x.original.txt").read_text(encoding="utf-8")
    run = _ai(c, jid, "structured", sid)
    assert run["content"]["conclusion"] == "经来源核对的结论。"
    assert "经来源核对的结论。" in run["markdown"] and "经来源核对的结论。" in run["text"]
    assert run["usage"]["total_tokens"] == 12 and run["proofread"]["status"] == "passed"
    c.post(f"/api/llm/runs/{run['id']}/render/B")
    assert len(corrections) == len(calls) == 1
    assert (tmp_path / "jobs" / jid / "x.original.txt").read_text(encoding="utf-8") == original
    reopened, _ = _llm_app(tmp_path, lambda messages: pytest.fail("cached result called the model"))
    again = _ai(reopened, jid, "structured", sid)
    assert again["cached"] and again["proofread"] == run["proofread"] and len(corrections) == 1


def test_proofread_failure_keeps_audit_and_does_not_render(tmp_path, fake_png, monkeypatch):
    from vtx.llm import LLMError

    def reject(*args, **kwargs):
        error = LLMError("LLM_BAD_OUTPUT", "校对补丁未通过")
        error.audit = {"status": "failed", "rejected": [{"reason": "无来源依据"}], "usage": {"total_tokens": 13}}
        raise error

    monkeypatch.setattr("vtx.proofread.proofread", reject)
    c, sid, _ = _ai_app(tmp_path)
    jid = _finished_job(c)
    run = _ai(c, jid, "notes", sid)
    assert run["status"] == "failed" and not run["renders"] and not fake_png
    assert run["usage"]["total_tokens"] == 22
    saved = json.loads((tmp_path / "jobs" / jid / "ai" / f"{run['id']}.json").read_text(encoding="utf-8"))
    assert saved["proofread"]["status"] == "failed" and saved["proofread"]["rejected"]
    assert saved["usage"] == run["usage"]


def test_llm_connection_test_uses_saved_config_and_categorizes_failure(tmp_path):
    failing = {"on": False}

    def chat(messages):
        from vtx.llm import LLMError
        if failing["on"]:
            raise LLMError("LLM_ACCESS_DENIED", "凭证无效")
        return "OK", {}

    c, llm = _llm_app(tmp_path, chat)
    sid = llm.add({"label": "测试", "base_url": "https://api.example.com/v1", "model": "m"}, "test-key")
    assert c.post(f"/api/llm/services/{sid}/test").json()["ok"]
    failing["on"] = True
    r = c.post(f"/api/llm/services/{sid}/test").json()
    assert not r["ok"] and r["code"] == "LLM_ACCESS_DENIED"


def test_draft_llm_test_uses_current_config_without_saving(tmp_path, monkeypatch):
    seen = []

    def chat(client, messages):
        seen.append((client.base_url, client.model, client._key, messages))
        return "OK", {}

    monkeypatch.setattr("vtx.llm.ChatClient.chat", chat)
    c, llm = _llm_app(tmp_path, None)
    fields = {"label": "新模型", "base_url": "https://api.example.com/v2", "model": "new-model", "key": "draft-key"}
    assert c.post("/api/llm/services/test", json=fields).json() == {"ok": True, "message": "已连接"}
    assert seen == [(fields["base_url"], "new-model", "draft-key", [{"role": "user", "content": "仅回复 OK"}])]
    assert not llm.config_file.exists() and not llm.keys_file.exists()
    sid = llm.add({"label": "已存模型", "base_url": "https://api.example.com/v1", "model": "old-model"}, "stored-key")
    llm.set_default(sid)
    llm.mark_used(sid)
    before = (llm.config_file.read_bytes(), llm.keys_file.read_bytes())
    fields.pop("key")
    assert c.post(f"/api/llm/services/test?sid={sid}", json=fields).json()["ok"]
    assert seen[-1][:3] == (fields["base_url"], "new-model", "stored-key")
    assert (llm.config_file.read_bytes(), llm.keys_file.read_bytes()) == before


@pytest.mark.parametrize("fields", [
    {"context": 100}, {"model": ""}, {"base_url": "http://10.0.0.1/v1"},
    {"key_from": "unknown"}, {"key": ""},
])
def test_draft_llm_validation_does_not_call_provider(tmp_path, monkeypatch, fields):
    calls = []
    monkeypatch.setattr("vtx.llm.ChatClient.chat", lambda *args: calls.append(args))
    c, llm = _llm_app(tmp_path, None)
    body = {"label": "新模型", "base_url": "https://api.example.com/v1", "model": "m", "key": "draft-key", **fields}
    response = c.post("/api/llm/services/test", json=body)
    assert response.status_code == 200 and response.json()["ok"] is False
    assert calls == [] and not llm.config_file.exists() and not llm.keys_file.exists()


@pytest.mark.parametrize("saved", [False, True])
def test_draft_llm_failure_never_echoes_credentials(tmp_path, monkeypatch, saved):
    def chat(client, messages):
        raise RuntimeError(f"upstream rejected {client._key}")

    monkeypatch.setattr("vtx.llm.ChatClient.chat", chat)
    c, llm = _llm_app(tmp_path, None)
    body = {"label": "新模型", "base_url": "https://api.example.com/v1", "model": "m"}
    if saved:
        sid = llm.add(body, "arbitrary-private-credential")
        url = f"/api/llm/services/test?sid={sid}"
    else:
        body["key"] = "arbitrary-private-credential"
        url = "/api/llm/services/test"
    response = c.post(url, json=body)
    assert response.json()["ok"] is False
    assert "arbitrary-private-credential" not in response.text
    assert response.json()["code"] == "LLM_SERVICE_ERROR"


def test_history_delete(tmp_path, fake_png):
    c, sid, _ = _ai_app(tmp_path)
    jid = _finished_job(c)
    _ai(c, jid, "structured", sid)
    assert (tmp_path / "jobs" / jid / "ai").is_dir()
    assert c.delete("/api/history/nope").status_code == 404
    assert c.delete(f"/api/history/{jid}").json() == {"ok": True}
    assert not (tmp_path / "jobs" / jid).exists() and c.get("/api/history").json()["items"] == []
    assert c.get(f"/api/jobs/{jid}").status_code == 404


def test_history_delete_refused_while_running(tmp_path):
    c = TestClient(create_app(work_dir=tmp_path, run=fake_run_factory(delay=2.0), registry=_registry(tmp_path)))
    jid = c.post("/api/jobs", json={"url": "https://b23.tv/x"}).json()["id"]
    assert c.delete(f"/api/history/{jid}").status_code == 409
    wait_done(c, jid, timeout=10)


def test_history_keeps_running_and_queued_recognition(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def run(url, out_dir, progress):
        progress("asr", {"title": "正在识别的视频"})
        entered.set()
        assert release.wait(5)
        return fake_run_factory()(url, out_dir, progress)

    c = TestClient(create_app(work_dir=tmp_path, run=run, registry=_registry(tmp_path)))
    first = c.post("/api/jobs", json={"url": "https://b23.tv/first"}).json()["id"]
    assert entered.wait(5)
    second = c.post("/api/jobs", json={"url": "https://b23.tv/second"}).json()["id"]
    try:
        items = {item["id"]: item for item in c.get("/api/history").json()["items"]}
        assert set(items) == {first, second}
        assert items[first]["status"] == "running" and items[first]["stage"] == "asr"
        assert items[first]["title"] == "正在识别的视频"
        assert items[second]["status"] == "queued" and items[second]["stage"] is None
        assert items[second]["title"] == "https://b23.tv/second"
        assert c.delete(f"/api/history/{first}").status_code == 409
        assert c.delete(f"/api/history/{second}").status_code == 409
    finally:
        release.set()
        wait_done(c, second)


def test_history_keeps_failed_recognition(tmp_path):
    c = client(tmp_path, fail=FetchError("login_required", "需要登录"))
    jid = c.post("/api/jobs", json={"url": "https://b23.tv/failed"}).json()["id"]
    done = wait_done(c, jid)
    items = c.get("/api/history").json()["items"]
    assert len(items) == 1 and items[0]["id"] == jid
    assert items[0]["status"] == "failed"
    assert items[0]["stage"] == done["stage"]
    assert items[0]["error"] == done["error"]


def test_history_keeps_running_and_queued_ai(tmp_path, fake_png):
    entered, release = threading.Event(), threading.Event()

    def chat(messages):
        entered.set()
        assert release.wait(5)
        return STRUCTURED_REPLY, {}

    c, llm = _llm_app(tmp_path, chat)
    sid = llm.add({"label": "测试", "base_url": "https://api.example.com/v1", "model": "m"}, "test-key")
    jid = c.post("/api/jobs", json={"url": "https://b23.tv/x", "workflow": ["structured", "notes"],
                                   "llm_service": sid}).json()["id"]
    assert entered.wait(5)
    try:
        item = c.get("/api/history").json()["items"][0]
        assert item["id"] == jid and item["status"] == "done"
        assert item["title"] == "示例"
        runs = {run["output"]: run for run in item["ai"]}
        assert set(runs) == {"structured", "notes"}
        assert runs["structured"]["status"] == "running"
        assert runs["structured"]["progress"] == {"phase": "generate", "index": 1, "total": 1}
        assert runs["notes"]["status"] == "queued"
        assert runs["notes"]["progress"] is None
        assert c.delete(f"/api/history/{jid}").status_code == 409
    finally:
        release.set()
        for run in c.get(f"/api/jobs/{jid}").json()["ai"]:
            _wait_llm(c, run["id"])


def test_history_keeps_failed_ai(tmp_path):
    c, sid, _ = _ai_app(tmp_path, fail=lambda prompt: True)
    jid = _finished_job(c)
    run = _ai(c, jid, "structured", sid)
    assert run["status"] == "failed"
    item = c.get("/api/history").json()["items"][0]
    assert item["status"] == "done"
    assert item["ai"][0]["id"] == run["id"]
    assert item["ai"][0]["status"] == "failed"
    assert item["ai"][0]["error"] == run["error"]


def test_job_status_keeps_manual_ai_without_restarting_generation(tmp_path, fake_png):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def chat(messages):
        calls.append(messages)
        entered.set()
        assert release.wait(5)
        return STRUCTURED_REPLY, {}

    c, llm = _llm_app(tmp_path, chat)
    sid = llm.add({"label": "测试", "base_url": "https://api.example.com/v1", "model": "m"}, "test-key")
    jid = _finished_job(c)
    rid = c.post(f"/api/jobs/{jid}/ai", json={"output": "structured", "service": sid}).json()["id"]
    assert entered.wait(5)
    try:
        for _ in range(2):
            job = c.get(f"/api/jobs/{jid}").json()
            assert len(job["ai"]) == 1 and job["ai"][0]["id"] == rid
            assert job["ai"][0]["status"] == "running"
            assert job["ai"][0]["progress"] == {"phase": "generate", "index": 1, "total": 1}
        assert len(calls) == 1
    finally:
        release.set()
        _wait_llm(c, rid)
    job = c.get(f"/api/jobs/{jid}").json()
    assert len(job["ai"]) == 1 and job["ai"][0]["status"] == "done"
    assert len(calls) == 1


def test_layout_api_and_preview(tmp_path, fake_png):
    c, _, calls = _ai_app(tmp_path)
    groups = c.get("/api/layouts").json()
    assert set(groups["groups"]) == {"points", "article"}
    assert [t["id"] for t in c.get("/api/layouts?group=article").json()["layouts"]] == ["book", "magazine", "night", "print"]
    bad = c.post("/api/layouts", json={"name": "坏", "html": "{% for x in %}"})
    assert bad.status_code == 422 and "模板有错误" in bad.json()["detail"]
    r = c.post("/api/layouts/preview", json={"html": "<p>{{ title }}</p>", "width": 600})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    r = c.post("/api/layouts/preview", json={"copy_of": "night"})
    assert r.status_code == 200 and fake_png[-1][0] == "night" and not calls


def test_long_job_does_not_block_status(tmp_path):
    c = client(tmp_path, delay=3.0)
    job = c.post("/api/jobs", json={"url": "https://b23.tv/x"}).json()
    started = time.time()
    status = c.get(f"/api/jobs/{job['id']}")
    assert status.status_code == 200 and time.time() - started < 1.0
    assert status.json()["status"] in ("queued", "running")
    wait_done(c, job["id"], timeout=10)


def test_reveal_keys_only_when_allowed(tmp_path):
    reg = _registry(tmp_path)
    for reveal, status in ((True, 200), (False, 403)):
        c = TestClient(create_app(work_dir=tmp_path, run=fake_run_factory(), registry=reg, reveal_keys=reveal))
        assert c.get("/api/capabilities").json() == {"reveal_keys": reveal, "browser_login": reveal, "diagnostics": reveal}
        sid = c.post("/api/engines", json={"kind": "tencent", "label": "腾讯", "model": "16k_zh",
                                           "secrets": {"secret_id": "AKIDx", "secret_key": "skx"}}).json()["id"]
        assert "skx" not in c.get("/api/engines").text                     # list never carries keys
        r = c.get(f"/api/engines/{sid}/secrets")
        assert r.status_code == status
        if reveal:
            assert r.json() == {"secret_id": "AKIDx", "secret_key": "skx"}
            c.put(f"/api/engines/{sid}", json={"secrets": {"secret_key": "sky"}})
            assert c.get(f"/api/engines/{sid}/secrets").json() == {"secret_id": "AKIDx", "secret_key": "sky"}


def test_main_reveals_only_on_loopback():
    from vtx.server import is_loopback

    assert is_loopback("127.0.0.1") and is_loopback("localhost") and is_loopback("::1")
    assert not is_loopback("0.0.0.0") and not is_loopback("192.168.1.5")


def test_frame_templates_applied_to_job_and_ai(tmp_path, fake_png):
    c, sid, _ = _ai_app(tmp_path)
    jid = _finished_job(c)
    job = c.get(f"/api/jobs/{jid}").json()
    assert job["frame"]["header"].startswith("标题：示例\n链接：https://www.bilibili.com/video/BV1")
    assert c.put("/api/frame", json={"footer": "F:{title}", "poster_footer": "P:{title}"}).json()["modified"] == [
        "footer", "poster_footer"]
    assert c.put("/api/frame", json={"bogus": "x"}).status_code == 422
    assert c.get(f"/api/jobs/{jid}").json()["frame"]["footer"] == "F:示例"
    r = _ai(c, jid, "structured", sid)
    assert r["markdown"].endswith("---\nF:示例\n") and fake_png[-1][1] == "P:示例"
    s = _ai(c, jid, "structure", sid)
    assert s["markdown"].endswith("---\nF:示例\n") and fake_png[-1][1] == "F:示例"      # article pages use footer
    assert "footer" not in c.post("/api/frame/footer/reset").json()["modified"]


def test_old_workflow_file_migrated(tmp_path):
    (tmp_path / "workflow.json").write_text(json.dumps({"nodes": [{"kind": "text", "ref": "refine"},
                                                                  {"kind": "poster", "ref": "B"},
                                                                  {"kind": "custom", "ref": "gone"}]}), encoding="utf-8")
    from vtx.configuration import AppPaths
    from vtx.config import ROOT

    AppPaths.for_app(tmp_path, ROOT).migrate_legacy()
    c, sid, _ = _ai_app(tmp_path)
    assert c.get("/api/workflow").json()["nodes"] == ["refine", "structured"]


def test_run_offers_layouts_added_to_template_later(tmp_path, fake_png):
    c, sid, _ = _ai_app(tmp_path)
    jid = _finished_job(c)
    r = _ai(c, jid, "cards", sid)
    assert r["layouts"] == ["cards", "A", "brief"]
    c.put("/api/outputs/cards", json={"layouts": ["cards", "A", "brief", "cover-qa"]})
    assert c.get(f"/api/llm/runs/{r['id']}").json()["layouts"][-1] == "cover-qa"
