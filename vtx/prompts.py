"""Prompt templates, in three groups (design 4.11):

text    faithful rewrites of the transcript (built-in: 精校原文). Always pass the refine guard.
        Variables {title}, {paragraphs}; the reply format (JSON array of {n, text, removed}) is fixed.
poster  structured-knowledge prompts used by poster templates (built-in: 结构化要点).
        Variables {title}, {text}, {max_points}; the reply format (the knowledge JSON) is fixed.
custom  free prompts, no guard. Variables {title}, {text}; a template without {text} gets it appended.

Substitution is a single pass over known variables only, so JSON examples in a prompt and a
transcript that itself contains "{text}" are left alone.
"""
import json
import re
import uuid
from pathlib import Path
from typing import List

from vtx.engines import EngineError

GROUPS = {
    "text": {"label": "文本", "required": ("paragraphs",), "vars": ("title", "paragraphs")},
    "poster": {"label": "图文", "required": ("text",), "vars": ("title", "text", "max_points")},
    "custom": {"label": "自定义", "required": (), "vars": ("title", "text")},
}
_VAR = re.compile(r"\{(\w+)\}")


def _builtins() -> List[dict]:
    from vtx.knowledge import PROMPT as STRUCTURED
    from vtx.refine import PROMPT as REFINE

    plain = lambda s: s.replace("{{", "{").replace("}}", "}")   # str.format escapes -> literal braces
    return [{"id": "refine", "group": "text", "name": "精校原文", "template": plain(REFINE)},
            {"id": "structured", "group": "poster", "name": "结构化要点", "template": plain(STRUCTURED)}]


def render(template: str, **values) -> str:
    """values: the group's variables. {text} is appended when a custom prompt does not mention it."""
    if "text" in values and "{text}" not in template:
        template = template.rstrip() + "\n\n{text}"
    return _VAR.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


class PromptStore:
    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        data.setdefault("overrides", {})
        data.setdefault("custom", [])
        for c in data["custom"]:          # prompts saved before groups existed were free prompts
            c.setdefault("group", "custom")
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def list(self, group: str = "") -> List[dict]:
        data = self._load()
        out = []
        for b in _builtins():
            o = data["overrides"].get(b["id"])
            out.append({**b, **(o or {}), "group": b["group"], "builtin": True, "modified": bool(o)})
        out += [{**c, "builtin": False, "modified": False} for c in data["custom"]]
        return [p for p in out if not group or p["group"] == group]

    def get(self, pid: str) -> dict:
        p = next((x for x in self.list() if x["id"] == pid), None)
        if not p:
            raise EngineError(f"提示词不存在：{pid}", "PROMPT_NOT_FOUND")
        return p

    @staticmethod
    def _check(group: str, name: str, template: str) -> None:
        if group not in GROUPS:
            raise EngineError(f"未知的提示词类别：{group}")
        if not (name or "").strip():
            raise EngineError("请填写提示词名称")
        if not (template or "").strip():
            raise EngineError("提示词内容不能为空")
        if len(template) > 20000:
            raise EngineError("提示词过长（最多 20000 字）")
        for var in GROUPS[group]["required"]:
            if "{" + var + "}" not in template:
                raise EngineError(f"{GROUPS[group]['label']}提示词必须包含 {{{var}}}（文本放在这里）")

    def add(self, name: str, template: str, group: str = "custom") -> str:
        self._check(group, name, template)
        data = self._load()
        pid = "p-" + uuid.uuid4().hex[:8]
        data["custom"].append({"id": pid, "group": group, "name": name.strip(), "template": template})
        self._save(data)
        return pid

    def update(self, pid: str, name: str, template: str) -> None:
        group = self.get(pid)["group"]
        self._check(group, name, template)
        data = self._load()
        if any(b["id"] == pid for b in _builtins()):
            data["overrides"][pid] = {"name": name.strip(), "template": template}
        else:
            next(c for c in data["custom"] if c["id"] == pid).update(name=name.strip(), template=template)
        self._save(data)

    def reset(self, pid: str) -> None:
        if not any(b["id"] == pid for b in _builtins()):
            raise EngineError("只有内置提示词可以恢复默认")
        data = self._load()
        data["overrides"].pop(pid, None)
        self._save(data)

    def delete(self, pid: str) -> None:
        if any(b["id"] == pid for b in _builtins()):
            raise EngineError("内置提示词不能删除，可以修改或恢复默认")
        data = self._load()
        before = len(data["custom"])
        data["custom"] = [c for c in data["custom"] if c["id"] != pid]
        if len(data["custom"]) == before:
            raise EngineError(f"提示词不存在：{pid}", "PROMPT_NOT_FOUND")
        self._save(data)
