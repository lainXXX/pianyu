"""Rule snapshots, numeric equivalence and truthful source guards."""
import json

import pytest

from vtx.asr import Segment
from vtx.knowledge import generate
from vtx.llm import process
from vtx.proofread import proofread
from vtx.refine import guard, refine
from vtx.server import SettingsStore
from vtx.structure import structure
from vtx.system_prompt import DEFAULT_SYSTEM_PROMPT, EditorialClient


@pytest.mark.parametrize("before,after", [
    ("V四点一flash比V四pro更快。", "V4.1 Flash 比 V4 Pro 更快。"),
    ("程序化三D城市。", "程序化 3D 城市。"),
    ("GLM五点三flash生成的效果。", "GLM 5.3 Flash 生成的效果。"),
    ("每次一点五毫升，不要超过三次。", "每次 1.5 毫升，不要超过 3 次。"),
    ("GPU运行十二秒。", "GPU 运行 12 秒。"),
])
def test_equivalent_writing_survives_guard(before, after):
    result, restored, _ = guard(before, after)
    assert result == after and not restored


@pytest.mark.parametrize("before,after", [
    ("版本V四点一flash。", "版本 V4.2 Flash。"),
    ("每次12毫升，不要超过三次。", "每次21毫升，要超过三次。"),
    ("每次1.5毫升。", "每次15毫升。"),
    ("每次15毫升。", "每次1.5毫升。"),
    ("每次12毫升。", "每次132毫升。"),
    ("可以使用。", "不可以使用。"),
    ("温度高于20度。", "温度低于20度。"),
    ("条件 x < 3。", "条件 x > 3。"),
    ("用V四pro。", "用V5 Pro。"),
    ("不要使用这个模型。", "使用这个模型。"),
    ("运行 `v四`。", "运行 `v4`。"),
    ("访问 https://example.com/v四", "访问 https://example.com/v4"),
])
def test_fact_changes_still_rejected(before, after):
    result, restored, _ = guard(before, after)
    assert result != after and restored


def test_rules_persist_without_overwriting_proxy(tmp_path):
    store = SettingsStore(tmp_path / "unused.env", tmp_path, tmp_path / "settings.json")
    assert store.system_prompt() == DEFAULT_SYSTEM_PROMPT
    store.set_proxy("http://127.0.0.1:8888")
    store.set_system_prompt("通用规则")
    store.set_proxy("socks5://127.0.0.1:9999")
    reopened = SettingsStore(store.env_file, tmp_path, store.settings_file)
    assert reopened.system_prompt() == "通用规则"
    assert reopened.proxy() == "socks5://127.0.0.1:9999"


class Capture:
    context = 4000

    def __init__(self, reply="结果"):
        self.calls = []
        self.reply = reply

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply(messages[-1]["content"]) if callable(self.reply) else self.reply, {}


def assert_system(c):
    assert c.calls
    assert all(call[0] == {"role": "system", "content": "固定规则"} for call in c.calls)


def test_free_chunks_and_merge_all_use_same_snapshot():
    c = Capture()
    client = EditorialClient(c, "固定规则")
    r = process(client, "整理{text}", "\n\n".join("内容" * 800 for _ in range(6)))
    assert r.chunks > 1 and len(c.calls) == r.chunks + 1
    assert_system(c)


def test_points_chunks_retry_and_merge_use_same_snapshot():
    data = json.dumps({"title": "标题", "conclusion": "结论", "points": []}, ensure_ascii=False)
    c = Capture(data)
    r = generate(EditorialClient(c, "固定规则"), "\n\n".join("内容" * 800 for _ in range(5)), template="整理{text}")
    assert r.chunks > 1 and len(c.calls) == r.chunks + 1
    assert_system(c)


def test_source_and_structure_batches_use_rules_and_keep_raw_source():
    def echo(prompt):
        return json.dumps(json.loads(prompt), ensure_ascii=False)
    c = Capture(echo)
    original = "程序化三D城市。"
    r = refine(EditorialClient(c, "固定规则"), [Segment(0, 0, 1, original)], template="{paragraphs}")
    assert r.paragraphs[0]["original"] == original
    assert_system(c)

    def arrangement(prompt):
        ids = [s["i"] for s in json.loads(prompt)]
        return json.dumps({"sections": [{"blocks": [{"type": "p", "ids": ids}]}]})
    c = Capture(arrangement)
    structure(EditorialClient(c, "固定规则"), [original] * 1000, template="{sentences}")
    assert len(c.calls) > 1
    assert_system(c)


def test_proofreading_receives_current_hypothesis_rule_snapshot(tmp_path):
    store = SettingsStore(tmp_path / "unused.env", tmp_path, tmp_path / "settings.json")
    rules = store.system_prompt()
    assert "测算的假设前提须紧随相关数字" in rules
    assert "不把假设人口、规模或条件归给随后出现的案例对象" in rules
    # Capture the missing proofreading seam, not model compliance with the rule.
    source = "假设常住人口若干，可能仅在节日期间达到设计处理量。\n\n"
    c = Capture('{"patches":[]}')
    c.context = 16000
    proofread(EditorialClient(c, rules), {"markdown_body": source}, source)
    assert len(c.calls) == 1
    assert c.calls[0][0] == {"role": "system", "content": rules}
    assert "SOURCE_BACKED_PROOFREAD_V1" in c.calls[0][-1]["content"]
    assert "假设常住人口" in c.calls[0][-1]["content"]
