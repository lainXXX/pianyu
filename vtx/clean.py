"""Conservative filler removal: rules propose edits, a validator guards meaning, edits are reversible.

An Edit replaces text[start:end] (== old) with new. `new` is "" (pure deletion) or a single
sentence/comma mark produced by punctuation tidying. Nothing else may be inserted.
"""
import re
from dataclasses import dataclass, field
from typing import List, Tuple

SENT_END = "。！!？?；;…"
COMMA = "，,、"
QUESTION = "？?"

_PROTECT = [
    ("quote", re.compile(r"“[^”]*”|\"[^\"]*\"|「[^」]*」|‘[^’]*’")),
    ("number", re.compile(r"-?\d+(?:\.\d+)?%?")),
    ("number", re.compile(r"[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?")),
    ("negation", re.compile(r"[不没别未非无勿莫]")),
    ("condition", re.compile(r"只有|只要|如果|假如|要是|除非|一旦|否则|即使|哪怕")),
    ("step", re.compile(r"首先|然后|接着|最后|其次")),
    ("latin", re.compile(r"[A-Za-z][A-Za-z'\-]*")),
]
_REPEAT_UNITS = "这个|那个|我们|然后|这|那|我|你|他|她|它|就"


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    old: str
    new: str
    reason: str


@dataclass
class CleanResult:
    text: str
    edits: List[Edit] = field(default_factory=list)      # accepted, sorted by start
    rejected: List[Tuple[Edit, str]] = field(default_factory=list)


def protected_spans(text: str) -> List[Tuple[int, int, str]]:
    return [(m.start(), m.end(), kind) for kind, rx in _PROTECT for m in rx.finditer(text)]


def _prev(text: str, i: int, context_before: str) -> str:
    return text[i - 1] if i > 0 else context_before[-1:]


def _boundary_before(text: str, i: int, context_before: str) -> bool:
    p = _prev(text, i, context_before)
    return p == "" or p in SENT_END + COMMA


def _tidy(text: str, a: int, b: int) -> Tuple[int, int, str]:
    """Widen a deletion so it leaves no dangling or doubled punctuation."""
    before = text[a - 1] if a > 0 else ""
    after = text[b] if b < len(text) else ""
    if a == 0 or before in SENT_END:
        return a, (b + 1 if after and after in COMMA else b), ""
    if before in COMMA and (after == "" or after in SENT_END + COMMA):
        return a - 1, b, ("。" if after == "" else "")
    return a, b, ""


def _candidates(text: str, context_before: str) -> List[Edit]:
    out = []

    def add(a, b, reason, tidy=True):
        a, b, new = _tidy(text, a, b) if tidy else (a, b, "")
        out.append(Edit(a, b, text[a:b], new, reason))

    for m in re.finditer(r"呃+", text):                                   # always a filler
        add(*m.span(), "filler:呃")

    for m in re.finditer(r"[嗯啊](?:……|…)?", text):                         # standalone 嗯 / 啊
        a, b = m.span()
        nxt = text[b] if b < len(text) else ""
        prev = _prev(text, a, context_before)
        if not _boundary_before(text, a, context_before) or (prev and prev in QUESTION):
            continue                                                       # after a question it may be an answer
        if m.group(0).startswith("啊") and nxt not in COMMA:
            continue                                                       # 啊 only as "啊，"
        if nxt and nxt not in SENT_END + COMMA:
            continue                                                       # 嗯我的… : part of the sentence, keep
        add(a, b, "filler:" + m.group(0)[0])

    for m in re.finditer(r"(?:就是){2,}，", text):                          # 就是就是，
        add(*m.span(), "repeat:就是", tidy=False)
    for m in re.finditer(r"(就是)(?:就是)+(?!，|就是)", text):              # 就是就是X -> 就是X
        a, b = m.span()
        add(a, b - 2, "repeat:就是", tidy=False)
    for m in re.finditer(r"就是，", text):                                  # 然后就是， / ，就是，
        a = m.start()
        if text[max(0, a - 2):a] == "然后" or _boundary_before(text, a, context_before):
            add(a, m.end(), "filler:就是", tidy=False)

    for m in re.finditer(rf"({_REPEAT_UNITS})\1+", text):                  # 那那块 / 这个这个
        unit = m.group(1)
        add(m.start(), m.end() - len(unit), "repeat:" + unit, tidy=False)
    return out


def validate(text: str, edits: List[Edit]) -> Tuple[List[Edit], List[Tuple[Edit, str]]]:
    protected = protected_spans(text)
    accepted, rejected = [], []
    for e in sorted(edits, key=lambda e: (e.start, -e.end)):
        if text[e.start:e.end] != e.old or not e.old:
            rejected.append((e, "offset mismatch"))
        elif e.new not in ("", "。", "，"):
            rejected.append((e, "insertion not allowed"))
        elif accepted and e.start < accepted[-1].end:
            rejected.append((e, "overlaps another edit"))
        else:
            hits = {k for a, b, k in protected if e.start < b and a < e.end}
            # a stutter may drop one copy of a protected word as long as an identical copy stays right
            # after it; quoted speech is never touched
            stutter_ok = (e.reason.startswith("repeat:") and "quote" not in hits
                          and text[e.end:e.end + len(e.old)] == e.old)
            if hits and not stutter_ok:
                rejected.append((e, "touches protected " + ",".join(sorted(hits))))
            else:
                accepted.append(e)
    return accepted, rejected


def apply(text: str, edits: List[Edit]) -> str:
    for e in sorted(edits, key=lambda e: e.start, reverse=True):
        assert text[e.start:e.end] == e.old, e
        text = text[:e.start] + e.new + text[e.end:]
    return text


def revert(cleaned: str, edits: List[Edit]) -> str:
    """Rebuild the original from the cleaned text and its accepted edits."""
    out, pos_c, pos_o = [], 0, 0
    for e in sorted(edits, key=lambda e: e.start):
        keep = e.start - pos_o
        out += [cleaned[pos_c:pos_c + keep], e.old]
        pos_c += keep + len(e.new)
        pos_o = e.end
    out.append(cleaned[pos_c:])
    return "".join(out)


def clean(text: str, context_before: str = "") -> CleanResult:
    accepted, rejected = validate(text, _candidates(text, context_before))
    return CleanResult(apply(text, accepted), accepted, rejected)
