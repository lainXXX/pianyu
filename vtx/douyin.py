"""抖音: read the detail API from inside a real browser page, then download directly.

Pure-HTTP routes (F2, yt-dlp, iesdouyin share page) fail as of 2026-10; see the design doc.
"""
import json

from vtx import cdp
from vtx.fetch import FetchError, douyin_detail_status

# absolute URL: a relative one fails while the tab is mid-navigation / on about:blank
DETAIL_JS = """
fetch('https://www.douyin.com/aweme/v1/web/aweme/detail/?device_platform=webapp&aid=6383&aweme_id=%s',
      {credentials: 'include'}).then(r => r.text())
"""
HOME = "https://www.douyin.com/"


def fetch_detail(settings, aweme_id: str) -> dict:
    """Return {'duration': s, 'title': str, 'author': str, 'play_urls': [...]} for one aweme."""
    if not aweme_id.isdigit():
        raise FetchError("parse", f"抖音作品 ID 非法: {aweme_id}")
    cdp.ensure_browser(settings.browser, settings.cdp_port, settings.work_dir / "browser-profile",
                       "https://www.douyin.com/", settings.browser_headless)
    body, status, error = "", "api_failed", None
    for attempt in (1, 2):  # second try after reloading the home page (fresh tokens, settled tab)
        try:
            ws = cdp.page_for(settings.cdp_port, "douyin.com", HOME)
            if attempt == 2:
                cdp.reload(ws, HOME)
            body = cdp.evaluate(ws, DETAIL_JS % aweme_id)
            status, error = douyin_detail_status(body), None
        except cdp.CDPError as exc:
            status, error = "api_failed", exc
        if status in ("ok", "dead"):
            break
    if error is not None:
        raise FetchError("api_failed", f"抖音浏览器通道失败: {error}")
    if status == "dead":
        raise FetchError("dead", f"抖音作品不可用: {aweme_id}")
    if status != "ok":
        raise FetchError("risk_control", "抖音返回空响应或验证页，请在专用浏览器窗口完成验证后重试")

    aweme = json.loads(body)["aweme_detail"]
    video = aweme.get("video") or {}
    urls = (video.get("play_addr") or {}).get("url_list") or []
    if not urls:
        raise FetchError("api_failed", "抖音作品没有可用的播放地址（可能是图文作品）")
    return {
        "duration": (aweme.get("duration") or video.get("duration") or 0) / 1000,
        "title": " ".join((aweme.get("desc") or "").split())[:200],  # desc may contain newlines
        "author": ((aweme.get("author") or {}).get("nickname") or "").strip(),
        "play_urls": urls,
    }
