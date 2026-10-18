"""T3 acceptance 1-2: failure categories, subtitle status, danmaku exclusion, redaction."""
import pytest

from vtx.fetch import (
    FetchError,
    StallWatch,
    check_duration,
    classify_error,
    douyin_detail_status,
    pick_subtitle_langs,
    subtitle_status,
)
from vtx.redact import redact


@pytest.mark.parametrize("message,category", [
    ("Unsupported URL: https://example.com/x", "parse"),
    ("[Douyin] 7665: Fresh cookies (not necessarily logged in) are needed", "risk_control"),
    ("HTTP Error 412: Precondition Failed", "risk_control"),
    ("Sign in to confirm you're not a bot", "risk_control"),
    ("Private video. Sign in if you've been granted access to this video", "login_required"),
    ("Sign in to confirm your age. This video may be inappropriate for some users.", "login_required"),
    ("This video is available to this channel's members on level: Member", "login_required"),
    ("The uploader has not made this video available in your country", "region"),
    ("Video unavailable. This video has been removed by the uploader", "dead"),
    ("B站视频不可用: BV1zW411t7YE（啥都木有）", "dead"),
    ("Connection reset by peer", "api_failed"),
])
def test_classify_error(message, category):
    assert classify_error(message) == category


def test_fetch_error_carries_category_and_redacted_text():
    err = FetchError.from_message("Sign in to confirm you're not a bot; Cookie: SESSDATA=abc123secret; x=1")
    assert err.category == "risk_control"
    assert "abc123secret" not in str(err)


def test_redact_hides_cookie_values():
    text = "headers Cookie: SESSDATA=s3cr3t; bili_jct=t0ken sessionid=zz9 ttwid=1%7Cabc"
    out = redact(text)
    for secret in ["s3cr3t", "t0ken", "zz9", "1%7Cabc"]:
        assert secret not in out
    assert "SESSDATA=***" in out


@pytest.mark.parametrize("player,expected", [
    ({"code": 0, "data": {"need_login_subtitle": True, "login_mid": 0, "subtitle": {"subtitles": []}}}, "login_required"),
    ({"code": 0, "data": {"need_login_subtitle": False, "login_mid": 42, "subtitle": {"subtitles": []}}}, "no_subs"),
    ({"code": -400, "message": "请求错误"}, "api_failed"),
    (None, "api_failed"),
    ({"code": 0, "data": {"login_mid": 42, "subtitle": {"subtitles": [{"lan": "ai-zh", "subtitle_url": "//x"}]}}}, "ok"),
])
def test_bilibili_subtitle_status(player, expected):
    assert subtitle_status(player) == expected


def test_danmaku_and_live_chat_excluded():
    available = {"danmaku": [], "ai-zh": [], "zh-CN": [], "live_chat": []}
    assert pick_subtitle_langs(available) == ["ai-zh", "zh-CN"]


def test_signed_url_keeps_only_host():
    url = "https://v26-web.douyinvod.com/abc/video/tos/cn/x.mp4?a=6383&sign=deadbeef&expire=1"
    out = redact(f"downloading {url} now")
    assert "deadbeef" not in out and "tos/cn" not in out
    assert "v26-web.douyinvod.com" in out


@pytest.mark.parametrize("duration,ok", [
    (None, False), (0, False), (-3, False), (5 * 3600 + 1, False),
    (1, True), (5 * 3600, True), (197.055, True),
])
def test_probe_admission(duration, ok):
    if ok:
        check_duration(duration)
    else:
        with pytest.raises(FetchError) as info:
            check_duration(duration)
        assert info.value.category == "parse"


def test_stall_detected_after_10s_without_progress():
    watch = StallWatch(window=10)
    assert not watch.update(t=0, done=0)
    assert not watch.update(t=5, done=1000)
    assert not watch.update(t=14, done=1000)   # 9s since last progress
    assert watch.update(t=15.5, done=1000)     # 10.5s since last progress


def test_stall_watch_resets_on_progress():
    watch = StallWatch(window=10)
    for t in range(0, 60, 5):
        assert not watch.update(t=t, done=t * 100)


@pytest.mark.parametrize("body,expected", [
    ('{"aweme_detail": {"aweme_id": "1"}}', "ok"),
    ('{"aweme_detail": null, "filter_detail": {"filter_reason": "core_dep"}}', "dead"),
    ("", "risk_control"),
    ("<html>verify</html>", "risk_control"),
])
def test_douyin_detail_status(body, expected):
    assert douyin_detail_status(body) == expected
