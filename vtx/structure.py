"""结构化原文 (design 4.12, 原文类): the model arranges the transcript, the program writes the text.

The transcript is split into numbered sentences. The model returns only a structure:
    {"sections": [{"heading": "...", "level": 2|3, "blocks": [{"type": "p", "ids": [..]},
                                                             {"type": "list", "items": [[..], [..]]}]}],
     "removed": [ids], "fixes": {"id": "corrected sentence"}, "key": [ids]}
The body is rebuilt from the original sentences, so nothing the model invents can appear in it:
  - a fix is used only when the refine guard accepts it as a short typo fix (no new digits, no rewrites);
  - explicitly removed sentences remain in the audit; unlisted omissions are restored;
  - every sentence that is missing or used twice is put back in its original place and reported;
  - a heading may not contain digits that its section does not contain.
Long transcripts are cut into batches of whole paragraphs; order changes stay inside a batch.
"""
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from vtx.llm import INPUT_SHARE, estimate_tokens

BATCH_TOKENS = 6000           # the reply is small (ids only), so batches can be larger than refine's
HEADING_MAX = 20
_SENT_END = re.compile(r"(?<=[。！？!?；;])")

PROMPT = """# 结构化原文
<video_title>{title}</video_title>

输入为按句编号的 JSON 数组，每项含 i 和 s。只组织结构，不改写内容。

## 规则
- 按主题分节，heading 简短且有来源依据，level 为 2；有必要时用 level=3 表示子主题。
- 句子组成段落；来源中的并列或列举可组成列表。
- 可归并同一主题，但须保留因果、条件、步骤顺序和原意。
- 把与主题无关的发布运营、引流、欢迎客套和告别放入 removed；主题相关的经历、案例、论据与背景保留。
- fixes 只修明确识别错字或等价书写，返回完整句子，不改其余内容或数值。
- key 只列关键结论的编号，可为空。
- 每个编号在 blocks 或 removed 中恰好出现一次；fixes 与 key 仅引用已有编号。

## 输出
只输出此 JSON 对象，不加代码围栏：
{{"sections": [{{"heading": "小标题", "level": 2, "blocks": [{{"type": "p", "ids": [1, 2, 3]}}, {{"type": "list", "items": [[4], [5, 6]]}}]}}],
 "removed": [7], "fixes": {{"12": "改正后的整句"}}, "key": [3]}}

<transcript>
{sentences}
</transcript>"""


@dataclass
class StructureResult:
    doc: dict                       # {"sections": [{"heading", "level", "blocks": [{"type", "items": [[sentence dict]]}]}]}
    usage: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return to_markdown(self.doc)


def split_sentences(lines: Sequence[str]) -> List[str]:
    """Transcript lines -> sentences (a line may hold several). Empty pieces are dropped."""
    out = []
    for line in lines:
        out += [s.strip() for s in _SENT_END.split(line) if s.strip()]
    return out


def _fill(template: Optional[str], title: str, sentences: str) -> str:
    if template is None:
        return PROMPT.format(title=title, sentences=sentences)
    from vtx.prompts import render

    return render(template, title=title, sentences=sentences)


def _parse(raw: str) -> dict:
    from vtx.knowledge import parse_json

    return parse_json(raw, "sections")


def _ids(value) -> List[int]:
    out = []
    for v in value if isinstance(value, list) else []:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


def _accept_fix(src: str, fixed) -> Optional[str]:
    """The fix when it is a pure typo correction of src (refine guard rules), else None."""
    from vtx.refine import guard

    if not isinstance(fixed, str) or not fixed.strip() or fixed.strip() == src:
        return None
    new, restored, _ = guard(src, fixed.strip())
    return new if not restored and new == fixed.strip() else None


def _batches(sentences: List[str], title: str, budget: int, template: Optional[str]) -> List[range]:
    overhead = estimate_tokens(_fill(template, title, "[]"))
    out, start, used = [], 0, overhead
    for i, s in enumerate(sentences):
        cost = estimate_tokens(s) + 6
        if i > start and used + cost > budget:
            out.append(range(start, i))
            start, used = i, overhead
        used += cost
    out.append(range(start, len(sentences)))
    return [r for r in out if len(r)]


