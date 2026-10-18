"""Public package example and attribution stay usable without private configuration."""
import json
from pathlib import Path

from vtx.frame import FrameStore
from vtx.outputs import OutputStore
from vtx.poster_templates import TemplateStore
from vtx.project import URL
from vtx.style_packages import validate_package


def test_documented_style_package_is_importable(tmp_path):
    example = Path(__file__).resolve().parents[2] / "docs" / "style-package.example.json"
    package = json.loads(example.read_text(encoding="utf-8"))
    layouts = TemplateStore(tmp_path / "layouts.json")
    style, templates, refs = validate_package(package, OutputStore(tmp_path / "outputs.json", layouts))
    assert style["kind"] == "free" and templates[0]["group"] == "article"
    assert refs == [templates[0]["id"]]


def test_default_image_and_ai_text_footer_include_project(tmp_path):
    frames = FrameStore(tmp_path / "frame.json")
    rendered = frames.render_all({"title": "示例", "url": "https://example.com/video"})
    assert rendered["footer"].count(URL) == 1
    assert rendered["poster_footer"].count(URL) == 1
    frames.update({"footer": "自定义页脚"})
    assert frames.render_all({})["footer"] == "自定义页脚"
