"""Write the five result files for one video.

reading.txt   cleaned text, with a header (title, source, engine, billed time)
original.txt  verbatim ASR text, one sentence per line with time
<stem>.srt    verbatim subtitles on the video timeline
edits.json    every accepted deletion (reversible)
issues.json   QC findings with a jump link to the original video time
"""
import json
import re
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Sequence

from vtx.asr import Segment
from vtx.clean import clean
from vtx.qc import Issue
from vtx.project import CREDIT

MAX_NAME = 100
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
SUFFIXES = {"reading": ".reading.txt", "original": ".original.txt", "srt": ".srt",
            "edits": ".edits.json", "issues": ".issues.json"}


def srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600:02}:{s // 60 % 60:02}:{s % 60:02}"


def safe_stem(platform: str, video_id: str, part, title: str) -> str:
    """`<id>[-pN]-<title>`, safe on Windows and Linux, short enough for the longest suffix."""
    head = f"{video_id}-p{part}" if part else video_id
    clean_title = _ILLEGAL.sub("_", unicodedata.normalize("NFC", title or ""))
    clean_title = "".join(c for c in clean_title if unicodedata.category(c)[0] != "S" or c in "+-")
    clean_title = re.sub(r"\s+", " ", clean_title).strip(" .")
    budget = MAX_NAME - max(len(s) for s in SUFFIXES.values()) - len(head) - 1
    stem = f"{head}-{clean_title[:budget]}".rstrip(" .-") if clean_title and budget > 0 else head
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    return stem


def jump_url(meta: dict, seconds: float) -> str:
    t = int(seconds)
    if meta["platform"] == "bilibili":
        return f"https://www.bilibili.com/video/{meta['video_id']}?p={meta.get('part') or 1}&t={t}"
    if meta["platform"] == "youtube":
        return f"https://www.youtube.com/watch?v={meta['video_id']}&t={t}"
    return f"https://www.douyin.com/video/{meta['video_id']}?t={t}"  # douyin ignores t, link still opens the video


def write_outputs(out_dir: Path, segments: Sequence[Segment], issues: Sequence[Issue], meta: dict) -> Dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(meta["platform"], meta["video_id"], meta.get("part"), meta.get("title", ""))
    paths = {k: out_dir / f"{stem}{suffix}" for k, suffix in SUFFIXES.items()}

    reading, edits, prev = [], [], ""
    for line, seg in enumerate(segments):  # `line` = position in original.txt / SRT cue order
        result = clean(seg.text, context_before=prev)
        prev = seg.text
        if result.text.strip():
            reading.append(result.text)
        for e in result.edits:
            edits.append({"segment": seg.index, "line": line, "time": round(seg.start, 2), **asdict(e)})

    billed = meta.get("billed_seconds")
    header = [f"标题：{meta.get('title', '')}", f"链接：{meta.get('url', '')}",
              f"来源：云端识别 {meta.get('engine', '')}" + (f"，计费 {billed} 秒（{billed / 60:.1f} 分钟）" if billed else "") + f" · {CREDIT}",
              f"口语清理：删除 {len(edits)} 处（见 {paths['edits'].name}）；疑点 {len(issues)} 处（见 {paths['issues'].name}）", ""]
    # newline="\n": identical bytes on Windows and Linux (no \r\n translation)
    lf = {"encoding": "utf-8", "newline": "\n"}
    paths["reading"].write_text("\n".join(header) + "\n".join(_paragraphs(reading)) + "\n", **lf)
    paths["original"].write_text(
        "".join(f"[{_clock(s.start)}] {s.text}\n" for s in segments), **lf)
    paths["srt"].write_text("".join(
        f"{n}\n{srt_time(s.start)} --> {srt_time(max(s.end, s.start + 0.001))}\n{s.text}\n\n"
        for n, s in enumerate(segments, 1)), **lf)
    paths["edits"].write_text(json.dumps(edits, ensure_ascii=False, indent=1), **lf)
    paths["issues"].write_text(json.dumps(
        [{**asdict(i), "jump_url": jump_url(meta, i.start)} for i in issues], ensure_ascii=False, indent=1), **lf)
    return paths


def _paragraphs(sentences: List[str], size: int = 6) -> List[str]:
    """Group sentences into paragraphs for reading; no text is changed."""
    return ["".join(sentences[i:i + size]) + "\n" for i in range(0, len(sentences), size)]
