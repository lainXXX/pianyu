"""T42 acceptance: 结构化原文 rebuilds the body from original sentences, whatever the model replies."""
import json

import pytest

from vtx.structure import build, split_sentences, structure, to_markdown

LINES = ["大家好，欢迎来到我的频道。长颈鹿会舔钢管获取盐分。",
         "牛圈里要放舔砖。骆驼出门前要喂盐。",
         "直接喂水会越喂越缺水。钠离子参与肌肉收缩。",
         "出汗几支的人会吸引蝴蝶。记得点赞关注。"]
S = split_sentences(LINES)   # 8 sentences


def body(doc):
    return [s for sec in doc["sections"] for b in sec["blocks"] for it in b["items"] for s in it]


def test_split_sentences():
    assert len(S) == 8 and S[0] == "大家好，欢迎来到我的频道。" and S[-1] == "记得点赞关注。"


def test_good_reply_reorders_lists_fixes_and_bolds():
    reply = {"sections": [
        {"heading": "动物也要盐", "level": 2, "blocks": [{"type": "list", "items": [[2], [3]]}]},
        {"heading": "骆驼喂盐不喂水", "blocks": [{"type": "p", "ids": [5, 4]}]},     # conclusion first
        {"heading": "钠的作用", "blocks": [{"type": "p", "ids": [6, 7]}]}],
        "removed": [1], "fixes": {"7": "出汗体质的人会吸引蝴蝶。"}, "key": [5]}   # cap: max(1, 10% of 8)
    secs, st = build(S, reply, range(8))
    doc = {"sections": secs}
    texts = [s["text"] for s in body(doc)]
    assert "大家好，欢迎来到我的频道。" not in texts and st["removed"] == ["大家好，欢迎来到我的频道。"]
    assert "出汗体质的人会吸引蝴蝶。" in texts and st["fixed"][0]["old"] == "出汗几支的人会吸引蝴蝶。"
    assert texts.index("直接喂水会越喂越缺水。") < texts.index("骆驼出门前要喂盐。") and st["moved"] == 1
    assert secs[0]["blocks"][0]["type"] == "list"
    md = to_markdown(doc, "吃盐", "来源")
    assert md.startswith("# 吃盐\n\n## 动物也要盐\n\n- 长颈鹿会舔钢管获取盐分。\n- 牛圈里要放舔砖。")
    assert "**直接喂水会越喂越缺水。**" in md and md.endswith("---\n来源\n")
    # sentence 8 (点赞关注) was not listed anywhere: put back, not lost
    assert st["restored"] == 1 and "记得点赞关注。" in texts


def test_every_char_comes_from_the_source():
    """The model cannot inject text: invented ids, rewrites and new content are all ignored."""
    reply = {"sections": [{"heading": "总结", "blocks": [{"type": "p", "ids": [2, 99, "x", 2, 3]}]}],
             "removed": [], "fixes": {"2": "长颈鹿其实最爱吃盐了，每天要吃十克。", "4": "骆驼出门前要喂盐，而且要喂很多。"},
             "key": []}
    secs, st = build(S, reply, range(8))
    texts = [s["text"] for s in body({"sections": secs})]
    assert sorted(texts) == sorted(S) and len(texts) == 8          # nothing lost, nothing duplicated
    assert st["rejected_fixes"] == 2 and st["restored"] == 6


def test_fix_may_not_change_numbers_or_negations():
    s = ["每次服用1.5毫升，不要超过三次。"]
    for bad in ("每次服用15毫升，不要超过三次。", "每次服用1.5毫升，要超过三次。", "每次服用2.5毫升，不要超过三次。"):
        secs, st = build(s, {"sections": [{"blocks": [{"type": "p", "ids": [1]}]}], "fixes": {"1": bad}}, range(1))
        assert body({"sections": secs})[0]["text"] == s[0] and st["rejected_fixes"] == 1


def test_long_explicit_outro_is_removed_and_missing_main_sentence_restored():
    sentences = ["模型生成的城市结构清晰。", "同样设置下运行时间为12秒。",
                 "这期放在大号上面。", "小想法以后会发在小号。", "欢迎关注我的小号。", "谢谢观看，下期再见。"]
    reply = {"sections": [{"blocks": [{"type": "p", "ids": [1]}]}], "removed": [3, 4, 5, 6]}
    secs, st = build(sentences, reply, range(len(sentences)))
    assert [item["text"] for item in body({"sections": secs})] == sentences[:2]
    assert st["removed"] == sentences[2:] and st["restored"] == 1


def test_heading_with_invented_number_dropped():
    reply = {"sections": [{"heading": "3个步骤", "blocks": [{"type": "p", "ids": list(range(1, 9))}]}]}
    secs, _ = build(S, reply, range(8))
    assert secs[0]["heading"] == ""


class FakeClient:
    context = 64000

    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def chat(self, messages):
        self.prompts.append(messages[-1]["content"])
        return self.replies.pop(0), {"total_tokens": 10}


def test_structure_end_to_end_and_retry_on_bad_json():
    good = json.dumps({"sections": [{"heading": "盐", "blocks": [{"type": "p", "ids": list(range(2, 9))}]}],
                       "removed": [1], "fixes": {}, "key": []}, ensure_ascii=False)
    client = FakeClient(["不是 JSON", good])
    r = structure(client, LINES, "吃盐")
    assert len(client.prompts) == 2 and '"i": 1' in client.prompts[0] and "<video_title>吃盐</video_title>" in client.prompts[0]
    assert r.stats["removed"] == ["大家好，欢迎来到我的频道。"] and r.usage["total_tokens"] == 20
    assert r.text.startswith("## 盐\n\n长颈鹿会舔钢管获取盐分。")


def test_structure_all_bad_raises():
    from vtx.llm import LLMError

    with pytest.raises(LLMError):
        structure(FakeClient(["x", "y"]), LINES)


def test_long_text_is_batched_and_order_kept_across_batches(monkeypatch):
    monkeypatch.setattr("vtx.structure.BATCH_TOKENS", 900)
    lines = [f"第{i}句讲的是一个很长的知识点，内容需要占用一些字数才能分批。" for i in range(1, 80)]

    def reply_for(prompt):
        ids = [x["i"] for x in json.loads(prompt.rsplit("<transcript>\n", 1)[1].split("\n</transcript>", 1)[0])]
        return json.dumps({"sections": [{"heading": "节", "blocks": [{"type": "p", "ids": ids[::-1]}]}]})

    class C(FakeClient):
        def chat(self, messages):
            self.prompts.append(messages[-1]["content"])
            return reply_for(messages[-1]["content"]), {}

    c = C([])
    r = structure(c, lines)
    assert r.stats["batches"] == len(c.prompts) > 1
    ids = [s["id"] for s in body(r.doc)]
    # reversed inside each batch, but batches stay in order
    firsts = [sec["blocks"][0]["items"][0][-1]["id"] for sec in r.doc["sections"]]
    assert firsts == sorted(firsts) and len(ids) == 79
