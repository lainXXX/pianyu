"""T45 acceptance: output templates (built-ins, edit / reset / hide, user templates, migration)."""
import json

import pytest

from vtx.engines import EngineError
from vtx.outputs import OutputStore
from vtx.poster_templates import TemplateStore


@pytest.fixture
def store(tmp_path):
    return OutputStore(tmp_path / "outputs.json", TemplateStore(tmp_path / "layouts.json"))


def test_builtins_complete(store):
    ids = [t["id"] for t in store.list()]
    assert ids == ["structure", "refine", "structured", "steps", "cards", "review", "debate", "news",
                   "notes", "qa", "actions", "article", "xhs", "chapters"]
    kinds = {t["id"]: t["kind"] for t in store.list()}
    assert kinds["structure"] == "structure" and kinds["refine"] == "source" and kinds["cards"] == "points"
    assert kinds["qa"] == "free"
    assert store.get("structured")["layouts"][:2] == ["A", "B"]
    assert "cover-qa" in store.get("structured")["layouts"]
    assert all(t["layouts"] for t in store.list())


def test_layouts_exist_and_match_kind(store, tmp_path):
    layouts = TemplateStore(tmp_path / "layouts.json")
    group = {"structure": "article", "source": "article", "free": "article", "points": "points"}
    for t in store.list():
        for lid in t["layouts"]:
            assert layouts.get(lid)["group"] == group[t["kind"]], (t["id"], lid)


def test_edit_reset_hide_builtin(store):
    store.update("cards", {"name": "复习卡", "layouts": ["cards"]})
    assert store.get("cards")["name"] == "复习卡" and store.get("cards")["modified"]
    store.reset("cards")
    assert store.get("cards")["name"] == "学习卡片"
    store.hide("xhs")
    assert "xhs" not in [t["id"] for t in store.list()] and store.get("xhs")["hidden"]
    store.hide("xhs", False)
    assert "xhs" in [t["id"] for t in store.list()]
    with pytest.raises(EngineError):
        store.delete("refine")


def test_validation(store):
    with pytest.raises(EngineError):
        store.update("structure", {"prompt": "没有句子变量"})              # {sentences} required
    with pytest.raises(EngineError):
        store.update("structured", {"layouts": ["book"]})                  # article layout on points
    with pytest.raises(EngineError):
        store.update("notes", {"layouts": []})
    with pytest.raises(EngineError):
        store.add({"kind": "nope", "name": "x", "prompt": "x"})


def test_user_template_copy_and_delete(store):
    oid = store.add({"name": "我的测评"}, copy_of="review")
    t = store.get(oid)
    assert t["kind"] == "points" and t["prompt"] == store.get("review")["prompt"] and not t["builtin"]
    free = store.add({"kind": "free", "name": "周报", "prompt": "写成周报：{text}"})
    assert store.get(free)["layouts"] == ["book"]
    store.delete(oid)
    with pytest.raises(EngineError):
        store.get(oid)


def test_old_prompts_migrated(tmp_path):
    legacy = tmp_path / "prompts.json"
    legacy.write_text(json.dumps({"overrides": {}, "custom": [
        {"id": "p-1", "name": "旧自定义", "template": "翻译：{text}", "group": "custom"},
        {"id": "p-2", "name": "旧文本", "template": "整理：{paragraphs}", "group": "text"}]}), encoding="utf-8")
    store = OutputStore(tmp_path / "outputs.json", TemplateStore(tmp_path / "l.json"), legacy)
    assert (store.get("p-1")["kind"], store.get("p-2")["kind"]) == ("free", "source")
    assert (tmp_path / "outputs.json").exists()
