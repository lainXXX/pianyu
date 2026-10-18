"""History: finished jobs and AI runs survive a server restart.

Layout under work/jobs/<job_id>/:
  job.json          the finished job (file paths stored as names relative to the job dir)
  ai/<run_id>.json  one finished AI run (rendered image / HTML paths likewise relative to ai/)
Job dirs written before history existed have no job.json; they are rebuilt from their result files.
"""
import json
import os
import re
from pathlib import Path

from vtx.output import SUFFIXES


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def save_job(job_dir: Path, job: dict) -> None:
    data = {k: job[k] for k in ("id", "url", "status", "engine", "created_at", "result") if k in job}
    data["files"] = {k: Path(v).name for k, v in job.get("files", {}).items()}
    _write(job_dir / "job.json", data)


def save_run(job_dir: Path, rec: dict) -> None:
    data = dict(rec)
    data["renders"] = {lid: {"pngs": [Path(x).name for x in r["pngs"]], "html": Path(r["html"]).name if r.get("html") else ""}
                       for lid, r in (rec.get("renders") or {}).items()}
    _write(job_dir / "ai" / f"{rec['id']}.json", data)


def _platform(url: str) -> str:
    for host, name in (("bilibili", "bilibili"), ("b23.tv", "bilibili"), ("douyin", "douyin"),
                       ("youtu", "youtube")):
        if host in url:
            return name
    return ""


def _rebuild(job_dir: Path) -> dict | None:
    """Job dir from before history existed: recover what the page needs from the result files."""
    files = {}
    for kind, suffix in SUFFIXES.items():
        hit = sorted(job_dir.glob(f"*{suffix}"))
        if not hit:
            return None
        files[kind] = hit[0].name
    head = dict(l.split("：", 1) for l in (job_dir / files["reading"]).read_text(encoding="utf-8").splitlines()[:4]
                if "：" in l)
    source = head.get("来源", "")
    engine = re.sub(r"^云端识别\s*", "", source.split("，")[0])
    billed = re.search(r"计费 (\d+) 秒", source)
    stem = files["reading"][: -len(SUFFIXES["reading"])]
    vid = re.match(r"([^-]+)(?:-p(\d+))?", stem)
    srt = (job_dir / files["srt"]).read_text(encoding="utf-8")
    ends = re.findall(r"--> (\d\d):(\d\d):(\d\d),(\d\d\d)", srt)
    duration = (lambda h, m, s, ms: int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000)(*ends[-1]) if ends else 0
    count = lambda kind: len(json.loads((job_dir / files[kind]).read_text(encoding="utf-8")))
    url = head.get("链接", "")
    meta = {"platform": _platform(url), "video_id": vid.group(1), "part": int(vid.group(2)) if vid.group(2) else None,
            "title": head.get("标题", ""), "url": url, "duration": duration, "engine": engine,
            "billed_seconds": int(billed.group(1)) if billed else None}
    return {"id": job_dir.name, "url": url, "status": "done", "created_at": (job_dir / files["reading"]).stat().st_mtime,
            "files": files, "result": {"meta": meta, "issues": count("issues"), "edits": count("edits"),
                                       "cached_asr": billed is None}}


def _upgrade_run(rec: dict) -> None:
    """Runs saved before design 4.12 become output-template runs, so the page can show them."""
    kind = rec.get("kind")
    files = rec.get("files") or {}
    if kind in ("refine", "text"):
        rec.update(kind="source", output=rec.get("ref") or "refine", content=rec.get("paragraphs") or [],
                   name=rec.get("name") or "精校原文", layouts=["book"])
    elif kind == "poster":
        png = files.get("png") or files.get("poster_A_png")
        html = files.get("html") or files.get("poster_A_html", "")
        lid = rec.get("ref") or "A"
        rec.update(kind="points", output="structured", content=rec.get("data"), layouts=[lid],
                   name=rec.get("name") or "结构化要点",
                   renders={lid: {"pngs": [png], "html": html}} if png else {})
    elif kind in ("custom", "structured"):
        rec.update(kind="free", output=rec.get("ref") or "", layouts=["book"], name=rec.get("name") or "自定义",
                   markdown_body=rec.get("text") or rec.get("markdown") or "")
    rec.setdefault("renders", {})
    rec.pop("files", None)


def load(jobs_root: Path) -> tuple[dict, dict]:
    """-> (jobs, runs) keyed by id, with file paths made absolute again. Unreadable entries are skipped."""
    jobs, runs = {}, {}
    if not jobs_root.is_dir():
        return jobs, runs
    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        try:
            path = job_dir / "job.json"
            job = json.loads(path.read_text(encoding="utf-8")) if path.exists() else _rebuild(job_dir)
        except (OSError, ValueError, KeyError, IndexError, AttributeError):
            continue
        if not job:
            continue
        job["files"] = {k: str(job_dir / v) for k, v in job.get("files", {}).items()}
        jobs[job["id"]] = job
        for run_path in (job_dir / "ai").glob("*.json"):
            try:
                rec = json.loads(run_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if "files" in rec:          # pre-4.12 runs stored poster files here
                rec["files"] = {k: str(job_dir / "ai" / v) for k, v in rec["files"].items() if v}
            for r in (rec.get("renders") or {}).values():
                r["pngs"] = [str(job_dir / "ai" / x) for x in r["pngs"]]
                r["html"] = str(job_dir / "ai" / r["html"]) if r.get("html") else ""
            _upgrade_run(rec)
            runs[rec["id"]] = rec
    return jobs, runs
