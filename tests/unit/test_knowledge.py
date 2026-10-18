"""T27 acceptance 1-4: structured generation, JSON tolerance, limits, quote verification."""
import json
import re

import pytest

from vtx.knowledge import generate, parse_json, verify_quotes, enforce_limits
from vtx.llm import LLMError, estimate_tokens

TEXT = ("为什么人类需要不断吃盐，而动物们却不用？谁说动物不用？长颈鹿就经常舔食钢管来获取盐分和矿物质。"
        "对牛牛来讲那可是九九成稀罕物。真就是90度是墙，89度是坡。"
        "正所谓吃一碗粉就得给一碗的钱，喝一份水就得喝一份的盐。")


def good_payload(**over):
    data = {
        "title": "为什么人类需要不断吃盐",
        "conclusion": "所有生物都离不开电解质。",
        "points": [
            {"heading": "动物也缺盐", "body": "长颈鹿舔钢管。", "key": False, "quotes": []},
            {"heading": "骆驼喂盐", "body": "出门前喂盐。", "key": True, "detail": ["做法：喂盐", "原因：越喂水越缺水"],
             "quotes": [{"quote": "喝一份水就得喝一份的盐。", "source": "喝一份水就得喝一份的盐。"}]},
            {"heading": "爬坝的羊", "body": "野山羊爬水坝。", "key": True,
             "quotes": [{"quote": "真就是 90 度是墙，89 度是坡。", "source": "真就是90度是墙，89度是坡。"}]},
        ],
    }
    data.update(over)
    return data


class FakeClient:
    def __init__(self, replies, context=32000):
        self.replies, self.context, self.calls = list(replies), context, []

    def chat(self, messages):
        self.calls.append(messages)
        reply = self.replies.pop(0) if self.replies else self.last
        self.last = reply
        return reply, {"total_tokens": 10}


# ---------- 1. JSON tolerance

def test_plain_json():
    assert parse_json(json.dumps(good_payload(), ensure_ascii=False))["title"] == "为什么人类需要不断吃盐"


def test_fenced_json_with_chatter():
    raw = "好的，下面是结果：\n```json\n" + json.dumps(good_payload(), ensure_ascii=False) + "\n```\n希望有帮助"
    assert parse_json(raw)["conclusion"].startswith("所有生物")


def test_bad_json_retried_once_then_error():
    c = FakeClient(["不是 JSON", "还是不是"])
    with pytest.raises(LLMError) as info:
        generate(c, TEXT, title="吃盐")
    assert info.value.code == "LLM_BAD_OUTPUT" and len(c.calls) == 2


def test_bad_json_then_good_on_retry():
    c = FakeClient(["oops", json.dumps(good_payload(), ensure_ascii=False)])
    result = generate(c, TEXT, title="吃盐")
    assert len(c.calls) == 2 and result.data["points"]


# ---------- 2. quote verification

def test_quote_passes_when_source_found():
    points, stats = verify_quotes(good_payload()["points"], TEXT)
    assert stats == {"kept": 2, "dropped": 0, "replaced": 0}
    assert points[1]["quotes"][0]["quote"] == "喝一份水就得喝一份的盐。"


def test_quote_dropped_when_source_not_in_text():
    pts = good_payload()["points"]
    pts[1]["quotes"] = [{"quote": "人一天必须吃十克盐。", "source": "人一天必须吃十克盐。"}]
    points, stats = verify_quotes(pts, TEXT)
    assert stats["dropped"] == 1 and points[1]["quotes"] == []


def test_quote_replaced_by_source_when_rewritten():
    pts = good_payload()["points"]
    pts[1]["quotes"] = [{"quote": "补水的时候一定要同时补充盐分。", "source": "喝一份水就得喝一份的盐。"}]
    points, stats = verify_quotes(pts, TEXT)
    assert stats["replaced"] == 1
    assert points[1]["quotes"][0]["quote"] == "喝一份水就得喝一份的盐。"


def test_quote_with_fixed_typo_is_kept():
    # model corrected a recognition error: "九九神" -> "九九成"; input already has 九九成 here, so check the reverse
    text = TEXT.replace("九九成", "九九神")
    pts = [{"heading": "h", "body": "b", "key": False,
            "quotes": [{"quote": "那可是九九成稀罕物。", "source": "那可是九九神稀罕物。"}]}]
    points, stats = verify_quotes(pts, text)
    assert stats["kept"] == 1 and points[0]["quotes"][0]["quote"] == "那可是九九成稀罕物。"


# ---------- 3. limits and no timestamps

def test_limits_enforced():
    many = [{"heading": f"要点{i}", "body": "内容。", "key": True,
             "quotes": [{"quote": "q", "source": "s"}] * 2} for i in range(14)]
    data = enforce_limits({"title": "t", "conclusion": "c", "points": many})
    assert len(data["points"]) == 10
    assert sum(p["key"] for p in data["points"]) == 3
    assert sum(len(p["quotes"]) for p in data["points"]) == 4


def test_timestamps_stripped():
    payload = good_payload()
    payload["points"][0]["body"] = "在 01:23 处讲到长颈鹿（0:41）舔钢管。"
    data = enforce_limits(payload)
    assert not re.search(r"\d+:\d\d", json.dumps(data, ensure_ascii=False))


