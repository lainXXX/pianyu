"""URL -> Target: which platform, which video, which part.

Network lookups (short-link expansion, B站 part count) are injected so the
parsing rules stay testable offline.
"""
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse


class UnsupportedURL(ValueError):
    pass


@dataclass(frozen=True)
class Target:
    platform: str
    video_id: Optional[str]
    part: Optional[int]  # B站分 P (1-based); None for other platforms or when unchosen
    needs_choice: bool   # user must pick a part / a video before processing


SHORT_HOSTS = {"b23.tv", "v.douyin.com"}
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
YT_ID_RE = re.compile(r"^[0-9A-Za-z_-]{11}$")
DY_ID_RE = re.compile(r"/(?:video|note|share/video)/(\d{15,20})")
URL_IN_TEXT_RE = re.compile(r"https?://[^\s，。]+")


def resolve_target(
    text: str,
    expand: Callable[[str], Optional[str]],
    bili_page_count: Callable[[str], int],
) -> Target:
    url = _extract_url(text)
    host = (urlparse(url).hostname or "").lower()
    if host in SHORT_HOSTS:
        expanded = expand(url)
        if not expanded:
            raise UnsupportedURL(f"短链无法展开: {url}")
        url, host = expanded, (urlparse(expanded).hostname or "").lower()

    if host.endswith("bilibili.com"):
        return _bilibili(url, bili_page_count)
    if host.endswith("youtube.com") or host == "youtu.be":
        return _youtube(url, host)
    if host.endswith("douyin.com") or host.endswith("iesdouyin.com"):
        return _douyin(url)
    raise UnsupportedURL(f"不支持的链接: {url}")


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")


def http_expand(url: str) -> Optional[str]:
    """Follow redirects of a short link and return the final URL."""
    import httpx

    with httpx.Client(follow_redirects=True, timeout=15, headers={"User-Agent": UA}) as client:
        resp = client.get(url)
    return str(resp.url) if resp.is_success or resp.is_redirect else None


def http_bili_page_count(bvid: str) -> int:
    import httpx

    resp = httpx.get("https://api.bilibili.com/x/web-interface/view",
                     params={"bvid": bvid}, headers={"User-Agent": UA}, timeout=15)
    data = resp.json()
    if data.get("code") != 0:
        raise UnsupportedURL(f"B站视频不可用: {bvid}（{data.get('message')}）")
    return len(data["data"]["pages"])


def http_bili_parts(bvid: str) -> list:
    """[{page, title, duration}] for the part picker."""
    import httpx

    data = httpx.get("https://api.bilibili.com/x/web-interface/view", params={"bvid": bvid},
                     headers={"User-Agent": UA}, timeout=15).json()
    if data.get("code") != 0:
        raise UnsupportedURL(f"B站视频不可用: {bvid}（{data.get('message')}）")
    return [{"page": p["page"], "title": p["part"], "duration": p["duration"]} for p in data["data"]["pages"]]


def resolve(text: str) -> Target:
    return resolve_target(text, expand=http_expand, bili_page_count=http_bili_page_count)


def _extract_url(text: str) -> str:
    match = URL_IN_TEXT_RE.search(text)
    if not match:
        raise UnsupportedURL(f"没有找到链接: {text[:50]}")
    return match.group(0)


def _bilibili(url: str, page_count: Callable[[str], int]) -> Target:
    parsed = urlparse(url)
    match = BV_RE.search(parsed.path)
    if not match or "/video/" not in parsed.path:
        raise UnsupportedURL(f"不是 B站视频链接: {url}")
    bvid = match.group(0)
    pages = page_count(bvid)
    p = parse_qs(parsed.query).get("p")
    if p:
        part = int(p[0])
        if not 1 <= part <= pages:
            raise UnsupportedURL(f"分 P 超出范围: p={part}，共 {pages} P")
        return Target("bilibili", bvid, part, False)
    if pages > 1:
        return Target("bilibili", bvid, None, True)
    return Target("bilibili", bvid, 1, False)


def _youtube(url: str, host: str) -> Target:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if host == "youtu.be":
        vid = parsed.path.strip("/").split("/")[0]
    elif parsed.path.startswith("/shorts/"):
        vid = parsed.path.split("/")[2]
    else:
        vid = (query.get("v") or [""])[0]
    if YT_ID_RE.match(vid or ""):
        return Target("youtube", vid, None, False)
    if parsed.path == "/playlist" and query.get("list"):
        return Target("youtube", None, None, True)
    raise UnsupportedURL(f"不是 YouTube 视频链接: {url}")


def _douyin(url: str) -> Target:
    parsed = urlparse(url)
    match = DY_ID_RE.search(parsed.path)
    if match:
        return Target("douyin", match.group(1), None, False)
    modal = (parse_qs(parsed.query).get("modal_id") or [""])[0]
    if modal.isdigit():
        return Target("douyin", modal, None, False)
    raise UnsupportedURL(f"不是抖音作品链接: {url}")
