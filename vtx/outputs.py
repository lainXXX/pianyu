"""Output templates (design 4.12): name + kind + prompt + layouts.

kind      model output                           check                      layouts
source    精校: {n, text, removed} per paragraph  refine guard               article group
structure 结构化原文: sentence-id structure       vtx.structure rebuild      article group
points    knowledge JSON                         quote verification         points group
free      Markdown                               none                       article group

Built-ins can be edited, reset and hidden (hidden ones can be shown again); user templates can be
deleted. Old prompts.json entries (text / poster / custom groups) are migrated on first load.
"""
import json
import uuid
from pathlib import Path
from vtx.configuration import atomic_write
from typing import List

from vtx.engines import EngineError

KINDS = {
    "structure": {"label": "原文类 · 结构化", "group": "article", "vars": ("title", "sentences"), "required": "sentences"},
    "source": {"label": "原文类 · 精校", "group": "article", "vars": ("title", "paragraphs"), "required": "paragraphs"},
    "points": {"label": "要点类", "group": "points", "vars": ("title", "text", "max_points"), "required": "text"},
    "free": {"label": "自由类", "group": "article", "vars": ("title", "text"), "required": ""},
}
MAX_TEMPLATE = 20_000


def builtins() -> List[dict]:
    from vtx.builtin_templates import EXTRA
    from vtx.knowledge import PROMPT as POINTS
    from vtx.prompts import _builtins as old
    from vtx.structure import PROMPT as STRUCTURE

    refine = next(p for p in old() if p["id"] == "refine")["template"]
    core = [
        {"id": "structure", "name": "结构化原文", "kind": "structure", "layouts": ["book", "magazine", "night", "print"],
         "prompt": STRUCTURE.replace("{{", "{").replace("}}", "}")},
        {"id": "refine", "name": "精校原文", "kind": "source", "layouts": ["book", "night", "print"], "prompt": refine},
        {"id": "structured", "name": "结构化要点", "kind": "points",
         "layouts": ["A", "B", "brief", "cover-editorial", "cover-qa", "cover-note"],
         "prompt": POINTS.replace("{{", "{").replace("}}", "}")},
    ]
    return core + EXTRA


