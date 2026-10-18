"""Poster templates (design 4.11): built-in A / B plus user copies, rendered in a Jinja2 sandbox.

Layouts (design 4.12) come in two groups: "points" (knowledge JSON, this module's context) and
"article" (paginated pages, see vtx.article). A layout is {id, name, group, html, width, height_mode}:
  html         Jinja2 source; variables: title, conclusion, points (heading, body, text, key, detail,
               quotes[quote]), left / right (points split into two balanced columns), quotes (all quotes),
               kicker (platform · duration), footer_lines.
  width        screenshot width in px
  height_mode  "auto" (tall image, capped at 8000 px by dropping trailing points) | "fixed:<px>"
               (for article layouts: the page height used for pagination)
Rendered output is sanitized: no scripts, event handlers, external resources or javascript: URLs.
"""
import json
import re
import uuid
from pathlib import Path
from vtx.configuration import atomic_write
from typing import List, Optional

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

from vtx.engines import EngineError

A_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{{ title }}</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC","Source Han Sans SC",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;background:#e9e5dc}
blockquote{color:#6b5530;background:#fbf7ef;border-left:4px solid #c9a227;border-radius:6px;padding:.5em .8em;margin:.6em 0 0}
ul{margin:.4em 0 0 1.1em}
li{margin:.15em 0}
.poster{width:1080px;background:#f4efe6;color:#2a2622;font-size:30px;line-height:1.7}
.hero{padding:64px 64px 52px;background:linear-gradient(150deg,#2f3e46,#3d5a52)}
.kicker{font-size:24px;letter-spacing:.08em;color:#b9cfc6}
.hero h1{font-size:54px;line-height:1.3;color:#fff;margin-top:10px;font-weight:700}
.concl{margin-top:30px;padding:22px 26px;background:rgba(255,255,255,.1);border-left:6px solid #d9b45a;color:#f3ead6;font-size:31px}
.body{padding:28px 44px 8px}
.pt{padding:22px 20px}
.pt h2{font-size:34px;color:#3d5a52;font-weight:700}
.pt .num{display:inline-block;min-width:1.6em;color:#c9a227}
.pt p{color:#4a443d;margin-top:6px}
.pt.key{background:#fff;border:2px solid #e6dccb;border-radius:16px;margin:14px 0;padding:26px 28px}
.pt.key h2{color:#8a5a12}
.pt.key .tag{font-size:20px;background:#fff3cd;color:#8a5a12;border-radius:6px;padding:2px 10px;margin-left:10px;vertical-align:middle}
.pt ul{color:#4a443d}
footer{color:#a39a8c;font-size:15px;line-height:1.5;padding:26px 64px 34px;border-top:2px solid #e3d9c8;margin-top:18px}
footer div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style></head><body><div class="poster">
<header class="hero"><div class="kicker">{{ kicker }}</div><h1>{{ title }}</h1><div class="concl">{{ conclusion }}</div></header>
<main class="body">
{% for p in points %}<section class="{{ 'pt key' if p.key else 'pt' }}"><h2><span class="num">{{ loop.index }}</span>{{ p.heading }}{% if p.key %}<span class="tag">重点</span>{% endif %}</h2>
<p>{{ p.body }}</p>{% if p.detail %}<ul>{% for d in p.detail %}<li>{{ d }}</li>{% endfor %}</ul>{% endif %}
{% for q in p.quotes %}<blockquote>“{{ q.quote }}”</blockquote>{% endfor %}</section>
{% endfor %}</main>
<footer>{% for line in footer_lines %}<div>{{ line }}</div>{% endfor %}</footer>
</div></body></html>"""

B_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{{ title }}</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC","Source Han Sans SC",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;background:#e9e5dc}
blockquote{color:#6b5530;border-radius:6px;padding:.5em .8em}
ul{margin:.4em 0 0 1.1em}
li{margin:.15em 0}
.poster{width:1600px;background:#f7f6f1;color:#2a2622;font-size:21px;line-height:1.6;padding:44px 52px 28px}
.top{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:28px}
.top h1{font-size:38px;font-weight:700}
.top .kicker{font-size:19px;color:#8a8478}
.map{display:grid;grid-template-columns:1fr 360px 1fr;gap:28px;align-items:center}
.col{display:flex;flex-direction:column;gap:16px}
.center{background:#2f3e46;color:#fff;border-radius:50%;width:360px;height:360px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;text-align:center;padding:44px;box-shadow:0 0 0 12px #dfe9e4}
.center .label{font-size:18px;color:#e0c36a;letter-spacing:.1em}
.center .text{font-size:26px;line-height:1.45;margin-top:10px}
.pt{background:#e4efe9;border-radius:14px;padding:16px 20px}
.left .pt{border-right:6px solid #6f9a8a}
.right .pt{border-left:6px solid #6f9a8a}
.pt h2{font-size:24px;font-weight:700}
.pt p{color:#4a443d;font-size:19px;margin-top:4px}
.pt.key{background:#fdf2e3}
.left .pt.key{border-right-color:#c98a2b}
.right .pt.key{border-left-color:#c98a2b}
.pt ul{font-size:18px;color:#4a443d}
.quotes{display:flex;gap:16px;margin-top:28px}
.quotes blockquote{flex:1;margin:0;background:#fff;border-left:5px solid #c9a227;font-size:19px}
footer{color:#a39a8c;font-size:15px;line-height:1.5;margin-top:24px;padding-top:14px;border-top:1px solid #e3ddd0}
footer div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style></head><body><div class="poster">
<div class="top"><h1>{{ title }}</h1><div class="kicker">{{ kicker }}</div></div>
<div class="map"><div class="col left">
{% for p in left %}<section class="{{ 'pt key' if p.key else 'pt' }}"><h2>{{ p.heading }}</h2><p>{{ p.body }}</p>{% if p.detail %}<ul>{% for d in p.detail %}<li>{{ d }}</li>{% endfor %}</ul>{% endif %}</section>
{% endfor %}</div>
<div class="center"><div class="label">核心结论</div><div class="text">{{ conclusion }}</div></div>
<div class="col right">
{% for p in right %}<section class="{{ 'pt key' if p.key else 'pt' }}"><h2>{{ p.heading }}</h2><p>{{ p.body }}</p>{% if p.detail %}<ul>{% for d in p.detail %}<li>{{ d }}</li>{% endfor %}</ul>{% endif %}</section>
{% endfor %}</div></div>
{% if quotes %}<div class="quotes">{% for q in quotes %}<blockquote>“{{ q.quote }}”</blockquote>{% endfor %}</div>{% endif %}
<footer>{% for line in footer_lines %}<div>{{ line }}</div>{% endfor %}</footer>
</div></body></html>"""

def _builtins() -> List[dict]:
    from vtx.layouts_article import ARTICLE
    from vtx.layouts_points import POINTS

    points = [{"id": "A", "name": "竖版长图", "html": A_HTML, "width": 1080, "height_mode": "auto"},
              {"id": "B", "name": "横版思维导图", "html": B_HTML, "width": 1600, "height_mode": "auto"}] + POINTS
    return [{**t, "group": "points"} for t in points] + [{**t, "group": "article"} for t in ARTICLE]


GROUPS = {"points": "要点版式", "article": "文章版式"}
MAX_HTML = 100_000
# Sample data for previews: no model call needed to see what a template looks like.
SAMPLE = {
    "title": "示例：为什么人类需要不断吃盐", "conclusion": "地球上所有生物都离不开电解质，这是生命从海洋里带出来的底层代码。",
    "points": [
        {"heading": "动物其实更馋盐", "body": "长颈鹿舔钢管、牛圈要放舔砖。", "key": False, "detail": [],
         "quotes": [{"quote": "对牛牛来讲那可是九九成稀罕物。"}]},
        {"heading": "为什么骆驼出门前喂盐", "body": "出远门前直接喂盐，骆驼会大量喝水提前储水。", "key": True,
         "detail": ["做法：喂盐，让骆驼大量喝水", "原因：直接喂水反而越喂越缺水"],
         "quotes": [{"quote": "喝一份水就得喝一份的盐。"}]},
        {"heading": "盐在身体里做什么", "body": "钠离子参与神经信号传递、肌肉收缩和渗透压维持。", "key": True,
         "detail": ["长期缺钠会四肢无力、食欲不振"], "quotes": []},
        {"heading": "生命起源于海洋", "body": "与海水交换电解质是单细胞时代留下的机制。", "key": False, "detail": [], "quotes": []},
    ],
}
SAMPLE_META = {"platform": "bilibili", "video_id": "BV1wFZ8YBEt4", "title": SAMPLE["title"], "author": "示例UP",
               "duration": 197}

_ENV = SandboxedEnvironment(autoescape=True)
# --- sanitizer: applied to the rendered page, so nothing a template (or data) does can add active content
_ACTIVE = r"script|iframe|frame|object|embed|link|base|form|svg|math|template|noscript"
_ACTIVE_BLOCK = re.compile(rf"<\s*({_ACTIVE})\b.*?<\s*/\s*\1\s*>", re.I | re.S)
_ACTIVE_TAG = re.compile(rf"<\s*/?\s*({_ACTIVE})\b[^>]*>|<\s*meta\b[^>]*http-equiv[^>]*>", re.I)
_EVENT = re.compile(r"""\s+on[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_EXTERNAL_ATTR = re.compile(r"""\s+(src|href|srcset|action|formaction|poster|xlink:href)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_CSS_URL = re.compile(r"url\s*\([^)]*\)", re.I)
_IMPORT = re.compile(r"@import[^;]*;?", re.I)


def sanitize(html: str) -> str:
    html = _ACTIVE_TAG.sub("", _ACTIVE_BLOCK.sub("", html))
    html = _EVENT.sub("", html)
    html = _EXTERNAL_ATTR.sub("", html)          # no images / links: posters are self-contained
    html = _CSS_URL.sub("none", html)
    return _IMPORT.sub("", html)


def parse_height(mode: str) -> Optional[int]:
    """None = auto (tall); int = fixed height in px."""
    if mode == "auto":
        return None
    m = re.fullmatch(r"fixed:(\d{3,5})", mode or "")
    if not m or not 300 <= int(m.group(1)) <= 8000:
        raise EngineError("高度只能是 auto 或 fixed:300～8000")
    return int(m.group(1))


def context(data: dict, meta: dict, footer: str, max_points: Optional[int] = None) -> dict:
    from vtx.frame import PLATFORMS, duration_text
    from vtx.poster import balanced_split

    points = data["points"][:max_points] if max_points else data["points"]
    half = balanced_split(points)
    kicker = " · ".join(b for b in (PLATFORMS.get(meta.get("platform"), meta.get("platform") or ""),
                                    duration_text(meta.get("duration"))) if b)
    return {"title": data.get("title") or meta.get("title", ""), "conclusion": data.get("conclusion", ""),
            "points": points, "left": points[:half], "right": points[half:],
            "quotes": [q for p in points for q in p.get("quotes") or []], "kicker": kicker,
            "footer_lines": [l for l in footer.split("\n") if l]}


def sample_context(group: str) -> dict:
    """Example data for previews and save-time checks: no model call needed."""
    if group == "article":
        from vtx.article import context as article_context, markdown_blocks

        title, blocks = markdown_blocks(SAMPLE_ARTICLE)
        return article_context(title, "B站 · @示例UP · 3 分 17 秒", blocks, "来源：示例\nhttps://example.com/v")
    return context(SAMPLE, SAMPLE_META,
                   "来源：B站 @示例UP《示例》 内容版权归原作者\nhttps://www.bilibili.com/video/BV1wFZ8YBEt4")


SAMPLE_ARTICLE = """# 示例：为什么人类需要不断吃盐

## 动物也离不开盐
长颈鹿就经常舔食钢管来获取盐分和矿物质，有时候也会舔人。**其实任何动物都需要摄入盐分。**养过牛的都知道，牛圈里需要放一块舔砖。

## 骆驼为什么出门前喂盐
- 喂盐之后骆驼会大量喝水，提前储水。
- 直接喂水会越喂越缺水。

> 吃一碗粉就得给一碗的钱，喝一份水就得喝一份的盐。
"""


def render(source: str, ctx: dict) -> str:
    try:
        return sanitize(_ENV.from_string(source).render(**ctx))
    except TemplateError as exc:
        raise EngineError(f"模板有错误：{exc}") from exc
    except Exception as exc:    # sandbox SecurityError etc.
        raise EngineError(f"模板无法渲染：{type(exc).__name__}: {exc}") from exc


class TemplateStore:
    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        data.setdefault("overrides", {})
        data.setdefault("custom", [])
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))

    def list(self, group: str = "") -> List[dict]:
        data = self._load()
        out = [{**b, **data["overrides"].get(b["id"], {}), "builtin": True,
                "modified": b["id"] in data["overrides"]} for b in _builtins()]
        out += [{"group": "points", **c, "builtin": False, "modified": False} for c in data["custom"]]
        return [t for t in out if not group or t["group"] == group]

    def get(self, tid: str) -> dict:
        t = next((x for x in self.list() if x["id"] == tid), None)
        if not t:
            raise EngineError(f"图文模板不存在：{tid}", "TEMPLATE_NOT_FOUND")
        return t

    @staticmethod
    def _clean(fields: dict, group: str = "points") -> dict:
        out = {k: fields[k] for k in ("name", "html", "width", "height_mode") if k in fields}
        if "name" in out and not (out["name"] or "").strip():
            raise EngineError("请填写模板名称")
        if "html" in out:
            if not (out["html"] or "").strip():
                raise EngineError("模板内容不能为空")
            if len(out["html"]) > MAX_HTML:
                raise EngineError("模板过长")
            render(out["html"], sample_context(group))                  # syntax / sandbox check now
        if "width" in out:
            out["width"] = int(out["width"])
            if not 320 <= out["width"] <= 4000:
                raise EngineError("宽度应在 320～4000 之间")
        if "height_mode" in out:
            parse_height(out["height_mode"])
        return out

    def add(self, fields: dict, copy_of: str = "", tid: str = "") -> str:
        group = fields.get("group") or "points"
        if group not in GROUPS:
            raise EngineError("未知的版式类型")
        article = '<!doctype html><html><head><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;font:28px/1.7 "Microsoft YaHei",sans-serif;color:#302c26;background:#faf8f3}.page{padding:60px}h1{font-size:48px}h2{font-size:34px}blockquote{border-left:3px solid #a98b60;padding-left:20px}</style></head><body><article class="page">{% if first %}<h1>{{ title }}</h1><div>{{ kicker }}</div>{% endif %}<main class="content">{% for block in blocks %}{{ block.html }}{% endfor %}</main>{% if last %}<footer>{% for line in footer_lines %}<div>{{ line }}</div>{% endfor %}</footer>{% endif %}</article></body></html>'
        points = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;padding:64px;color:#25221e;background:#fffdf8;font:28px/1.8 "Microsoft YaHei",sans-serif}h1{font-size:48px;line-height:1.4}h2{font-size:34px}footer{color:#888;font-size:20px}</style></head><body><h1>{{ title }}</h1><main>{% for p in points %}<section><h2>{{ p.heading }}</h2><p>{{ p.body }}</p></section>{% endfor %}</main><footer>{% for line in footer_lines %}<div>{{ line }}</div>{% endfor %}</footer></body></html>'
        base = self.get(copy_of) if copy_of else {"width": 1080,
                "height_mode": "fixed:1600" if group == "article" else "auto",
                "html": article if group == "article" else points, "group": group}
        t = {k: base[k] for k in ("html", "width", "height_mode", "group")}
        t.update(self._clean({"name": fields.get("name") or (base.get("name", "") + " 副本" if copy_of else ""), **fields}, t["group"]))
        t["id"] = tid or "t-" + uuid.uuid4().hex[:8]
        data = self._load()
        data["custom"].append(t)
        self._save(data)
        return t["id"]

    def update(self, tid: str, fields: dict) -> None:
        if fields.get("group") and fields["group"] != self.get(tid)["group"]:
            raise EngineError("已有版式不能更改内容类型")
        clean = self._clean(fields, self.get(tid)["group"])
        data = self._load()
        if any(b["id"] == tid for b in _builtins()):
            data["overrides"].setdefault(tid, {}).update(clean)
        else:
            next(c for c in data["custom"] if c["id"] == tid).update(clean)
        self._save(data)

    def reset(self, tid: str) -> None:
        if not any(b["id"] == tid for b in _builtins()):
            raise EngineError("只有内置模板可以恢复默认")
        data = self._load()
        data["overrides"].pop(tid, None)
        self._save(data)

    def delete(self, tid: str) -> None:
        if any(b["id"] == tid for b in _builtins()):
            raise EngineError("内置模板不能删除，可以修改或恢复默认")
        data = self._load()
        before = len(data["custom"])
        data["custom"] = [c for c in data["custom"] if c["id"] != tid]
        if len(data["custom"]) == before:
            raise EngineError(f"图文模板不存在：{tid}", "TEMPLATE_NOT_FOUND")
        self._save(data)
