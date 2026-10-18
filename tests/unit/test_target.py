"""T2 acceptance 1: URL -> Target, network calls replaced by fakes."""
import pytest

from vtx.target import Target, UnsupportedURL, resolve_target

REDIRECTS = {
    "https://b23.tv/abc123": "https://www.bilibili.com/video/BV1WVfcYFEiX?share_source=copy",
    "https://v.douyin.com/iRNBho6u/": "https://www.iesdouyin.com/share/video/7332788445338668326/?region=CN",
}
PAGES = {"BV1Wv411h7kN": 155, "BV1WVfcYFEiX": 1, "BV1wFZ8YBEt4": 1}


def resolve(url):
    return resolve_target(url, expand=REDIRECTS.get, bili_page_count=PAGES.__getitem__)


# --- bilibili ---

def test_bili_long_link_single_part():
    assert resolve("https://www.bilibili.com/video/BV1wFZ8YBEt4") == Target("bilibili", "BV1wFZ8YBEt4", 1, False)


def test_bili_short_link_is_expanded():
    assert resolve("https://b23.tv/abc123") == Target("bilibili", "BV1WVfcYFEiX", 1, False)


def test_bili_p_param_selects_part():
    assert resolve("https://www.bilibili.com/video/BV1Wv411h7kN?p=3") == Target("bilibili", "BV1Wv411h7kN", 3, False)


def test_bili_multi_part_without_p_needs_choice():
    assert resolve("https://www.bilibili.com/video/BV1Wv411h7kN") == Target("bilibili", "BV1Wv411h7kN", None, True)


def test_bili_p_out_of_range_rejected():
    with pytest.raises(UnsupportedURL, match="分 P"):
        resolve("https://www.bilibili.com/video/BV1wFZ8YBEt4?p=2")


# --- youtube ---

@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=K_RSZC0s8a4",
    "https://youtu.be/K_RSZC0s8a4",
    "https://www.youtube.com/shorts/K_RSZC0s8a4",
    "https://m.youtube.com/watch?v=K_RSZC0s8a4&t=30s",
])
def test_youtube_forms(url):
    assert resolve(url) == Target("youtube", "K_RSZC0s8a4", None, False)


def test_youtube_list_param_keeps_only_the_video():
    url = "https://www.youtube.com/watch?v=K_RSZC0s8a4&list=PL1234567890&index=4"
    assert resolve(url) == Target("youtube", "K_RSZC0s8a4", None, False)


def test_youtube_pure_playlist_needs_choice():
    assert resolve("https://www.youtube.com/playlist?list=PL1234567890") == Target("youtube", None, None, True)


# --- douyin ---

def test_douyin_long_link():
    assert resolve("https://www.douyin.com/video/7665606762753510710") == Target("douyin", "7665606762753510710", None, False)


def test_douyin_share_short_link():
    assert resolve("https://v.douyin.com/iRNBho6u/") == Target("douyin", "7332788445338668326", None, False)


def test_douyin_modal_id_link():
    url = "https://www.douyin.com/jingxuan?modal_id=7681626630355258665"
    assert resolve(url) == Target("douyin", "7681626630355258665", None, False)


def test_share_text_with_url_inside():
    text = "7.43 复制打开抖音，看看【某某的作品】早睡 https://v.douyin.com/iRNBho6u/ ZzZ:/ 01/02"
    assert resolve(text) == Target("douyin", "7332788445338668326", None, False)


# --- errors ---

@pytest.mark.parametrize("url", [
    "https://example.com/video/1",
    "not a url",
    "https://www.bilibili.com/read/cv123",
])
def test_unsupported(url):
    with pytest.raises(UnsupportedURL):
        resolve(url)