def build(sentences: List[str], reply: dict, ids: range) -> tuple:
    """Rebuild one batch from the model's structure. Returns (sections, stats)."""
    valid = set(i + 1 for i in ids)
    text = {i + 1: sentences[i] for i in ids}
    stats = {"sentences": len(valid), "removed": [], "fixed": [], "rejected_fixes": 0, "restored": 0,
             "moved": 0, "removed_ignored": False}

    removed = [i for i in _ids(reply.get("removed")) if i in valid]
    removed = set(removed)
    fixes = {}
    for k, v in (reply.get("fixes") or {}).items() if isinstance(reply.get("fixes"), dict) else []:
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        if i in valid:
            ok = _accept_fix(text[i], v)
            if ok:
                fixes[i] = ok
            else:
                stats["rejected_fixes"] += 1
    key = set(_ids(reply.get("key")))

    seen = set()

    def sentence(i):
        return {"id": i, "text": fixes.get(i, text[i]), "key": i in key, "fixed": i in fixes}

    def take(group) -> list:
        out = []
        for i in _ids(group):
            if i in valid and i not in seen and i not in removed:
                seen.add(i)
                out.append(sentence(i))
        return out

    sections = []
    for sec in reply.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        blocks = []
        for b in sec.get("blocks") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "list":
                items = [it for it in (take(g) for g in b.get("items") or []) if it]
                if items:
                    blocks.append({"type": "list", "items": items})
            else:
                items = take(b.get("ids"))
                if items:
                    blocks.append({"type": "p", "items": [items]})
        if blocks:
            sections.append({"heading": str(sec.get("heading") or "").strip()[:HEADING_MAX],
                             "level": 3 if sec.get("level") == 3 else 2, "blocks": blocks})

    # sentences the model lost: back into the section holding their nearest earlier neighbour
    missing = sorted(i for i in valid if i not in seen and i not in removed)
    stats["restored"] = len(missing)
    for i in missing:
        target = _block_after(sections, i)
        if target is None:
            sections.append({"heading": "", "level": 2, "blocks": [{"type": "p", "items": [[]]}]})
            target = sections[-1]["blocks"][-1]["items"][-1]
        target.append({**sentence(i), "restored": True})
        target.sort(key=lambda s: s["id"])
        seen.add(i)

    # a heading may only use digits that occur in its own section
    for sec in sections:
        body = "".join(s["text"] for b in sec["blocks"] for it in b["items"] for s in it)
        from vtx.refine import _editorial_tokens

        body_numbers = {x for x in _editorial_tokens(body) if re.fullmatch(r"-?\d+(?:\.\d+)?%?", x)}
        heading_numbers = {x for x in _editorial_tokens(sec["heading"]) if re.fullmatch(r"-?\d+(?:\.\d+)?%?", x)}
        if not heading_numbers <= body_numbers:
            sec["heading"] = ""

    order = [s["id"] for sec in sections for b in sec["blocks"] for it in b["items"] for s in it]
    stats["moved"] = sum(1 for a, b in zip(order, order[1:]) if b < a)
    stats["removed"] = [text[i] for i in sorted(removed)]
    stats["fixed"] = [{"old": text[i], "new": fixes[i]} for i in sorted(fixes)]
    return sections, stats


def _block_after(sections: list, i: int):
    """The item list holding the largest sentence id below i (where i originally followed)."""
    best, best_id = None, 0
    for sec in sections:
        for b in sec["blocks"]:
            for it in b["items"]:
                for s in it:
                    if best_id < s["id"] < i:
                        best, best_id = it, s["id"]
    if best is None and sections:
        return sections[0]["blocks"][0]["items"][0]
    return best


def structure(client, lines: Sequence[str], title: str = "", template: Optional[str] = None) -> StructureResult:
    """lines: cleaned transcript lines (fillers already removed by the rule cleaner)."""
    from vtx.llm import LLMError
    from vtx.refine import _add

    sentences = split_sentences(lines)
    budget = min(int(client.context * INPUT_SHARE), BATCH_TOKENS)
    usage: dict = {}
    sections, totals = [], {"sentences": 0, "removed": [], "fixed": [], "rejected_fixes": 0, "restored": 0,
                            "moved": 0, "removed_ignored": False, "batches": 0, "failed_batches": 0}
    for ids in _batches(sentences, title, budget, template):
        payload = json.dumps([{"i": i + 1, "s": sentences[i]} for i in ids], ensure_ascii=False)
        reply = None
        for attempt in (1, 2):
            out, u = client.chat([{"role": "user", "content": _fill(template, title, payload)}])
            _add(usage, u)
            try:
                reply = _parse(out)
                break
            except (ValueError, json.JSONDecodeError):
                reply = None
        totals["batches"] += 1
        if reply is None:            # unusable reply: this batch keeps the original order, one paragraph
            totals["failed_batches"] += 1
            reply = {"sections": [{"heading": "", "blocks": [{"type": "p", "ids": [i + 1 for i in ids]}]}]}
        secs, st = build(sentences, reply, ids)
        sections += secs
        for k in ("sentences", "rejected_fixes", "restored", "moved"):
            totals[k] += st[k]
        totals["removed"] += st["removed"]
        totals["fixed"] += st["fixed"]
        totals["removed_ignored"] |= st["removed_ignored"]
    if sentences and totals["failed_batches"] == totals["batches"]:
        raise LLMError("LLM_BAD_OUTPUT", "模型没有返回可用的结构，请换一个模型再试")
    return StructureResult({"sections": sections}, usage, totals)


def to_markdown(doc: dict, title: str = "", footer: str = "") -> str:
    lines = [f"# {title}", ""] if title else []
    for sec in doc["sections"]:
        if sec["heading"]:
            lines += ["#" * sec["level"] + " " + sec["heading"], ""]
        for b in sec["blocks"]:
            render = lambda item: "".join(f"**{s['text']}**" if s["key"] else s["text"] for s in item)
            if b["type"] == "list":
                lines += [f"- {render(it)}" for it in b["items"]] + [""]
            else:
                lines += [render(it) for it in b["items"]] + [""]
    if footer:
        lines += ["---", footer, ""]
    return "\n".join(lines)
