"""URL -> five result files. Shared by the CLI (T11) and the web service (T13)."""
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from vtx import config
from vtx.asr import Segment
from vtx.fetch import fetch
from vtx.output import write_outputs
from vtx.qc import check, vad_activity
from vtx.target import resolve

CACHE_VERSION = 1   # bump when ASR request parameters change


@dataclass
class RunResult:
    files: Dict[str, Path]
    meta: dict
    issues: int
    edits: int
    cached_asr: bool


def parse_srt(path: Path) -> List[tuple]:
    def t(s):
        h, m, rest = s.split(":")
        sec, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = block.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = (x.strip() for x in lines[1].split("-->"))
            out.append((t(a), t(b), " ".join(lines[2:])))
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def asr_cache_key(engine_name: str, audio_sha256: str) -> str:
    """engine_name already encodes service + model (e.g. 'dashscope/fun-asr', 'openai:svc1/whisper-1')."""
    return hashlib.sha256(f"{engine_name}|v{CACHE_VERSION}|{audio_sha256}".encode()).hexdigest()[:16]


def transcribe_cached(engine, wav: Path, cache_dir: Path):
    """ASR results are cached by (engine, params version, audio hash): re-runs are not billed again."""
    path = cache_dir / f"asr-{asr_cache_key(engine.name, _sha256(wav))}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return [Segment(**s) for s in data["segments"]], data["billed_seconds"], True
    segments = engine.transcribe(wav)
    billed = getattr(engine, "last_billed_seconds", 0)
    path.write_text(json.dumps({"engine": engine.name, "billed_seconds": billed,
                                "segments": [asdict(s) for s in segments]}, ensure_ascii=False), encoding="utf-8")
    return segments, billed, False


def run(url: str, out_dir: Path, engine=None, settings=None,
        progress: Callable[..., None] = lambda stage, info=None: None) -> RunResult:
    """progress(stage, info=None): info carries title/platform/duration once the media is fetched."""
    settings = settings or config.load()
    progress("resolve")
    target = resolve(url)
    progress("fetch")
    fetched = fetch(target, settings)
    progress("asr", {"title": fetched.title, "platform": target.platform, "video_id": target.video_id,
                     "part": target.part, "duration": fetched.duration})
    if engine is None:
        from vtx.asr_cloud import DashScopeEngine

        engine = DashScopeEngine()
    segments, billed, cached = transcribe_cached(engine, fetched.audio, fetched.audio.parent)
    progress("qc")
    second_lang = next((k for k in ("ai-zh", "zh-CN", "zh-Hans", "zh") if k in fetched.subtitles), None)
    second = parse_srt(fetched.subtitles[second_lang]) if second_lang else None
    issues = check(segments, fetched.duration, vad_activity(fetched.audio), second)
    progress("output")
    meta = {"platform": target.platform, "video_id": target.video_id, "part": target.part,
            "title": fetched.title, "author": fetched.author, "url": url, "duration": fetched.duration, "engine": engine.name,
            "billed_seconds": 0 if cached else billed, "second_source": second_lang,
            "segments": len(segments)}
    files = write_outputs(out_dir, segments, issues, meta)
    if second_lang:
        reference = out_dir / "reference.srt"
        reference.write_text(fetched.subtitles[second_lang].read_text(encoding="utf-8"), encoding="utf-8")
        files["reference"] = reference
    edits = len(json.loads(files["edits"].read_text(encoding="utf-8")))
    return RunResult(files, meta, len(issues), edits, cached)
