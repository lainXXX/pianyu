"""Structured knowledge for "文本 · 结构化要点" and "图文" (design 4.10a / 4.10).

One LLM call returns JSON; both the Markdown text and the A/B posters render from it, so
switching between them costs nothing. Every quote must come with the sentence it was taken
from; quotes are checked against the input locally so a model cannot put words in a
speaker's mouth:
- source not found in the input (best fuzzy match < SOURCE_MIN) -> quote dropped
- quote drifted too far from its source (< QUOTE_MIN)             -> source used instead
"""
import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from rapidfuzz import fuzz

from vtx.llm import INPUT_SHARE, LLMError, _add, _merge_parts, estimate_tokens, split_text

SOURCE_MIN = 85      # rapidfuzz partial_ratio, 0-100
QUOTE_MIN = 80       # rapidfuzz ratio between quote and its source, punctuation removed
MAX_POINTS, MAX_KEY, MAX_QUOTES = 10, 3, 4
_TIME = re.compile(r"[（(]?\s*\d{1,2}:\d{2}(?::\d{2})?\s*[)）]?")
_PUNCT = re.compile(r"[\W_]+")

PROMPT = """# 结构化要点
<video_title>{title}</video_title>

## 内容
- 按视频逻辑覆盖主要内容，最多 {max_points} 个要点，不凑数量。
- title 写主题，conclusion 写核心结论；heading 写短标题。
- body 是简短、独立可读的图片摘要；text 是充分的阅读正文，保留例子、数字、条件、因果和推理，不只列结论。
- 数据保留指标、对象与适用条件；不为缩短摘要而丢失条件。
- 核心要点设 key=true，最多 3 项；detail 仅补正文未覆盖的必要信息，无则留空。
- quotes 只选完整、清楚的单人表达，最多 4 句，可为空；quote 可去口水词、改明确错字，source 必须逐字复制来源，不拼接多轮问答。

## 输出
只输出此 JSON 对象，不加代码围栏：
{{"title": "...", "conclusion": "...", "points": [{{"heading": "...", "body": "...", "text": "...", "key": true, "detail": ["..."], "quotes": [{{"quote": "...", "source": "..."}}]}}]}}

<transcript>
{text}
</transcript>"""

# The JSON spec for every points prompt (rendered by vtx.prompts.render: only {title} {text} {max_points}
# are substituted, other braces stay), so all point prompts feed the same layouts.
REPLY_SPEC = """## 输出
- body 用于图片，须简短且保留关键条件；text 用于阅读，须充分呈现相关内容。
- detail 不重复正文，无补充则为空数组。
- quote 可去口水词、改明确错字，不改意思；source 必须逐字复制文字稿，不能拼接或改写。无合适引文则 quotes 为空数组。
- 不编造内容或时间点。只输出此 JSON 对象，不加代码围栏：
{"title": "...", "conclusion": "...", "points": [{"heading": "...", "body": "...", "text": "...", "key": true, "detail": ["..."], "quotes": [{"quote": "...", "source": "..."}]}]}

<transcript>
{text}
</transcript>"""

MERGE = """# 合并结构化要点
<video_title>{title}</video_title>

合并同一视频的 {n} 段结果：
- 按视频顺序去重，最多 {max_points} 项，key=true 最多 3 项。
- text 保留各片段的独有内容、例子、数据与条件，不压成图片摘要。
- quote 与 source 原样保留；无合适引文则留空。
- 只输出与输入结构相同的 JSON 对象，不加代码围栏。

<parts>
{parts}
</parts>"""

RETRY = "# 修正格式\n仅返回符合原定结构的合法 JSON 对象，不加说明或代码围栏。"


@dataclass
class KnowledgeResult:
    data: dict
    chunks: int
    usage: dict = field(default_factory=dict)
    quote_stats: dict = field(default_factory=dict)


def parse_json(raw: str, required: str = "points") -> dict:
    """Accept bare JSON, ```json fences, or JSON surrounded by chatter. `required` must be a list."""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict) or not isinstance(data.get(required), list):
        raise ValueError(f"missing {required}")
    return data


def _plain(s: str) -> str:
    return _PUNCT.sub("", s or "")


def _no_time(s: str) -> str:
    return re.sub(r"\s{2,}", " ", _TIME.sub("", s or "")).strip()


def verify_quotes(points: List[dict], text: str) -> Tuple[List[dict], dict]:
    stats = {"kept": 0, "dropped": 0, "replaced": 0}
    haystack = _plain(text)
    out = []
    for p in points:
        quotes = []
        for q in p.get("quotes") or []:
            quote, source = (q.get("quote") or "").strip(), (q.get("source") or "").strip()
            if not source or not quote or fuzz.partial_ratio(_plain(source), haystack) < SOURCE_MIN:
                stats["dropped"] += 1
                continue
            if fuzz.ratio(_plain(quote), _plain(source)) < QUOTE_MIN:
                quotes.append({"quote": source, "source": source})
                stats["replaced"] += 1
            else:
                quotes.append({"quote": quote, "source": source})
                stats["kept"] += 1
        out.append({**p, "quotes": quotes})
    return out, stats


