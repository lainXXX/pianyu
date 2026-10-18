""""文本 · 精校原文" (design 4.10a): the best-reading version of what was actually said.

The model may drop fillers and stutters, fix recognition errors, add punctuation and keep the
speaker's own register; it may not summarise, merge, split or rewrite. Every paragraph is
checked locally and falls back to the original when it changed too much:
- similarity (punctuation removed) < MIN_SIMILARITY, or
- length change beyond +-MAX_LENGTH_CHANGE, or
- the batch reply does not line up paragraph-for-paragraph (count / numbering) -> whole batch reverted.
"""
import difflib
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from rapidfuzz import fuzz

from vtx.llm import INPUT_SHARE, _add, estimate_tokens

CHAR_LIMIT = 600     # small units: the model edits them more carefully (T31: 1500 left stutters/typos in place)
# The reply is about as long as the input, and models cap output (DeepSeek: 8k tokens). T31: a 50-minute
# course sent as one batch was cut off at 8191 output tokens -> invalid JSON -> every paragraph reverted.
BATCH_TOKENS = 2500
MIN_SIMILARITY = 0.75
MAX_LENGTH_CHANGE = 0.30
_PUNCT = re.compile(r"[\W_]+")

PROMPT = """# 精校原文
<video_title>{title}</video_title>

输入为段落 JSON 数组，每项含 n 和 text。逐段精校，保留原来的表达与语气。

## 边界
- 去无意义口水词、结巴重复，补标点；仅修正有上下文依据的识别错误与个别漏字。
- 不总结、改写、合并或拆分编号；保留实词、数字、否定及关系。
- 删除与主题无关的发布运营、引流及欢迎客套等整句，将原句完整列入 removed；保留主题相关的经历、论据与背景，无删除则为空数组。
- 话题变化时可在 text 内用换行分段。

## 输出
只输出 JSON 数组，项数与编号对应输入，不加代码围栏：
[{{"n": 编号, "text": "精校后的文字", "removed": ["删除的原句"]}}]

<transcript>
{paragraphs}
</transcript>"""


