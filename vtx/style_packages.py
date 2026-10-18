"""Portable, credential-free output styles with their independent layouts."""
import json
import re
import uuid
from html.parser import HTMLParser

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from jinja2 import meta, nodes
from starlette.concurrency import run_in_threadpool

from vtx.api.settings import engine_error
from vtx.configuration import atomic_write
from vtx.engines import EngineError
from vtx.outputs import KINDS
from vtx.poster_templates import TemplateStore, _ENV, render, sample_context, sanitize

MAX_BYTES = 1024 * 1024
STYLE_FIELDS = {"name", "kind", "prompt", "layouts"}
LAYOUT_FIELDS = {"id", "name", "group", "html", "width", "height_mode"}
_FILTERS = {"e", "escape", "safe", "upper", "lower", "title", "trim", "default", "d", "length",
            "count", "int", "float", "round", "first", "last", "truncate", "replace", "format"}


class _ContentContainer(HTMLParser):
    found = False

    def handle_starttag(self, tag, attrs):
        if "content" in (dict(attrs).get("class") or "").split():
            self.found = True


def _safe_expressions(tree):
    """Keep portable templates declarative; the sandbox alone does not bound resource use."""
    for node in tree.find_all(nodes.Node):
        if isinstance(node, (nodes.Mul, nodes.Pow, nodes.Macro, nodes.Assign, nodes.AssignBlock)):
            return False
        if isinstance(node, nodes.Getattr) and node.attr.startswith("_"):
            return False
        if isinstance(node, nodes.Call):
            if not isinstance(node.node, nodes.Getattr) or node.node.attr not in {"startswith", "endswith"}:
                return False
        if isinstance(node, nodes.Const):
            if isinstance(node.value, str) and len(node.value) > 4096:
                return False
            if isinstance(node.value, (int, float)) and not -10000 <= node.value <= 10000:
                return False
        if isinstance(node, nodes.Filter):
            if node.name not in _FILTERS:
                return False
            if node.name == "replace":
                # Expanding replacements can allocate exponentially before rendering yields.
                if (len(node.args) < 2 or not all(isinstance(arg, nodes.Const) and isinstance(arg.value, str)
                                                  for arg in node.args[:2])
                        or not node.args[0].value or len(node.args[1].value) > len(node.args[0].value)):
                    return False
            if node.name == "format":
                if (not isinstance(node.node, nodes.Const) or not isinstance(node.node.value, str)
                        or "*" in node.node.value or any(int(n) > 200 for n in re.findall(r"\d+", node.node.value))):
                    return False

    def loops(parent, permitted, depth=0):
        for child in parent.iter_child_nodes():
            if isinstance(child, nodes.For):
                root = child.iter
                while isinstance(root, (nodes.Getattr, nodes.Getitem)):
                    root = root.node
                if depth >= 2 or not isinstance(root, nodes.Name) or root.name not in permitted:
                    return False
                if not isinstance(child.target, nodes.Name) or not loops(child, {child.target.name}, depth + 1):
                    return False
            elif not loops(child, permitted, depth):
                return False
        return True

    return loops(tree, {"points", "left", "right", "quotes", "blocks", "footer_lines"})


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != expected:
        raise EngineError("风格包字段不完整或包含未知字段")


