"""T28 acceptance 1-3: refined transcript, per-paragraph guard, lossless reconstruction."""
import json

import pytest

from vtx.asr import Segment
from vtx.refine import CHAR_LIMIT, group_paragraphs, judge, refine

SEGS = [
    Segment(0, 0, 3, "呃，为什么人类需要不断吃盐，而动物们却不用水？"),
    Segment(1, 3, 7, "说动物不用长颈鹿就经常舔食钢管来获取盐分和矿物质，有时候也会舔人。"),
    Segment(2, 7, 9, "对牛牛来讲那可是九九神稀罕物。"),
    Segment(3, 9, 12, "那那块沾了尿的青草就是牛最好的零食。"),
]


class FakeClient:
    """Answers the refine prompt by applying `fix` to every [n] paragraph it receives."""

    def __init__(self, fix, context=32000, mangle=None):
        self.fix, self.context, self.mangle, self.calls = fix, context, mangle, []

    def chat(self, messages):
        prompt = messages[-1]["content"]
        self.calls.append(prompt)
        body = prompt.rsplit("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0]
        paras = json.loads(body)
        out = [{"n": p["n"], "text": self.fix(p["text"])} for p in paras]
        if self.mangle:
            out = self.mangle(out)
        return json.dumps(out, ensure_ascii=False), {"total_tokens": 7}


def good_fix(t):
    return (t.replace("呃，", "").replace("不用水？", "不用？").replace("说动物不用", "谁说动物不用？")
             .replace("九九神", "九九成").replace("那那块", "那块"))


def test_group_paragraphs_lossless_and_bounded():
    many = [Segment(i, i, i + 1, "这是一句比较长的话，" * 20) for i in range(30)]
    paras = group_paragraphs(many)
    assert "".join(p["text"] for p in paras) == "".join(s.text for s in many)
    assert all(len(p["text"]) <= CHAR_LIMIT + 200 for p in paras)
    assert [p["n"] for p in paras] == list(range(1, len(paras) + 1))


@pytest.mark.parametrize("orig,new,ok", [
    ("呃，为什么人类需要不断吃盐，而动物们却不用水？", "为什么人类需要不断吃盐，而动物们却不用？", True),
    ("对牛牛来讲那可是九九神稀罕物。", "对牛牛来讲那可是九九成稀罕物。", True),
    ("对牛牛来讲那可是九九神稀罕物。", "牛非常喜欢盐，这对它们来说是很难得的东西。", False),    # rewritten
    ("那那块沾了尿的青草就是牛最好的零食。", "青草。", False),                                  # too short
])
def test_judge(orig, new, ok):
    assert judge(orig, new)[0] is ok


from vtx.refine import guard


def test_dropped_content_word_is_put_back():
    """T31 user feedback: '肌肉收缩' -> '收缩' loses meaning and must not pass."""
    src = "钠离子参与神经信号传递、肌肉收缩和维持细胞内外渗透压。"
    text, restored, _ = guard(src, "钠离子参与神经信号传递、收缩和维持细胞内外渗透压。")
    assert text == src and len(restored) == 1 and "肌肉" in restored[0]


def test_bad_change_restores_whole_clause_not_a_mix():
    """T31: patching single characters back produced garbage like '那我知我想必'. A clause with a
    disallowed change is taken from the source as a whole; other clauses keep the model's fixes."""
    src = "那什么是机器学习呢？那我知我想必大家都听过，对牛牛来讲那可是九九神稀罕物。"
    new = "那什么是机器学习呢？我想大家都听过这个，对牛牛来讲那可是九九成稀罕物。"
    text, restored, _ = guard(src, new)
    assert "九九成稀罕物" in text                                   # good clause kept
    assert "那我知我想必大家都听过" in text                          # bad clause verbatim
    assert "我想大家都听过这个" not in text


def test_invented_digit_rejected():
    src = "这个函式的输出是明天中午的pm 2.5的数值。"
    assert guard(src, "这个函式的输出是明天中午的12.5的数值。")[0] == src


def test_false_start_removed_but_content_word_kept():
    src = "那什么是机器学习呢？那我知我想必大家都听过。"
    new = "那什么是机器学习呢？我想必大家都听过。"
    assert guard(src, new)[0] == new
    src2 = "神经信号传递、肌肉收缩都需要钠。"
    assert guard(src2, "神经信号传递、收缩都需要钠。")[0] == src2


def test_misheard_english_term_fix_allowed():
    src = "现在每个人都有居面count，那gmail account会做垃圾邮件过滤。"
    new = "现在每个人都有gmail account，那gmail account会做垃圾邮件过滤。"
    assert guard(src, new)[0] == new


def test_typo_fix_by_context_is_kept():
    src = "很有可能它是出汗几支。对牛牛来讲那可是九九神稀罕物。"
    text, restored, _ = guard(src, "很有可能它是出汗体质。对牛牛来讲那可是九九成稀罕物。")
    assert text == "很有可能它是出汗体质。对牛牛来讲那可是九九成稀罕物。" and restored == []


def test_fillers_stutters_and_punctuation_may_go():
    src = "骆驼就会顿顿顿地喝水呃，这样就能储存水分"
    text, restored, _ = guard(src, "骆驼就会顿顿地喝水，这样就能储存水分。")
    assert text == "骆驼就会顿顿地喝水，这样就能储存水分。" and restored == []


@pytest.mark.parametrize("src,new", [
    ("我不建议直接喝水。", "我建议直接喝水。"),            # negation dropped
    ("每次服用1.5毫升。", "每次服用15毫升。"),              # decimal point dropped
    ("每次服用1.5毫升。", "每次服用2毫升。"),                # number replaced
])
def test_negation_and_numbers_protected(src, new):
    assert guard(src, new)[0] == src


@pytest.mark.parametrize("src,new", [
    ("计算结果为12×3。", "计算结果为12÷3。"),
    ("计算结果为12/3。", "计算结果为12*3。"),
    ("计算结果为12乘以3。", "计算结果为12除以3。"),
    ("计算结果为12除3。", "计算结果为12乘3。"),
    ("计算结果为12×3。", "计算结果为12 3。"),
    ("参数分别为12和3。", "参数分别为12÷和3。"),
])
def test_arithmetic_relation_cannot_change_even_when_numbers_match(src, new):
    assert guard(src, new)[0] == src


def test_arithmetic_relation_change_restores_only_affected_clause():
    src = "计算结果为12×3，对牛牛来讲那可是九九神稀罕物。"
    new = "计算结果为12÷3，对牛牛来讲那可是九九成稀罕物。"
    text, restored, _ = guard(src, new)
    assert text == "计算结果为12×3，对牛牛来讲那可是九九成稀罕物。"
    assert restored == ["计算结果为12×3，"]


def test_arithmetic_relation_survives_editorial_spacing():
    src = "计算结果为12×3。"
    new = "计算结果为 12 × 3。"
    assert guard(src, new) == (new, [], [])


def test_listed_irrelevant_sentence_removed_only_if_listed_and_real():
    src = "这就是电解质的道理。说出武名吓人一跳。"
    text, restored, removed = guard(src, "这就是电解质的道理。", ["说出武名吓人一跳。"])
    assert text == "这就是电解质的道理。" and removed == ["说出武名吓人一跳。"] and restored == []
    # not listed -> put back
    text, restored, removed = guard(src, "这就是电解质的道理。", [])
    assert "说出武名吓人一跳" in text and removed == []
    # listed but not in the source -> ignored
    assert guard(src, "这就是电解质的道理。", ["编造的句子不存在。"])[2] == []


def test_explicit_long_outro_removed_without_restoring_unrelated_content():
    main = "这个模型生成的城市结构清晰。"
    outro = ["这期视频发在大号上面。", "后面的小想法会放在小号，方便大家及时沟通。", "大家也可以关注一下我的小号，谢谢观看。"]
    text, restored, removed = guard(main + "".join(outro), main, outro)
    assert text == main and restored == [] and removed == outro
    assert guard(main + "".join(outro), main)[0] != main


def test_long_insertions_rejected():
    src = "牛喜欢盐。"
    text, _, _ = guard(src, "牛喜欢盐，因为盐对牛的健康非常重要。")
    assert "健康" not in text and _plain_chars(text) == _plain_chars(src)


def test_short_greeting_can_be_removed_but_partial_condition_cannot():
    assert guard("大家好。保留主要内容。谢谢。", "保留主要内容。", ["大家好。", "谢谢。"]) == (
        "保留主要内容。", [], ["大家好。", "谢谢。"])
    source = "每次服用1.5毫升，不要超过三次。"
    assert guard(source, "每次服用1.5毫升。", ["不要超过三次。"])[0] == source


def test_refine_accepts_leading_greeting_and_an_entire_outro_paragraph():
    class Filtered:
        context = 32000

        def chat(self, messages):
            paragraphs = json.loads(messages[-1]['content'].rsplit('<transcript>\n', 1)[1]
                                    .split('\n</transcript>', 1)[0])
            replies = []
            for paragraph in paragraphs:
                text = paragraph['text']
                removed = [sentence for sentence in ['大家好。', '大家也可以关注一下我的小号。']
                           if sentence in text]
                for sentence in removed:
                    text = text.replace(sentence, '', 1)
                replies.append({'n': paragraph['n'], 'text': text, 'removed': removed})
            return json.dumps(replies, ensure_ascii=False), {}

    result = refine(Filtered(), [{'text': '大家好。模型耗时12秒。'},
                                {'text': '大家也可以关注一下我的小号。'}], char_limit=16)
    assert result.text == '模型耗时12秒。'
    assert result.stats['reverted'] == 0 and len(result.stats['removed']) == 2


def _plain_chars(s):
    import re
    return re.sub(r"[\W_]+", "", s)


def test_normal_refine_passes():
    r = refine(FakeClient(good_fix), SEGS)
    assert all(p["status"] == "ok" for p in r.paragraphs)
    text = r.text
    assert "呃" not in text and "九九成" in text and "那那块" not in text and "谁说动物不用？" in text


def test_overrewritten_paragraph_reverted_and_marked():
    def fix(t):
        return "牛非常喜欢盐，这对它们来说是很难得的东西。" if "九九神" in t else good_fix(t)

    segs = [Segment(i, i, i + 1, s.text) for i, s in enumerate(SEGS)]
    r = refine(FakeClient(fix), segs, char_limit=30)          # one sentence per paragraph
    bad = [p for p in r.paragraphs if p["status"] == "reverted"]
    assert len(bad) == 1 and bad[0]["text"] == bad[0]["original"] and "九九神" in bad[0]["text"]
    assert r.stats["reverted"] == 1


@pytest.mark.parametrize("mangle", [
    lambda out: out[:-1],                                              # dropped a paragraph
    lambda out: [{"n": 1, "text": "".join(p["text"] for p in out)}],   # merged everything
    lambda out: [{"n": p["n"] + 1, "text": p["text"]} for p in out],   # numbering shifted
])
def test_batch_reverted_when_paragraphs_dont_line_up(mangle):
    r = refine(FakeClient(good_fix, mangle=mangle), SEGS, char_limit=30)   # one sentence per paragraph
    assert len(r.paragraphs) > 1
    assert all(p["status"] == "reverted" for p in r.paragraphs)
    assert "".join(p["text"] for p in r.paragraphs) == "".join(s.text for s in SEGS)


def test_non_json_reply_reverts_batch():
    class Broken(FakeClient):
        def chat(self, messages):
            self.calls.append(messages[-1]["content"])
            return "抱歉，我无法处理。", {"total_tokens": 1}

    r = refine(Broken(good_fix), SEGS)
    assert all(p["status"] == "reverted" for p in r.paragraphs)


def test_rules_strip_fillers_before_model_even_if_model_does_nothing():
    r = refine(FakeClient(lambda t: t), SEGS)
    assert "呃" not in r.text and "那那块" not in r.text
    assert all(p["status"] == "ok" for p in r.paragraphs)
    assert "".join(p["original"] for p in r.paragraphs) == "".join(s.text for s in SEGS)   # raw kept for diff


def test_inner_line_breaks_become_paragraphs():
    def fix(t):
        return good_fix(t).replace("有时候也会舔人。", "有时候也会舔人。\n")

    r = refine(FakeClient(fix), SEGS)
    assert r.paragraphs[0]["status"] == "ok"
    assert r.text.count("\n\n") >= 1 and "有时候也会舔人。\n\n对牛牛" in r.text


def test_paragraph_count_and_reconstruction():
    many = [Segment(i, i, i + 1, f"第{i}句，呃，说的是同一件事。") for i in range(40)]
    r = refine(FakeClient(lambda t: t.replace("呃，", "")), many, char_limit=80)
    assert len(r.paragraphs) == len(group_paragraphs(many, char_limit=80))
    assert "".join(p["original"] for p in r.paragraphs) == "".join(s.text for s in many)
    assert all(p["diff"] is not None for p in r.paragraphs)
    rebuilt = "".join(op[2] for p in r.paragraphs for op in p["diff"] if op[0] in ("equal", "delete", "replace"))
    assert rebuilt == "".join(s.text for s in many)


def test_batches_capped_even_with_huge_context():
    """Output is ~as long as input; a big batch would hit the model's output limit (T31 regression)."""
    from vtx.llm import estimate_tokens
    from vtx.refine import BATCH_TOKENS

    many = [Segment(i, i, i + 1, "这是一句话。" * 40) for i in range(60)]
    c = FakeClient(lambda t: t, context=128000)
    refine(c, many)
    assert len(c.calls) > 1 and all(estimate_tokens(p) <= BATCH_TOKENS for p in c.calls)


def test_batches_respect_context_budget():
    many = [Segment(i, i, i + 1, "这是一句话。" * 40) for i in range(60)]
    c = FakeClient(lambda t: t, context=4000)
    refine(c, many)
    from vtx.llm import estimate_tokens
    assert len(c.calls) > 1 and all(estimate_tokens(p) <= int(4000 * 0.6) for p in c.calls)