@dataclass
class RefineResult:
    paragraphs: List[dict]
    usage: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Paragraph breaks the model inserted inside a unit become real paragraphs."""
        pieces = [s.strip() for p in self.paragraphs for s in p["text"].split("\n")]
        return "\n\n".join(s for s in pieces if s)


def group_paragraphs(segments: Sequence, char_limit: int = CHAR_LIMIT) -> List[dict]:
    """Consecutive sentences joined into paragraphs of at most ~char_limit chars, losslessly."""
    paras, cur = [], ""
    for s in segments:
        text = s.text if hasattr(s, "text") else s["text"]
        if cur and len(cur) + len(text) > char_limit:
            paras.append(cur)
            cur = ""
        cur += text
    if cur:
        paras.append(cur)
    return [{"n": i, "text": t} for i, t in enumerate(paras, 1)]


def _plain(s: str) -> str:
    return _PUNCT.sub("", s or "")


def judge(original: str, new: str) -> Tuple[bool, str]:
    a, b = "".join(_editorial_tokens(original)), "".join(_editorial_tokens(new))
    if not a:
        return True, ""
    if not b:
        return False, "精校结果为空"
    change = len(b) / len(a) - 1
    if abs(change) > MAX_LENGTH_CHANGE:
        return False, f"长度变化 {change:+.0%}"
    sim = fuzz.ratio(a, b) / 100
    if sim < MIN_SIMILARITY:
        return False, f"与原文相似度 {sim:.0%}"
    return True, ""


_FILLER_CHARS = set("呃嗯啊哦额诶欸哎唉嘛呢吧呀哈")
_RELATIONS = r"[<>=≤≥×÷*/]|乘以|除以|乘|除|大于|小于|超过|低于|高于|最多|至少|之前|之后|早于|晚于|更快|更慢"
_PROTECTED_CHAR = re.compile(r"[不没别未非无勿莫零〇一二两三四五六七八九十百千万亿\d%.]")
TYPO_MAX = 4          # a replaced span longer than this is a rewrite, not a typo fix
INSERT_MAX = 2        # at most this many new characters may be added in one place (e.g. a dropped "谁")


def _editorial_tokens(text: str) -> List[str]:
    """Comparison only: equivalent spelling never changes the stored transcript."""
    from vtx.textnorm import _CN_NUM, _cn_number, normalize, tokens

    value = normalize(text)
    # normalize() intentionally excludes bare Chinese digits used as words. Adjacent
    # Latin characters here identify model versions and dimensions (V四 Pro / 三D).
    value = re.sub(_CN_NUM, lambda m: _cn_number(m.group())
                   if (re.search(r"[a-z]$", value[:m.start()].rstrip())
                       or re.match(r"[a-z]", value[m.end():].lstrip())) else m.group(), value)
    return tokens(value)


def _equivalent(old: str, new: str) -> bool:
    protected = r"`[^`]*`|https?://[^\s]+"
    if re.findall(protected, old) != re.findall(protected, new):
        return False
    if re.findall(_RELATIONS, old) != re.findall(_RELATIONS, new):
        return False
    return _editorial_tokens(old) == _editorial_tokens(new)


def _is_filler_or_repeat(src: str, i1: int, i2: int) -> bool:
    piece = src[i1:i2]
    core = _plain(piece)
    if not core:
        return True                                   # punctuation / spaces only
    if all(c in _FILLER_CHARS for c in core):
        return True
    n = len(piece)
    return src[i2:i2 + n] == piece or src[max(0, i1 - n):i1] == piece   # stutter: same text next to it


FALSE_START_MAX = 4


def _is_false_start(src: str, i1: int, i2: int) -> bool:
    """A speaker restart: a short fragment immediately followed by a word it already began
    ("那我知我想必" -> "我想必"). The character right after the cut must occur inside the cut."""
    core = _plain(src[i1:i2])
    nxt = _plain(src[i2:i2 + 3])[:1]
    return 0 < len(core) <= FALSE_START_MAX and bool(nxt) and nxt in core


def _lost_protected(old: str, new: str) -> bool:
    """Negations / numbers dropped, or digits invented ("pm 2.5" -> "12.5", T31)."""
    lost = any(c not in new for c in _PROTECTED_CHAR.findall(old))
    gained = any(c not in old for c in re.findall(r"\d", new))
    old_numbers = [x for x in _editorial_tokens(old) if re.fullmatch(r"-?\d+(?:\.\d+)?%?", x)]
    new_numbers = [x for x in _editorial_tokens(new) if re.fullmatch(r"-?\d+(?:\.\d+)?%?", x)]
    return lost or gained or old_numbers != new_numbers


def _declared_removals(src: str, removed: Sequence[str]) -> List[str]:
    sentences = []
    for sentence in dict.fromkeys(r for r in removed if isinstance(r, str) and _plain(r)):
        for match in re.finditer(re.escape(sentence), src):
            left = src[:match.start()].rstrip()
            if (not left or left[-1] in "。！？.!?；;\n") and (
                    match.end() == len(src) or sentence.rstrip()[-1] in "。！？.!?；;\n"):
                sentences.append(sentence)
                break
    return sentences


def guard(src: str, new: str, removed: Sequence[str] = ()) -> Tuple[str, List[str], List[str]]:
    """Keep the model's edits that are allowed, put everything else back.

    Allowed: deleting fillers / stutters / punctuation; deleting whole sentences that are listed in
    `removed` and really occur in `src`; replacing a short span (typo fix) without losing negations or
    numbers; inserting at most INSERT_MAX characters (plus punctuation / line breaks).
    Returns (text, restored_pieces, accepted_removed_sentences).
    """
    if _equivalent(src, new):
        return new, [], []
    listed = _declared_removals(src, removed or [])
    retained, accepted_removed = src, []
    for sentence in dict.fromkeys(listed):
        if sentence in retained and sentence not in new:
            retained = retained.replace(sentence, "", 1)
            accepted_removed.append(sentence)
    if accepted_removed:
        text, restored, _ = guard(retained, new)
        return text, restored, accepted_removed
    ops = _merged_ops(src, new)
    accepted, bad_clauses = set(), set()
    clause_of = _clause_index(src)
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            continue
        ok, hit = _allowed(src, new, i1, i2, j1, j2, listed)
        if hit:
            accepted.add(hit)
        if not ok:
            # an insert has no source span: blame the clause it lands in
            lo, hi = (i1, i2) if i2 > i1 else (max(i1 - 1, 0), min(i1, len(src) - 1) + 1)
            bad_clauses.update(clause_of[k] for k in range(lo, hi) if k < len(src))
    # rebuild: a clause containing any disallowed change is taken verbatim from the source, so the result
    # is always either the model's clause or the original clause, never a patched-together mix
    out, restored = [], []
    for tag, i1, i2, j1, j2 in ops:
        span = range(i1, i2) if i2 > i1 else range(max(i1 - 1, 0), min(i1 + 1, len(src)))
        if tag != "equal" and any(clause_of[k] in bad_clauses for k in span if k < len(src)):
            out.append(src[i1:i2])
        else:
            out.append(new[j1:j2])
    for c in sorted(bad_clauses):
        restored.append(_clause_text(src, clause_of, c))
    return "".join(out), restored, [r for r in listed if r in accepted]


_CLAUSE_END = set("，。！？；、,!?;\n")


def _clause_index(src: str) -> List[int]:
    idx, cur = [], 0
    for ch in src:
        idx.append(cur)
        if ch in _CLAUSE_END:
            cur += 1
    return idx


def _clause_text(src: str, clause_of: List[int], c: int) -> str:
    return "".join(ch for ch, k in zip(src, clause_of) if k == c).strip()


def _merged_ops(src: str, new: str):
    """difflib opcodes, with changes separated only by a short run of punctuation merged into one
    replace (e.g. '不用水？说' -> '不用？谁说' is one typo fix, not a deletion plus an insertion)."""
    raw = difflib.SequenceMatcher(None, src, new, autojunk=False).get_opcodes()
    out = []
    for op in raw:
        tag, i1, i2, j1, j2 = op
        if (out and tag != "equal" and len(out) >= 2 and out[-1][0] == "equal" and out[-2][0] != "equal"
                and out[-1][2] - out[-1][1] <= 2 and not _plain(src[out[-1][1]:out[-1][2]])):
            gap = out.pop()
            prev = out.pop()
            out.append(("replace", prev[1], i2, prev[3], j2))
            continue
        if out and tag != "equal" and out[-1][0] != "equal":
            prev = out.pop()
            out.append(("replace", prev[1], i2, prev[3], j2))
            continue
        out.append(op)
    return out


_LATIN = re.compile(r"[A-Za-z]")
LATIN_FIX_MAX = 16    # garbled English ("居面count" -> "gmail account") needs more room than a CJK typo


def _allowed(src, new, i1, i2, j1, j2, listed):
    """(allowed, accepted_removed_sentence_or_None) for one non-equal op."""
    old, cur = src[i1:i2], new[j1:j2]
    a, b = _plain(old), _plain(cur)
    if re.findall(_RELATIONS, src) != re.findall(_RELATIONS, new):
        for match in re.finditer(_RELATIONS, src):
            if i1 < match.end() and i2 > match.start():
                return False, None
        for match in re.finditer(_RELATIONS, new):
            if j1 < match.end() and j2 > match.start():
                return False, None
    for match in re.finditer(r"```[\s\S]*?```|`[^`]*`|https?://[^\s]+", src):
        if (i1 < match.end() and i2 > match.start()) or (i1 == i2 and match.start() < i1 < match.end()):
            return False, None
    # A single numeral edit can be interleaved with an independent typo fix.
    # Both sides must denote exactly the same number; real version/value changes
    # continue through the existing protected-character checks.
    from vtx.textnorm import _CN_NUM, _cn_number

    if re.fullmatch(_CN_NUM, old.strip()) and _cn_number(old.strip()) == cur.strip():
        return True, None
    if not a and not b:
        # punctuation / line breaks -- except a decimal point or percent sign inside a number ("1.5" -> "15")
        in_number = re.search(r"\d", src[max(0, i1 - 1):i1]) or re.search(r"\d", src[i2:i2 + 1])
        return not (in_number and re.search(r"[.%．％]", old + cur)), None
    if not b:                                                      # pure deletion
        if not _PROTECTED_CHAR.search(old) and (_is_filler_or_repeat(src, i1, i2) or _is_false_start(src, i1, i2)):
            return True, None
        hit = next((r for r in listed if a in _plain(r)), None)
        return (bool(hit), hit)                                    # listed off-topic sentence only
    if not a:                                                      # pure insertion
        return len(b) <= INSERT_MAX and not _PROTECTED_CHAR.search(cur), None
    if _lost_protected(old, cur):
        return False, None
    if len(a) <= TYPO_MAX and len(b) <= len(a) + INSERT_MAX:
        return True, None                                          # typo fix
    near_latin = _LATIN.search(src[max(0, i1 - 3):i2 + 3])
    if _LATIN.search(cur) and near_latin and len(a) <= LATIN_FIX_MAX and len(b) <= LATIN_FIX_MAX:
        return True, None                                          # misheard English term ("居面count")
    return False, None


def _diff(original: str, new: str) -> List[Tuple[str, str, str]]:
    """[(tag, new_piece, original_piece)]; original = join of original_piece over all ops."""
    ops = difflib.SequenceMatcher(None, original, new, autojunk=False).get_opcodes()
    return [(tag, new[j1:j2], original[i1:i2]) for tag, i1, i2, j1, j2 in ops]


def _parse(raw: str):
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("no JSON array")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("not a list")
    return data


def _fill(template: Optional[str], title: str, paragraphs: str) -> str:
    """template: an edited text prompt ({title}, {paragraphs}); None = the built-in PROMPT."""
    if template is None:
        return PROMPT.format(title=title, paragraphs=paragraphs)
    from vtx.prompts import render

    return render(template, title=title, paragraphs=paragraphs)


def _batches(paras: List[dict], title: str, budget: int, template: Optional[str] = None) -> List[List[dict]]:
    overhead = estimate_tokens(_fill(template, title, "[]"))
    out, cur, used = [], [], overhead
    for p in paras:
        cost = estimate_tokens(json.dumps(p, ensure_ascii=False)) + 2
        if cur and used + cost > budget:
            out.append(cur)
            cur, used = [], overhead
        cur.append(p)
        used += cost
    if cur:
        out.append(cur)
    return out


def refine(client, segments: Sequence, title: str = "", char_limit: int = CHAR_LIMIT,
           template: Optional[str] = None) -> RefineResult:
    """template: a user text prompt; whatever it says, every paragraph still goes through judge/guard."""
    budget = min(int(client.context * INPUT_SHARE), BATCH_TOKENS)
    overhead = estimate_tokens(_fill(template, title, "[]"))
    # a single paragraph must fit in one call together with the prompt
    char_limit = max(50, min(char_limit, budget - overhead - 50))
    paras = group_paragraphs(segments, char_limit)
    usage: dict = {}
    results = []
    from vtx.clean import clean

    for batch in _batches(paras, title, budget, template):
        # rule-based filler/stutter removal first (same rules as the reading text), so the model only
        # handles what rules cannot. Judging and diffs still use the raw paragraph.
        sent = [{"n": p["n"], "text": clean(p["text"]).text} for p in batch]
        prompt = _fill(template, title, json.dumps(sent, ensure_ascii=False))
        out, u = client.chat([{"role": "user", "content": prompt}])
        _add(usage, u)
        try:
            reply = _parse(out)
            aligned = (len(reply) == len(batch)
                       and all(isinstance(r, dict) and r.get("n") == p["n"] and isinstance(r.get("text"), str)
                               for r, p in zip(reply, batch)))
        except (ValueError, json.JSONDecodeError):
            reply, aligned = None, False
        for i, p in enumerate(batch):
            if not aligned:
                results.append(_reverted(p, "模型返回的段落与输入对不上，整批保留原文"))
                continue
            src = sent[i]["text"]
            raw = reply[i]["text"].strip()
            listed = [r for r in reply[i].get("removed") or [] if isinstance(r, str)]
            # judge the model's own output (minus sentences it declared off-topic): a heavy rewrite is
            # reverted as a whole instead of being patched word by word
            retained = src
            for sentence in _declared_removals(src, listed):
                if sentence not in raw:
                    retained = retained.replace(sentence, "", 1)
            ok, reason = judge(retained, raw)
            new, restored, removed = guard(src, raw, listed)
            if not ok:
                results.append(_reverted(p, f"改动过大（{reason}），保留原文"))
                continue
            results.append({"n": p["n"], "original": p["text"], "text": new.strip(), "status": "ok",
                            "reason": "", "restored": restored, "removed": removed,
                            "diff": _diff(p["text"], new.strip())})
    stats = {"ok": sum(r["status"] == "ok" for r in results),
             "reverted": sum(r["status"] == "reverted" for r in results),
             "restored": sum(len(r.get("restored") or []) for r in results),
             "removed": [s for r in results for s in r.get("removed") or []]}
    return RefineResult(results, usage, stats)


def _reverted(p: dict, reason: str) -> dict:
    return {"n": p["n"], "original": p["text"], "text": p["text"], "status": "reverted",
            "reason": reason, "restored": [], "removed": [], "diff": [("equal", p["text"], p["text"])]}