def validate_package(package, outputs):
    """Validate the whole package without touching either configuration file."""
    _keys(package, {"format", "version", "style", "layouts"})
    if package["format"] != "pianyu-style" or type(package["version"]) is not int or package["version"] != 1:
        raise EngineError("不支持的风格包格式或版本")
    style, layouts = package["style"], package["layouts"]
    _keys(style, STYLE_FIELDS)
    if not all(isinstance(style[k], str) for k in ("name", "kind", "prompt")) or style["kind"] not in KINDS:
        raise EngineError("无效的风格名称、类型或提示词")
    if not isinstance(layouts, list) or not 1 <= len(layouts) <= 10:
        raise EngineError("风格包需要 1～10 个版式")
    refs = style["layouts"]
    if not isinstance(refs, list) or not all(isinstance(x, str) for x in refs) or len(set(refs)) != len(refs):
        raise EngineError("无效的版式引用")
    kind = KINDS[style["kind"]]
    if set(re.findall(r"\{(\w+)\}", style["prompt"])) - set(kind["vars"]):
        raise EngineError("提示词包含不适用的变量")
    clean_style = {"kind": style["kind"], **outputs._check({k: style[k] for k in ("name", "prompt")}, style["kind"])}
    clean_layouts = []
    for layout in layouts:
        _keys(layout, LAYOUT_FIELDS)
        if not all(isinstance(layout[k], str) for k in LAYOUT_FIELDS - {"width"}) or type(layout["width"]) is not int:
            raise EngineError("无效的版式字段类型")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", layout["id"]):
            raise EngineError("无效的版式标识")
        if layout["group"] != kind["group"]:
            raise EngineError("版式与输出风格不兼容")
        if sanitize(layout["html"]) != layout["html"]:
            raise EngineError("版式不能包含脚本、外部资源或交互控件")
        try:
            tree = _ENV.parse(layout["html"])
            unknown = meta.find_undeclared_variables(tree) - set(sample_context(layout["group"]))
        except Exception as exc:
            raise EngineError("版式语法无效") from exc
        if unknown:
            raise EngineError("版式包含不适用的变量")
        if not _safe_expressions(tree):
            raise EngineError("版式包含不安全的表达式")
        clean = TemplateStore._clean({k: layout[k] for k in LAYOUT_FIELDS - {"id", "group"}}, layout["group"])
        if layout["group"] == "article":
            container = _ContentContainer()
            container.feed(render(clean["html"], sample_context("article")))
            if not container.found or "blocks" not in meta.find_undeclared_variables(tree):
                raise EngineError('文章版式需要 class="content" 容器和 blocks 正文')
        elif not meta.find_undeclared_variables(tree) & {"points", "left", "right"}:
            raise EngineError("要点版式需要 points、left 或 right 正文")
        clean_layouts.append({"id": layout["id"], "group": layout["group"], **clean})
    ids = [layout["id"] for layout in clean_layouts]
    if len(set(ids)) != len(ids) or set(ids) != set(refs):
        raise EngineError("版式引用必须完整且不能重复")
    return clean_style, clean_layouts, refs


def import_package(package, outputs, templates, config_lock):
    style, layouts, refs = validate_package(package, outputs)
    with config_lock:
        mapping = {layout["id"]: "t-" + uuid.uuid4().hex for layout in layouts}
        oid = "o-" + uuid.uuid4().hex
        # Loading may migrate an old configuration; do so before the transaction snapshot.
        output_data, layout_data = outputs._load(), templates._load()
        before = {store.path: store.path.read_bytes() if store.path.exists() else None for store in (outputs, templates)}
        output_data["custom"].append({**style, "id": oid, "layouts": [mapping[ref] for ref in refs]})
        layout_data["custom"].extend({**layout, "id": mapping[layout["id"]]} for layout in layouts)
        try:
            templates._save(layout_data)
            outputs._save(output_data)
        except Exception:
            for path, content in before.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, content)
            raise
        return {"output": oid, "layouts": [mapping[ref] for ref in refs]}


def export_package(oid, outputs, templates, config_lock):
    with config_lock:
        style = outputs.get(oid)
        layouts = [templates.get(lid) for lid in style["layouts"]]
        # Local portable IDs reveal neither config metadata nor machine paths.
        mapping = {layout["id"]: f"layout-{n}" for n, layout in enumerate(layouts, 1)}
        return {"format": "pianyu-style", "version": 1,
                "style": {**{key: style[key] for key in STYLE_FIELDS - {"layouts"}},
                          "layouts": [mapping[lid] for lid in style["layouts"]]},
                "layouts": [{**{key: layout[key] for key in LAYOUT_FIELDS - {"id"}},
                             "id": mapping[layout["id"]]} for layout in layouts]}


def register_style_packages(app, outputs, templates, auth, config_lock):
    @app.post("/api/style-packages/import", dependencies=[Depends(auth)])
    async def import_style(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "请使用 JSON 风格包")
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > MAX_BYTES):
            raise HTTPException(413, "风格包不能超过 1 MiB")
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > MAX_BYTES:
                raise HTTPException(413, "风格包不能超过 1 MiB")
            raw.extend(chunk)
        try:
            package = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(422, "请选择有效的 JSON 风格包")
        try:
            return await run_in_threadpool(import_package, package, outputs, templates, config_lock)
        except EngineError as exc:
            raise engine_error(exc)

    @app.get("/api/outputs/{oid}/package", dependencies=[Depends(auth)])
    def download_style(oid: str):
        try:
            package = export_package(oid, outputs, templates, config_lock)
        except EngineError as exc:
            raise engine_error(exc)
        return Response(json.dumps(package, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": 'attachment; filename="pianyu-style.json"'})
