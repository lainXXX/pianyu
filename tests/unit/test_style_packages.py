"""Portable styles remain independent and never partially persist on invalid input."""
import copy
import threading

import pytest
from fastapi.testclient import TestClient

from vtx.configuration import SettingsStore
from vtx.engines import EngineError
from vtx.outputs import OutputStore
from vtx.poster_templates import TemplateStore
from vtx.server import create_app
from vtx.style_packages import MAX_BYTES, export_package, import_package


@pytest.fixture
def stores(tmp_path):
    templates = TemplateStore(tmp_path / "layouts.json")
    outputs = OutputStore(tmp_path / "outputs.json", templates)
    outputs.list()
    return outputs, templates, threading.RLock()


@pytest.fixture
def package():
    return {"format": "pianyu-style", "version": 1,
            "style": {"name": "读书笔记", "kind": "free", "prompt": "整理：{title}\n{text}", "layouts": ["reading"]},
            "layouts": [{"id": "reading", "name": "清简", "group": "article", "width": 1080,
                         "height_mode": "fixed:1600", "html": '<html><h1>{{ title }}</h1><main class="content">'
                         "{% for block in blocks %}{{ block.html }}{% endfor %}</main></html>"}]}


def test_repeated_imports_are_independent_and_roundtrip(package, stores):
    outputs, layouts, lock = stores
    original = outputs.get("structure")
    one = import_package(package, *stores)
    two = import_package(package, *stores)
    assert one["output"] != two["output"] and one["layouts"] != two["layouts"]
    assert outputs.get(one["output"])["layouts"] == one["layouts"]
    layouts.update(one["layouts"][0], {"name": "仅修改第一个"})
    assert layouts.get(two["layouts"][0])["name"] == "清简"
    assert outputs.get("structure") == original
    exported = export_package(two["output"], *stores)
    assert exported["style"]["name"] == package["style"]["name"]
    assert exported["style"]["layouts"] == [exported["layouts"][0]["id"]]
    third = import_package(exported, *stores)
    assert outputs.get(third["output"])["prompt"] == package["style"]["prompt"]


@pytest.mark.parametrize("change", [
    lambda p: p.update(version=2),
    lambda p: p.update(version=True),
    lambda p: p.update(secrets={"key": "never-allowed"}),
    lambda p: p["style"].update(prompt="{unknown}"),
    lambda p: p["style"].update(kind="points"),
    lambda p: p["style"].update(layouts=[]),
    lambda p: p["layouts"].append(copy.deepcopy(p["layouts"][0])),
    lambda p: p["layouts"][0].update(id="../../local"),
    lambda p: p["layouts"][0].update(width=True),
    lambda p: p["layouts"][0].update(html="{{ missing_variable }}"),
    lambda p: p["layouts"][0].update(html="<script>alert(1)</script>"),
    lambda p: p["layouts"][0].update(html='<img src="https://evil.invalid/tracker">'),
    lambda p: p["layouts"][0].update(html="{{ title.__class__ }}"),
    lambda p: p["layouts"][0].update(html="{% for i in range(999999999) %}x{% endfor %}"),
    lambda p: p["layouts"][0].update(html="{{ 'x' * 999999999 }}"),
    lambda p: p["layouts"][0].update(html="{{ '%999999999s' | format(title) }}"),
    lambda p: p["layouts"][0].update(html="{{ title|replace('a','aaaaaaaaa') }}"),
    lambda p: p["layouts"][0].update(html="{{ title|join(title)|join(title) }}"),
    lambda p: p["layouts"][0].update(html="{% for b in blocks %}{% for x in blocks %}{{ x.html }}{% endfor %}{% endfor %}"),
    lambda p: p["layouts"][0].update(html="{% for b in blocks %}{{ b.html }}{% endfor %}"),
    lambda p: p["layouts"][0].update(html='<main class="content">{{ title }}</main>'),
])
def test_invalid_package_leaves_stores_unchanged(package, stores, change):
    outputs, layouts, _ = stores
    before = [store.list() for store in (outputs, layouts)]
    change(package)
    with pytest.raises(EngineError):
        import_package(package, *stores)
    assert [store.list() for store in (outputs, layouts)] == before


def test_all_builtin_styles_can_export_and_reimport(stores):
    outputs, _, _ = stores
    for style in outputs.list():
        imported = import_package(export_package(style["id"], *stores), *stores)
        assert outputs.get(imported["output"])["kind"] == style["kind"]


def test_failed_second_write_rolls_back_both_files(package, stores, monkeypatch):
    outputs, layouts, _ = stores
    before_output = outputs.path.read_bytes()
    assert not layouts.path.exists()

    def fail(_):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(outputs, "_save", fail)
    with pytest.raises(OSError):
        import_package(package, *stores)
    assert outputs.path.read_bytes() == before_output
    assert not layouts.path.exists()


def api_client(tmp_path, token=""):
    store = SettingsStore(tmp_path / ".env", tmp_path / "cookies", tmp_path / "app.json")
    return TestClient(create_app(tmp_path, store=store, token=token, run=lambda *_: None))


def test_api_auth_download_whitelist_and_independent_import(tmp_path, package):
    c = api_client(tmp_path, token="x" * 24)
    assert c.post("/api/style-packages/import", json=package).status_code == 401
    assert c.get("/api/outputs/structure/package").status_code == 401
    headers = {"Authorization": "Bearer " + "x" * 24}
    created = c.post("/api/style-packages/import", json=package, headers=headers)
    assert created.status_code == 200
    downloaded = c.get(f"/api/outputs/{created.json()['output']}/package", headers=headers)
    assert downloaded.status_code == 200
    assert "attachment" in downloaded.headers["content-disposition"]
    data = downloaded.json()
    assert set(data["style"]) == {"name", "kind", "prompt", "layouts"}
    assert set(data["layouts"][0]) == {"id", "name", "group", "html", "width", "height_mode"}
    assert "custom" not in downloaded.text and str(tmp_path) not in downloaded.text
    assert c.get("/api/outputs/missing/package", headers=headers).status_code == 404


def test_api_bounds_json_and_chunked_request(tmp_path, package):
    c = api_client(tmp_path)
    headers = {"Content-Type": "application/json"}
    assert c.post("/api/style-packages/import", content=b"not JSON", headers=headers).status_code == 422
    assert c.post("/api/style-packages/import", content=b"x" * (MAX_BYTES + 1), headers=headers).status_code == 413

    def chunks():
        for _ in range(17):
            yield b"x" * 65536

    assert c.post("/api/style-packages/import", content=chunks(), headers=headers).status_code == 413
    package["style"]["name"] = None
    assert c.post("/api/style-packages/import", json=package).status_code == 422
    assert c.get("/api/outputs").status_code == 200


def test_import_rejects_cross_site_simple_requests(tmp_path, package):
    import json

    c = api_client(tmp_path)
    before = c.get("/api/outputs").json()
    response = c.post("/api/style-packages/import", content=json.dumps(package),
                      headers={"Origin": "https://untrusted.invalid", "Content-Type": "text/plain"})
    assert response.status_code == 415
    assert c.get("/api/outputs").json() == before
