"""T37 acceptance: poster templates (store, sandbox, sanitizer, preview data)."""
import pytest

from vtx.engines import EngineError
from vtx.poster import render_html
from vtx.poster_templates import SAMPLE, SAMPLE_META, TemplateStore, parse_height, sanitize


@pytest.fixture
def store(tmp_path):
    return TemplateStore(tmp_path / "templates.json")


def test_builtins_and_copy_with_changes(store):
    assert [t["id"] for t in store.list("points")][:2] == ["A", "B"]
    assert [t["id"] for t in store.list("article")] == ["book", "magazine", "night", "print"]
    tid = store.add({"name": "绿色竖版"}, copy_of="A")
    t = store.get(tid)
    assert t["width"] == 1080 and not t["builtin"] and t["html"] == store.get("A")["html"]
    store.update(tid, {"html": t["html"].replace("#f4efe6", "#e8f5e9"), "width": 900})
    html = render_html(SAMPLE, SAMPLE_META, store.get(tid), footer="页脚")
    assert "#e8f5e9" in html and "为什么骆驼出门前喂盐" in html and "<div>页脚</div>" in html


def test_builtin_edit_reset_and_no_delete(store):
    store.update("A", {"width": 1200})
    assert store.get("A")["width"] == 1200 and store.get("A")["modified"]
    store.reset("A")
    assert store.get("A")["width"] == 1080
    with pytest.raises(EngineError):
        store.delete("B")


def test_new_points_layout_uses_own_minimal_skeleton(store):
    original = store.get('A')
    tid = store.add({'name': '全新要点', 'group': 'points'})
    created = store.get(tid)
    assert created['html'] != original['html']
    assert 'for p in points' in created['html']
    assert SAMPLE['title'] in render_html(SAMPLE, SAMPLE_META, created, footer='')
    assert store.get('A') == original


def test_sanitizer_strips_active_and_external_content():
    dirty = ('<div onclick="x()" style="background:url(https://evil/x.png)"><script>alert(1)</script>'
             '<img src="https://evil/p.png"><a href="javascript:alert(1)">a</a><iframe src="x"></iframe>'
             '<style>@import url(https://evil/c.css);</style></div>')
    clean = sanitize(dirty)
    for bad in ("onclick", "<script", "evil", "javascript:", "<iframe", "@import"):
        assert bad not in clean


def test_template_cannot_escape_sandbox_or_inject(store):
    with pytest.raises(EngineError):
        store.add({"name": "x", "html": "{{ ''.__class__.__mro__[1].__subclasses__() }}"})
    with pytest.raises(EngineError):
        store.add({"name": "x", "html": "{% for p in points %}"})                 # syntax error
    tid = store.add({"name": "x", "html": "<p>{{ title }}</p><script>1</script>"})
    data = dict(SAMPLE, title="<b>标题</b>")
    html = render_html(data, SAMPLE_META, store.get(tid), footer="")
    assert html == "<p>&lt;b&gt;标题&lt;/b&gt;</p>"                               # data is escaped, script removed


@pytest.mark.parametrize("mode,ok", [("auto", None), ("fixed:1350", 1350), ("fixed:10", "err"), ("tall", "err")])
def test_height_modes(mode, ok):
    if ok == "err":
        with pytest.raises(EngineError):
            parse_height(mode)
    else:
        assert parse_height(mode) == ok


def test_validation(store):
    for bad in ({"name": ""}, {"width": 100}, {"height_mode": "x"}, {"html": ""}):
        with pytest.raises(EngineError):
            store.add({"name": "n", **bad})
