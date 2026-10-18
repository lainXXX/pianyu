"""T8 acceptance 1-2: boundary sentences, validator, properties on real transcripts."""
import json
import re
from pathlib import Path

import pytest

from vtx.clean import Edit, apply, clean, protected_spans, revert, validate

ROOT = Path(__file__).resolve().parents[2]

# design doc 4.6 boundary sentences: (input, expected cleaned text)
BOUNDARY = [
    ("呃，今天就是就是，给大家讲一下这个工具，嗯……", "今天给大家讲一下这个工具。"),
    ("你确认了吗？嗯，确认了。", "你确认了吗？嗯，确认了。"),
    ("我说的就是这个问题。", "我说的就是这个问题。"),
    ("不是不是，我说的是一点五，不是十五。", "不是不是，我说的是一点五，不是十五。"),
    ("先断电，然后再拆机。", "先断电，然后再拆机。"),
    ("诶，等一下，别删那个文件。", "诶，等一下，别删那个文件。"),
    ("我不建议现在发布生产环境", "我不建议现在发布生产环境"),
    ("每次服用 1.5 毫升", "每次服用 1.5 毫升"),
    ("只有完成备份后才能升级", "只有完成备份后才能升级"),
]


@pytest.mark.parametrize("text,expected", BOUNDARY)
def test_boundary_sentences(text, expected):
    assert clean(text).text == expected


@pytest.mark.parametrize("text,expected", [
    ("吓汝一跳。呃，大家好啊。", "吓汝一跳。大家好啊。"),
    ("呃呃呃，那这种行为", "那这种行为"),
    ("啊这个侮辱啊，啊，错骨扬尸。", "啊这个侮辱啊，错骨扬尸。"),
    ("犯罪吗？啊，你比如张三", "犯罪吗？啊，你比如张三"),   # after a question: keep (could be an answer)
    ("的极限呢。啊，罪行法定原则", "的极限呢。罪行法定原则"),
    ("沾了尿，那那块沾了尿的青石", "沾了尿，那块沾了尿的青石"),
    ("那我我们就可以让机器", "那我们就可以让机器"),
    ("不是我们啊这个这个Y呢", "不是我们啊这个Y呢"),
    ("然后就是，我们去测一下", "然后我们去测一下"),
    ("你可能设说嗯我的上限", "你可能设说嗯我的上限"),     # 嗯 between characters: ambiguous, keep
    ("我们刚刚看看慢慢来", "我们刚刚看看慢慢来"),          # real reduplication kept
    ("对对对，就是这样。", "对对对，就是这样。"),
    ("他说“呃，我不去”。", "他说“呃，我不去”。"),          # quoted speech is untouched
    ("他讲：“那那块草最好。”那那天", "他讲：“那那块草最好。”那天"),  # stutter inside quote kept, outside removed
    ("额度不够，金额也不对。", "额度不够，金额也不对。"),
])
def test_rules_on_short_examples(text, expected):
    assert clean(text).text == expected


def test_filler_at_text_start_without_context_is_removed():
    # regression: "" in "？?" is True in Python, which made every start-of-text 嗯 look like an answer
    assert clean("嗯，因为这里的").text == "因为这里的"
    assert clean("啊，罪行法定原则").text == "罪行法定原则"


def test_context_before_marks_question():
    assert clean("嗯，因为这里的", context_before="他们为什么不说话呢？").text == "嗯，因为这里的"
    assert clean("嗯，因为这里的", context_before="这就是原因。").text == "因为这里的"


def test_validator_rejects_protected_and_misaligned():
    text = "我不建议发布，每次1.5毫升，只有备份后才行"
    bad = [
        Edit(1, 2, "不", "", "test"),                    # negation
        Edit(9, 10, ".", "", "test"),                    # decimal point
        Edit(13, 15, "只有", "", "test"),                 # condition
        Edit(0, 1, "你", "", "test"),                     # old text does not match the offsets
    ]
    accepted, rejected = validate(text, bad)
    assert accepted == [] and len(rejected) == 4


def test_validator_rejects_overlap():
    text = "呃呃，好"
    accepted, rejected = validate(text, [Edit(0, 3, "呃呃，", "", "a"), Edit(1, 2, "呃", "", "b")])
    assert len(accepted) == 1 and len(rejected) == 1


def test_apply_subset_and_revert():
    text = "呃，今天就是就是，给大家讲一下这个工具，嗯……"
    result = clean(text)
    assert revert(result.text, result.edits) == text
    one = apply(text, result.edits[:1])          # undo all but the first edit
    assert one == text[2:]


def _real_segments():
    texts = []
    for f in sorted((ROOT / "experiments" / "asr").glob("*/*.json")):
        texts += [s["text"] for s in json.loads(f.read_text(encoding="utf-8"))]
    return texts


_PUNCT = re.compile(r"[\W_]+")


def _is_subsequence(small, big):
    it = iter(big)
    return all(c in it for c in small)


def _assert_clean_properties(texts):
    changed = 0
    for t in texts:
        r = clean(t)
        assert _is_subsequence(_PUNCT.sub("", r.text), _PUNCT.sub("", t)), t
        before = [t[a:b] for a, b, _ in protected_spans(t)]
        after_text = r.text
        for piece in before:
            assert piece in after_text, (t, piece)
        assert revert(r.text, r.edits) == t
        changed += bool(r.edits)
    assert changed > 0


def test_properties_on_boundary_combinations():
    texts = [f"{left}\n{right}" for left, _ in BOUNDARY for right, _ in BOUNDARY]
    _assert_clean_properties(texts)


def test_properties_on_real_transcripts():
    texts = _real_segments()
    if not texts:
        pytest.skip("Optional local transcript corpus is not included in the repository")
    assert len(texts) > 500
    _assert_clean_properties(texts)
