"""T35 acceptance: editable header / footer templates."""
import pytest

from vtx.frame import DEFAULTS, FrameStore, clean_url, render

META = {"platform": "bilibili", "video_id": "BV1wFZ8YBEt4", "part": 1, "title": "吃盐", "author": "某UP",
        "url": "https://www.bilibili.com/video/BV1wFZ8YBEt4/?trackid=xx&spm_id_from=yy", "duration": 197.0,
        "engine": "dashscope/paraformer-v2"}


def test_default_footer_has_author_and_clean_link():
    t = render(DEFAULTS["footer"], META)
    assert t.startswith("来源：B站 @某UP 《吃盐》 https://www.bilibili.com/video/BV1wFZ8YBEt4\n")
    assert "trackid" not in t and "版权" in t


def test_missing_author_drops_bracket_part_and_line():
    meta = {**META, "author": ""}
    assert "@" not in render(DEFAULTS["footer"], meta)
    head = render(DEFAULTS["header"], meta)
    assert "作者" not in head and head.startswith("标题：吃盐\n链接：")
    assert render("作者：{author}\n标题：{title}", meta) == "标题：吃盐"


def test_unknown_variable_kept_and_duration_text():
    assert render("{title} {nope} {duration}", META) == "吃盐 {nope} 3 分 17 秒"


@pytest.mark.parametrize("meta,url", [
    ({"platform": "bilibili", "video_id": "BV1", "part": 3}, "https://www.bilibili.com/video/BV1?p=3"),
    ({"platform": "douyin", "video_id": "123"}, "https://www.douyin.com/video/123"),
    ({"platform": "youtube", "video_id": "abc"}, "https://www.youtube.com/watch?v=abc"),
    ({"url": "https://x.example/v"}, "https://x.example/v"),
])
def test_clean_url(meta, url):
    assert clean_url(meta) == url


def test_store_edit_and_reset(tmp_path):
    store = FrameStore(tmp_path / "frame.json")
    assert store.get() == DEFAULTS and store.public()["modified"] == []
    store.update({"footer": "—— {title} {url}"})
    assert store.render_all(META)["footer"] == "—— 吃盐 https://www.bilibili.com/video/BV1wFZ8YBEt4"
    assert store.public()["modified"] == ["footer"]
    store.reset("footer")
    assert store.get()["footer"] == DEFAULTS["footer"]
    with pytest.raises(ValueError):
        store.update({"nope": "x"})
    with pytest.raises(ValueError):
        store.update({"footer": "x" * 2001})
