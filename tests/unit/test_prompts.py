"""T36 acceptance: prompt groups (text / poster / custom)."""
import json

import pytest

from vtx.engines import EngineError
from vtx.prompts import PromptStore, render


def test_render_variables():
    assert render("《{title}》要点：{text}", text="正文", title="标题") == "《标题》要点：正文"


def test_render_appends_text_when_missing():
    assert render("请总结", text="正文") == "请总结\n\n正文"


def test_render_single_pass_and_keeps_json_braces():
    out = render('A {text} B {"n": 1}', text="包含 {text} 和 {title} 的原文", title="T")
    assert out == 'A 包含 {text} 和 {title} 的原文 B {"n": 1}'


@pytest.fixture
def store(tmp_path):
    return PromptStore(tmp_path / "prompts.json")


def test_builtins_per_group(store):
    assert [(p["id"], p["group"]) for p in store.list()] == [("refine", "text"), ("structured", "poster")]
    assert store.list("custom") == []
    assert "{paragraphs}" in store.get("refine")["template"] and '{"n": 编号' in store.get("refine")["template"]


def test_edit_and_reset_builtin(store):
    store.update("refine", "我的精校", store.get("refine")["template"] + "\n额外要求")
    assert store.get("refine")["modified"] and store.get("refine")["name"] == "我的精校"
    store.reset("refine")
    assert not store.get("refine")["modified"]
    with pytest.raises(EngineError):
        store.delete("refine")


def test_required_variables_per_group(store):
    with pytest.raises(EngineError):
        store.add("没有段落", "请精校", group="text")
    with pytest.raises(EngineError):
        store.add("没有文本", "请总结要点", group="poster")
    store.add("自由", "随便写", group="custom")         # custom: {text} appended automatically
    with pytest.raises(EngineError):
        store.add("x", "y", group="nope")


def test_custom_crud(store):
    pid = store.add("翻译日文", "翻译成日语：{text}")
    assert store.get(pid)["group"] == "custom" and not store.get(pid)["builtin"]
    store.update(pid, "翻译日语", "日本語に翻訳：{text}")
    assert store.get(pid)["name"] == "翻译日语"
    store.delete(pid)
    with pytest.raises(EngineError):
        store.get(pid)
    with pytest.raises(EngineError):
        store.reset(pid)


def test_old_file_without_groups_still_loads(tmp_path):
    path = tmp_path / "prompts.json"
    path.write_text(json.dumps({"overrides": {"quotes": {"name": "x", "template": "y"}},
                                "custom": [{"id": "p-1", "name": "旧的", "template": "{text}"}]}), encoding="utf-8")
    store = PromptStore(path)
    assert store.get("p-1")["group"] == "custom"
    assert [p["id"] for p in store.list()] == ["refine", "structured", "p-1"]    # stale override ignored


@pytest.mark.parametrize("name,template", [("", "x"), ("n", ""), ("n", "x" * 20001)])
def test_validation(store, name, template):
    with pytest.raises(EngineError):
        store.add(name, template)