def enforce_limits(data: dict) -> dict:
    points = []
    key_left, quotes_left = MAX_KEY, MAX_QUOTES
    for p in (data.get("points") or [])[:MAX_POINTS]:
        key = bool(p.get("key")) and key_left > 0
        key_left -= key
        quotes = [q for q in (p.get("quotes") or [])][:max(quotes_left, 0)]
        quotes_left -= len(quotes)
        points.append({
            "heading": _no_time(p.get("heading", "")),
            "body": _no_time(p.get("body", "")),
            "text": _no_time(p.get("text", "") or p.get("body", "")),
            "key": key,
            "detail": [_no_time(d) for d in (p.get("detail") or [])][:6] if key else [],
            "quotes": [{"quote": _no_time(q["quote"]), "source": q.get("source", "")} for q in quotes],
        })
    return {"title": _no_time(data.get("title", "")), "conclusion": _no_time(data.get("conclusion", "")),
            "points": points}


def _ask_json(client, prompt: str, usage: dict) -> dict:
    messages = [{"role": "user", "content": prompt}]
    for attempt in (1, 2):
        out, u = client.chat(messages)
        _add(usage, u)
        try:
            return parse_json(out)
        except (ValueError, json.JSONDecodeError):
            messages = messages + [{"role": "assistant", "content": out}, {"role": "user", "content": RETRY}]
    raise LLMError("LLM_BAD_OUTPUT", "模型两次都没有返回合法的结构化结果，请换一个模型再试")


def _fill(template: Optional[str], title: str, text: str) -> str:
    """template: an edited poster prompt ({title}, {text}, {max_points}); None = the built-in PROMPT."""
    if template is None:
        return PROMPT.format(title=title, text=text, max_points=MAX_POINTS)
    from vtx.prompts import render

    return render(template, title=title, text=text, max_points=MAX_POINTS)


def generate(client, text: str, title: str = "", template: Optional[str] = None,
             progress: Optional[Callable[[str, int, int], None]] = None) -> KnowledgeResult:
    """template: a user poster prompt; the reply must still be the fixed JSON and quotes are verified."""
    budget = int(client.context * INPUT_SHARE)
    usage: dict = {}
    whole = _fill(template, title, text)
    if estimate_tokens(whole) <= budget:
        if progress:
            progress("generate", 1, 1)
        data, chunks = _ask_json(client, whole, usage), 1
    else:
        overhead = estimate_tokens(_fill(template, title, ""))
        source_parts = split_text(text, budget - overhead)
        parts = []
        for index, chunk in enumerate(source_parts, 1):
            if progress:
                progress("generate", index, len(source_parts))
            parts.append(_ask_json(client, _fill(template, title, chunk), usage))
        chunks = len(parts)
        if progress:
            progress("merge", 0, 0)
        merge = MERGE.format(title=title, n=chunks, max_points=MAX_POINTS,
                             parts="\n\n".join(json.dumps(p, ensure_ascii=False) for p in parts))
        if estimate_tokens(merge) <= budget:
            data = _ask_json(client, merge, usage)
        else:
            instruction = MERGE.format(title=title, n=chunks, max_points=MAX_POINTS, parts="")
            merged = _merge_parts(client, [json.dumps(p, ensure_ascii=False) for p in parts],
                                  instruction, budget, usage, progress)
            try:
                data = parse_json(merged)
            except (ValueError, json.JSONDecodeError):
                raise LLMError("LLM_BAD_OUTPUT", "模型没有返回合法的合并结果，请换一个模型再试")
        if len(data["points"]) > MAX_POINTS:
            raise LLMError("LLM_BAD_OUTPUT", "合并要点超过上限，请换一个模型再试")
    points, stats = verify_quotes(data.get("points") or [], text)
    data = enforce_limits({**data, "points": points, "title": data.get("title") or title})
    return KnowledgeResult(data, chunks, usage, stats)


def to_markdown(data: dict, footer: str) -> str:
    lines = [f"# {data['title']}", "", f"> {data['conclusion']}", ""]
    for i, p in enumerate(data["points"], 1):
        lines.append(f"## {i}. {p['heading']}" + ("（重点）" if p["key"] else ""))
        lines.append(p.get("text") or p["body"])          # full explanation; posters use the short body
        lines += [f"- {d}" for d in p.get("detail") or []]
        lines += [f"\n> “{q['quote']}”" for q in p.get("quotes") or []]
        lines.append("")
    lines += ["---", footer, ""]
    return "\n".join(lines)
