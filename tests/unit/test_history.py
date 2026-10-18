"""History acceptance: finished jobs and AI runs survive a restart; old job dirs are rebuilt."""
import json

from fastapi.testclient import TestClient

from vtx import history
from vtx.server import create_app
from tests.unit.test_server import (_ai, _ai_app, _finished_job, _registry, _wait_llm, fake_png,  # noqa: F401
                                    fake_run_factory, wait_done)


def test_job_and_runs_survive_restart(tmp_path, fake_png):
    c, sid, calls = _ai_app(tmp_path)
    jid = _finished_job(c)
    text = _ai(c, jid, "refine", sid)
    poster = _ai(c, jid, "structured", sid)
    c.post(f"/api/llm/runs/{poster['id']}/render/B")
    assert (tmp_path / "jobs" / jid / "job.json").exists()
    n_calls = len(calls)

    # a fresh app over the same work dir = a server restart
    c2, _, calls2 = _ai_app(tmp_path)
    items = c2.get("/api/history").json()["items"]
    assert [i["id"] for i in items] == [jid] and items[0]["title"] == "示例"
    assert {r["kind"] for r in items[0]["ai"]} == {"source", "points"}
    job = c2.get(f"/api/jobs/{jid}").json()
    assert job["status"] == "done" and job["result"]["meta"]["title"] == "示例"
    assert c2.get(f"/api/jobs/{jid}/files/reading").text == "reading 内容"
    assert c2.get(f"/api/llm/runs/{text['id']}").json()["content"] == text["content"]
    restored = c2.get(f"/api/llm/runs/{poster['id']}").json()
    assert restored["renders"] == {"A": {"pages": 1}, "B": {"pages": 1}}
    assert c2.get(f"/api/llm/runs/{poster['id']}/files/B/1").content == b"\x89PNG"
    # the same points prompt is reused after restart: no new LLM call
    again = _ai(c2, jid, "structured", sid)
    assert again["cached"] and not calls2 and len(calls) == n_calls


def test_failed_job_remains_in_history_with_original_error(tmp_path):
    from vtx.fetch import FetchError

    c = TestClient(create_app(work_dir=tmp_path, run=fake_run_factory(fail=FetchError("dead", "x")),
                              registry=_registry(tmp_path)))
    jid = c.post("/api/jobs", json={"url": "https://b23.tv/x"}).json()["id"]
    wait_done(c, jid)
    items = c.get("/api/history").json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == jid and items[0]["status"] == "failed"
    assert items[0]["title"] == "https://b23.tv/x"
    assert items[0]["error"] == c.get(f"/api/jobs/{jid}").json()["error"]
    assert items[0]["error"]["category"] == "dead"
    assert not (tmp_path / "jobs" / jid / "job.json").exists()


def test_bad_source_failure_survives_restart_without_generation(tmp_path):
    c, sid, calls = _ai_app(tmp_path)
    jid = _finished_job(c)
    (tmp_path / "jobs" / jid / "x.original.txt").write_text("", encoding="utf-8")
    failed = _ai(c, jid, "notes", sid)
    assert failed["status"] == "failed" and not calls
    c2, _, calls2 = _ai_app(tmp_path)
    restored = c2.get(f"/api/llm/runs/{failed['id']}").json()
    assert restored["error"]["code"] == "LLM_BAD_SOURCE"
    latest = c2.get("/api/history").json()["items"][0]["ai"][0]
    assert latest["id"] == failed["id"] and latest["status"] == "failed"
    assert not calls2


def _old_job_dir(root):
    d = root / "jobs" / "abc123"
    d.mkdir(parents=True)
    stem = "BV1xx-p2-标题"
    (d / f"{stem}.reading.txt").write_text(
        "标题：标题\n链接：https://www.bilibili.com/video/BV1xx?p=2\n"
        "来源：云端识别 dashscope/paraformer-v2，计费 60 秒（1.0 分钟）\n口语清理：删除 1 处；疑点 0 处\n\n正文\n", encoding="utf-8")
    (d / f"{stem}.original.txt").write_text("[00:00:01] 正文\n", encoding="utf-8")
    (d / f"{stem}.srt").write_text("1\n00:00:01,000 --> 00:01:02,500\n正文\n", encoding="utf-8")
    (d / f"{stem}.edits.json").write_text(json.dumps([{"old": "嗯"}]), encoding="utf-8")
    (d / f"{stem}.issues.json").write_text("[]", encoding="utf-8")
    return d


def test_old_job_dir_rebuilt_from_files(tmp_path):
    _old_job_dir(tmp_path)
    (tmp_path / "jobs" / "broken").mkdir()                 # no result files: skipped, not an error
    jobs, runs = history.load(tmp_path / "jobs")
    assert list(jobs) == ["abc123"] and runs == {}
    m = jobs["abc123"]["result"]["meta"]
    assert (m["platform"], m["video_id"], m["part"], m["title"]) == ("bilibili", "BV1xx", 2, "标题")
    assert m["engine"] == "dashscope/paraformer-v2" and m["billed_seconds"] == 60 and m["duration"] == 62.5
    assert jobs["abc123"]["result"]["edits"] == 1
    c = TestClient(create_app(work_dir=tmp_path, run=fake_run_factory(), registry=_registry(tmp_path)))
    assert "正文" in c.get("/api/jobs/abc123/files/reading").text


def test_corrupt_job_json_skipped(tmp_path):
    d = _old_job_dir(tmp_path)
    (d / "job.json").write_text("{not json", encoding="utf-8")
    assert history.load(tmp_path / "jobs") == ({}, {})


def test_old_ai_runs_upgraded(tmp_path):
    d = _old_job_dir(tmp_path)
    (d / "ai").mkdir()
    (d / "ai" / "r1.json").write_text(json.dumps({"id": "r1", "job": "abc123", "kind": "refine", "status": "done",
                                                  "paragraphs": []}), encoding="utf-8")
    (d / "ai" / "r2.json").write_text(json.dumps({"id": "r2", "job": "abc123", "kind": "poster", "status": "done",
                                                  "files": {"poster_A_png": "r2-poster-A.png", "poster_A_html": "r2-poster-A.html",
                                                            "poster_B_png": "r2-poster-B.png"}}), encoding="utf-8")
    _, runs = history.load(tmp_path / "jobs")
    assert (runs["r1"]["kind"], runs["r1"]["output"], runs["r1"]["name"]) == ("source", "refine", "精校原文")
    assert runs["r2"]["kind"] == "points" and runs["r2"]["renders"]["A"]["pngs"][0].endswith("r2-poster-A.png")
