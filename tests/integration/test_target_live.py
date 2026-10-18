"""T2 acceptance 2: every real T1 link resolves to the expected ID / part."""
import pytest

from vtx.target import Target, UnsupportedURL, resolve

pytestmark = pytest.mark.integration

EXPECTED = {
    "https://www.bilibili.com/video/BV1wFZ8YBEt4": Target("bilibili", "BV1wFZ8YBEt4", 1, False),
    "https://b23.tv/BV1WVfcYFEiX": Target("bilibili", "BV1WVfcYFEiX", 1, False),
    "https://www.bilibili.com/video/BV1Wv411h7kN?p=3": Target("bilibili", "BV1Wv411h7kN", 3, False),
    "https://www.bilibili.com/video/BV1Wv411h7kN": Target("bilibili", "BV1Wv411h7kN", None, True),
    "https://www.douyin.com/video/7665606762753510710": Target("douyin", "7665606762753510710", None, False),
    "https://www.douyin.com/video/7681626630355258665": Target("douyin", "7681626630355258665", None, False),
    "https://www.douyin.com/video/7689755493836819739": Target("douyin", "7689755493836819739", None, False),
    "https://www.youtube.com/watch?v=K_RSZC0s8a4": Target("youtube", "K_RSZC0s8a4", None, False),
    "https://www.youtube.com/watch?v=mNUUThgKVbI": Target("youtube", "mNUUThgKVbI", None, False),
    "https://www.youtube.com/watch?v=HwmLtNIg-eI": Target("youtube", "HwmLtNIg-eI", None, False),
    "https://www.youtube.com/watch?v=catABDV68wI": Target("youtube", "catABDV68wI", None, False),
    "https://youtu.be/Ww9kaZfmHzY": Target("youtube", "Ww9kaZfmHzY", None, False),
    "https://www.youtube.com/watch?v=2A951VZuenw": Target("youtube", "2A951VZuenw", None, False),
}


@pytest.mark.parametrize("url,expected", EXPECTED.items())
def test_real_links(url, expected):
    assert resolve(url) == expected


def test_dead_bili_link_is_reported():
    with pytest.raises(UnsupportedURL, match="不可用"):
        resolve("https://www.bilibili.com/video/BV1zW411t7YE")
