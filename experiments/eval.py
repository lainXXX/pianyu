"""Score sources against human references (used by T7).

Reference file: experiments/refs/<sample>.json
    [{"start": 0.0, "end": 60.0, "text": "人工核对后的文字……"}, ...]
Each range is scored separately against the text every source produced inside it.

Usage: python experiments/eval.py
"""
import json
import re
from pathlib import Path
from typing import List

from vtx.textnorm import error_rate, number_errors, tokens

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {  # label -> how to load [(start, end, text)]
    "paraformer-v2": lambda sid, wd: _json_segments(ROOT / "experiments/asr/dashscope_paraformer-v2" / f"{sid}.json"),
    "sensevoice(参考)": lambda sid, wd: _json_segments(ROOT / "experiments/asr/sense_voice" / f"{sid}.json"),
    "B站AI字幕": lambda sid, wd: _srt_segments(wd / "sub.ai-zh.srt") if wd else [],
    "B站CC字幕": lambda sid, wd: _srt_segments(wd / "sub.zh-CN.srt") if wd else [],
}


def _json_segments(path: Path):
    if not path.exists():
        return []
    return [(s["start"], s["end"], s["text"]) for s in json.loads(path.read_text(encoding="utf-8"))]


def _srt_time(t: str) -> float:
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _srt_segments(path: Path):
    if not path.exists():
        return []
    out = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8").strip()):
        lines = block.splitlines()
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = (x.strip() for x in lines[1].split("-->"))
            out.append((_srt_time(a), _srt_time(b), " ".join(lines[2:])))
    return out


def text_in_range(segments, start: float, end: float) -> str:
    """Segments whose midpoint lies inside [start, end): each segment counted once across ranges."""
    return "".join(t for a, b, t in segments if start <= (a + b) / 2 < end)


def score(sample_id: str, refs: List[dict], work_dir) -> List[dict]:
    rows = []
    for label, load in SOURCES.items():
        segments = load(sample_id, work_dir)
        if not segments:
            continue
        ref_tokens = hyp_errors = 0
        numbers = []
        for r in refs:
            hyp = text_in_range(segments, r["start"], r["end"])
            n = len(tokens(r["text"]))
            ref_tokens += n
            hyp_errors += round(error_rate(r["text"], hyp) * n)
            numbers += number_errors(r["text"], hyp)
        rows.append({"sample": sample_id, "source": label, "ref_tokens": ref_tokens,
                     "error_rate": round(hyp_errors / ref_tokens, 4) if ref_tokens else None,
                     "number_errors": numbers,
                     "minutes": round(sum(r["end"] - r["start"] for r in refs) / 60, 1)})
    return rows


def work_dir_for(sample_id: str):
    import yaml

    from vtx.target import resolve

    samples = yaml.safe_load((ROOT / "experiments/samples.yaml").read_text(encoding="utf-8"))["samples"]
    url = next(s["url"] for s in samples if s["id"] == sample_id)
    t = resolve(url)
    return ROOT / "work" / f"{t.platform}-{t.video_id}-p{t.part or 0}"


if __name__ == "__main__":
    for ref_file in sorted((ROOT / "experiments/refs").glob("*.json")):
        sid = ref_file.stem
        for row in score(sid, json.loads(ref_file.read_text(encoding="utf-8")), work_dir_for(sid)):
            print(json.dumps(row, ensure_ascii=False))