class OutputStore:
    def __init__(self, path: Path, layouts, legacy: Path = None):
        """layouts: a TemplateStore (to validate layout ids). legacy: old prompts.json to migrate."""
        self.path, self.layouts, self.legacy = path, layouts, legacy

    def _load(self) -> dict:
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
        else:
            data = {"overrides": {}, "hidden": [], "custom": self._migrate()}
            self._save(data)
        for k, v in (("overrides", {}), ("hidden", []), ("custom", [])):
            data.setdefault(k, v)
        return data

    def _migrate(self) -> list:
        """Old user prompts become output templates; built-in overrides of 精校 / 结构化 carry over."""
        if not self.legacy or not self.legacy.exists():
            return []
        try:
            old = json.loads(self.legacy.read_text(encoding="utf-8"))
        except ValueError:
            return []
        kind_of = {"text": ("source", ["book"]), "poster": ("points", ["A"]), "custom": ("free", ["book"])}
        out = []
        for c in old.get("custom") or []:
            kind, lays = kind_of.get(c.get("group", "custom"), ("free", ["book"]))
            out.append({"id": c["id"], "name": c["name"], "kind": kind, "layouts": lays, "prompt": c["template"]})
        return out

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))

    def list(self, include_hidden: bool = False) -> List[dict]:
        data = self._load()
        out = []
        for b in builtins():
            if b["id"] in data["hidden"] and not include_hidden:
                continue
            o = data["overrides"].get(b["id"], {})
            out.append({**b, **o, "builtin": True, "modified": bool(o), "hidden": b["id"] in data["hidden"]})
        out += [{**c, "builtin": False, "modified": False, "hidden": False} for c in data["custom"]]
        return [{**t, "kind_label": KINDS[t["kind"]]["label"]} for t in out]

    def get(self, oid: str) -> dict:
        t = next((x for x in self.list(include_hidden=True) if x["id"] == oid), None)
        if not t:
            raise EngineError(f"输出模板不存在：{oid}", "OUTPUT_NOT_FOUND")
        return t

    def _check(self, fields: dict, kind: str) -> dict:
        out = {}
        if "name" in fields:
            if not (fields["name"] or "").strip():
                raise EngineError("请填写名称")
            out["name"] = fields["name"].strip()[:40]
        if "prompt" in fields:
            p = fields["prompt"] or ""
            if not p.strip():
                raise EngineError("提示词不能为空")
            if len(p) > MAX_TEMPLATE:
                raise EngineError("提示词太长")
            need = KINDS[kind]["required"]
            if need and "{" + need + "}" not in p:
                raise EngineError(f"{KINDS[kind]['label']}的提示词必须包含 {{{need}}}")
            out["prompt"] = p
        if "layouts" in fields:
            lays = [x for x in fields["layouts"] or [] if isinstance(x, str)]
            if not lays:
                raise EngineError("至少选一个版式")
            group = KINDS[kind]["group"]
            for lid in lays:
                if self.layouts.get(lid)["group"] != group:
                    raise EngineError(f"版式“{lid}”不适用于{KINDS[kind]['label']}")
            out["layouts"] = list(dict.fromkeys(lays))
        return out

    def add(self, fields: dict, copy_of: str = "") -> str:
        base = self.get(copy_of) if copy_of else None
        kind = fields.get("kind") or (base["kind"] if base else "free")
        if kind not in KINDS:
            raise EngineError("类型只能是 structure / source / points / free")
        if base and base["kind"] != kind:
            raise EngineError("复制时不能改类型")
        t = {"kind": kind, "name": f"{base['name']} 副本" if base else "",
             "prompt": base["prompt"] if base else "", "layouts": base["layouts"] if base else
             (["A"] if kind == "points" else ["book"])}
        t.update(self._check({**t, **{k: v for k, v in fields.items() if k != "kind"}}, kind))
        t["id"] = "o-" + uuid.uuid4().hex[:8]
        data = self._load()
        data["custom"].append(t)
        self._save(data)
        return t["id"]

    def update(self, oid: str, fields: dict) -> None:
        cur = self.get(oid)
        clean = self._check(fields, cur["kind"])
        data = self._load()
        if cur["builtin"]:
            data["overrides"].setdefault(oid, {}).update(clean)
        else:
            next(c for c in data["custom"] if c["id"] == oid).update(clean)
        self._save(data)

    def layout_outputs(self, lid: str) -> List[str]:
        return [o["id"] for o in self.list(include_hidden=True) if lid in o["layouts"]]

    def prepare_bindings(self, lid: str, group: str, selected: List[str]) -> dict:
        """Validate the whole reverse edit before writing either configuration file."""
        all_outputs = {o["id"]: o for o in self.list(include_hidden=True)}
        chosen = set(selected)
        for oid in chosen:
            if oid not in all_outputs:
                raise EngineError(f"输出风格不存在：{oid}", "OUTPUT_NOT_FOUND")
            if KINDS[all_outputs[oid]["kind"]]["group"] != group:
                raise EngineError("版式与输出风格不兼容")
        data = self._load()
        for oid, cur in all_outputs.items():
            lays = list(cur["layouts"]) if oid in chosen else [x for x in cur["layouts"] if x != lid]
            if oid in chosen and lid not in lays:
                lays.append(lid)
            if not lays:
                raise EngineError(f"「{cur['name']}」至少需要一个版式")
            if lays == cur["layouts"]:
                continue
            if cur["builtin"]:
                data["overrides"].setdefault(oid, {})["layouts"] = lays
            else:
                next(c for c in data["custom"] if c["id"] == oid)["layouts"] = lays
        return data

    def reset(self, oid: str) -> None:
        if not self.get(oid)["builtin"]:
            raise EngineError("只有内置模板可以恢复默认")
        data = self._load()
        data["overrides"].pop(oid, None)
        self._save(data)

    def hide(self, oid: str, hidden: bool = True) -> None:
        if not self.get(oid)["builtin"]:
            raise EngineError("自建模板请直接删除")
        data = self._load()
        data["hidden"] = [h for h in data["hidden"] if h != oid] + ([oid] if hidden else [])
        self._save(data)

    def delete(self, oid: str) -> None:
        if self.get(oid)["builtin"]:
            raise EngineError("内置模板不能删除，可以隐藏")
        data = self._load()
        data["custom"] = [c for c in data["custom"] if c["id"] != oid]
        self._save(data)
