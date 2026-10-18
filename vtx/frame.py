"""Editable header / footer templates for exports and posters (design 4.11).

Variables: {title} {author} {url} {platform} {duration} {engine}.
A part in [square brackets] is dropped when any variable inside it is empty, e.g. "[@{author} ]".
A line whose variable (outside brackets) is empty is dropped. Unknown {names} are kept as typed.
{url} is the clean video link (tracking parameters removed).
"""
import json
import re
from pathlib import Path
from vtx.configuration import atomic_write
from vtx.project import CREDIT

PLATFORMS = {"bilibili": "B站", "douyin": "抖音", "youtube": "YouTube"}
VARIABLES = ("title", "author", "url", "platform", "duration", "engine")
DEFAULTS = {
    # top of TXT / Markdown exports of the reading text
    "header": "标题：{title}\n[作者：{author}\n]链接：{url}\n来源：{platform} · 时长 {duration} · 识别 {engine}",
    # end of every AI text / Markdown export
    "footer": "来源：{platform} [@{author} ]《{title}》 {url}\n内容版权归原作者，AI 整理可能有误，请以原视频为准\n" + CREDIT,
    # bottom of knowledge posters: short, each line is cut to one line in the image
    "poster_footer": "来源：{platform} [@{author} ]《{title}》 内容版权归原作者，AI 整理可能有误\n{url}\n" + CREDIT,
}
LABELS = {"header": "导出页眉（阅读稿 TXT / Markdown）", "footer": "导出页脚（AI 文本 / Markdown）",
          "poster_footer": "图文页脚（知识图底部）"}
MAX_LEN = 2000
_VAR = re.compile(r"\{(\w+)\}")
_GROUP = re.compile(r"\[([^\[\]]*)\]")


def clean_url(meta: dict) -> str:
    vid, platform = meta.get("video_id") or "", meta.get("platform")
    if platform == "bilibili" and vid:
        part = meta.get("part") or 1
        return f"https://www.bilibili.com/video/{vid}" + (f"?p={part}" if part != 1 else "")
    if platform == "douyin" and vid:
        return f"https://www.douyin.com/video/{vid}"
    if platform == "youtube" and vid:
        return f"https://www.youtube.com/watch?v={vid}"
    return meta.get("url") or ""


def duration_text(seconds) -> str:
    if not seconds:
        return ""
    s = int(round(seconds))
    h, m, sec = s // 3600, s // 60 % 60, s % 60
    return (f"{h} 小时 {m} 分" if h else f"{m} 分 {sec} 秒") if (h or m) else f"{sec} 秒"


def values(meta: dict) -> dict:
    return {"title": meta.get("title") or "", "author": meta.get("author") or "", "url": clean_url(meta),
            "platform": PLATFORMS.get(meta.get("platform"), meta.get("platform") or ""),
            "duration": duration_text(meta.get("duration")),
            "engine": meta.get("service_label") or meta.get("engine") or ""}


def render(template: str, meta: dict) -> str:
    vals = values(meta)

    def fill(text: str) -> str:
        return _VAR.sub(lambda m: vals.get(m.group(1), m.group(0)), text)

    def empty_var(text: str) -> bool:
        return any(m.group(1) in vals and not vals[m.group(1)] for m in _VAR.finditer(text))

    text = _GROUP.sub(lambda m: "" if empty_var(m.group(1)) else fill(m.group(1)), template)
    return "\n".join(fill(line) for line in text.split("\n") if not empty_var(line)).strip("\n")


class FrameStore:
    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def get(self) -> dict:
        saved = self._load()
        return {k: saved.get(k, v) for k, v in DEFAULTS.items()}

    def public(self) -> dict:
        saved = self._load()
        return {"templates": self.get(), "defaults": DEFAULTS, "labels": LABELS, "variables": list(VARIABLES),
                "modified": [k for k in DEFAULTS if k in saved]}

    def update(self, templates: dict) -> None:
        saved = self._load()
        for k, v in templates.items():
            if k not in DEFAULTS:
                raise ValueError(f"未知的模板：{k}")
            if len(v or "") > MAX_LEN:
                raise ValueError(f"模板过长（最多 {MAX_LEN} 字）")
            saved[k] = v or ""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(saved, ensure_ascii=False, indent=1).encode("utf-8"))

    def reset(self, name: str) -> None:
        saved = self._load()
        saved.pop(name, None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(saved, ensure_ascii=False, indent=1).encode("utf-8"))

    def render_all(self, meta: dict) -> dict:
        return {k: render(t, meta) for k, t in self.get().items()}