def test_generate_end_to_end_single_call():
    c = FakeClient([json.dumps(good_payload(), ensure_ascii=False)])
    r = generate(c, TEXT, title="吃盐")
    assert len(c.calls) == 1 and r.chunks == 1
    assert r.quote_stats["kept"] == 2 and r.usage["total_tokens"] == 10
    prompt = c.calls[0][-1]["content"]
    assert "JSON" in prompt and TEXT in prompt


# ---------- 4. long text chunked then merged

def test_markdown_uses_full_text_poster_keeps_short_body():
    from vtx.knowledge import to_markdown

    data = enforce_limits({"title": "T", "conclusion": "C", "points": [
        {"heading": "H", "body": "短概括。", "text": "完整讲解：例子、数字和推理过程都在这里。", "key": False, "quotes": []}]})
    md = to_markdown(data, "来源")
    assert "完整讲解：例子、数字和推理过程都在这里。" in md and "短概括。" not in md
    assert data["points"][0]["body"] == "短概括。"          # posters render body, unchanged


def test_markdown_falls_back_to_body_without_text():
    from vtx.knowledge import to_markdown

    data = enforce_limits({"title": "T", "conclusion": "C", "points": [{"heading": "H", "body": "只有概括。", "key": False}]})
    assert "只有概括。" in to_markdown(data, "来源")


def test_prompt_asks_for_full_text():
    from vtx.knowledge import PROMPT

    assert '"text"' in PROMPT and "充分的阅读正文" in PROMPT


def test_long_text_chunked_and_merged():
    text = "\n\n".join(("段落%d。" % i) + TEXT * 6 for i in range(8))
    c = FakeClient([json.dumps(good_payload(), ensure_ascii=False)], context=6000)
    stages = []
    r = generate(c, text, title="长视频", progress=lambda *stage: stages.append(stage))
    assert r.chunks > 1 and len(c.calls) == r.chunks + 1
    assert stages[:r.chunks] == [("generate", index, r.chunks) for index in range(1, r.chunks + 1)]
    assert stages[r.chunks] == ("merge", 0, 0)
    assert "合并" in c.calls[-1][-1]["content"]
    data = r.data
    assert len(data["points"]) <= 10 and sum(p["key"] for p in data["points"]) <= 3
    for m in c.calls:
        assert estimate_tokens(m[-1]["content"]) <= int(6000 * 0.6)


def test_merge_too_big_fails_explicitly_when_model_will_not_reduce_it():
    text = "\n\n".join(("段落%d。" % i) + TEXT * 6 for i in range(8))
    payload = good_payload(points=[{"heading": "过长要点", "body": "内容", "text": "详" * 5000}])
    c = FakeClient([json.dumps(payload, ensure_ascii=False)], context=3000)
    with pytest.raises(LLMError) as error:
        generate(c, text, title="长视频")
    assert error.value.code == "LLM_TOO_LONG"
    for m in c.calls:
        assert estimate_tokens(m[-1]["content"]) <= int(3000 * 0.6)


def test_oversized_merge_keeps_last_source_fact_in_final_document():
    from vtx.knowledge import to_markdown

    class DetailedClient:
        context = 6000

        def __init__(self):
            self.calls = []

        def chat(self, messages):
            prompt = messages[-1]["content"]
            self.calls.append(prompt)
            assert estimate_tokens(prompt) <= int(self.context * 0.6)
            facts = list(dict.fromkeys(re.findall(r"FACT_\d+|TAIL_FACT", prompt)))
            is_merge = "<parts>" in prompt
            point = {"heading": " / ".join(facts), "body": " ".join(facts),
                     "text": " ".join(facts) + ("详" * 1400 if not is_merge else ""), "key": False}
            return json.dumps({"title": "长视频", "points": [point]}, ensure_ascii=False), {"total_tokens": 7}

    source = "\n\n".join(f"FACT_{i}：" + "源" * 1900 for i in range(6)) + "\nTAIL_FACT"
    c = DetailedClient()
    result = generate(c, source, title="长视频")
    assert result.chunks > 1 and len(c.calls) > result.chunks + 1
    document = to_markdown(result.data, "来源")
    assert all(f"FACT_{i}" in document for i in range(6))
    assert "TAIL_FACT" in document
    assert result.usage["total_tokens"] == 7 * len(c.calls)


def test_merged_too_many_points_cannot_silently_drop_tail():
    source = "源" * 4000
    many = good_payload(points=[{"heading": f"要点{i}", "body": "内容"} for i in range(11)])
    c = FakeClient([json.dumps(many, ensure_ascii=False)], context=6000)
    with pytest.raises(LLMError) as error:
        generate(c, source)
    assert error.value.code == "LLM_BAD_OUTPUT"


def test_hierarchical_merge_requires_final_json():
    class InvalidMergeClient(FakeClient):
        def chat(self, messages):
            if "<parts>" in messages[-1]["content"]:
                self.calls.append(messages)
                return "不是JSON", {"total_tokens": 10}
            return super().chat(messages)

    payload = good_payload(points=[{"heading": "详细要点", "body": "内容", "text": "详" * 2000}])
    c = InvalidMergeClient([json.dumps(payload, ensure_ascii=False)], context=6000)
    with pytest.raises(LLMError) as error:
        generate(c, "源" * 4000)
    assert error.value.code == "LLM_BAD_OUTPUT"
