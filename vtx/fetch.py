"""Fetch audio + original-language subtitles for a Target.

Pure helpers (classification, admission, stall detection) are kept separate
from the network code so they can be tested offline.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from vtx.redact import redact

MAX_DURATION_S = 5 * 3600
CATEGORIES = ("parse", "risk_control", "login_required", "region", "dead", "no_subs", "api_failed")

# order matters: first match wins ("Sign in to confirm" is a bot check, not a login wall)
_RULES = [
    ("risk_control", r"fresh cookies|http error 412|precondition failed|confirm you.?re not a bot|captcha|验证"),
    ("login_required", r"private video|sign in|members|login required|需要登录|会员"),
    ("region", r"not (?:made this video )?available in your country|geo.?restrict|地区"),
    ("dead", r"video unavailable|has been removed|不可用|已失效|已删除|-404"),
    ("parse", r"unsupported url|不支持的链接|不是.*链接|没有找到链接|短链无法展开|分 P 超出范围"),
]


class FetchError(Exception):
    def __init__(self, category: str, message: str):
        assert category in CATEGORIES, category
        self.category = category
        super().__init__(f"[{category}] {redact(message)}")

    @classmethod
    def from_message(cls, message: str) -> "FetchError":
        return cls(classify_error(message), message)


def classify_error(message: str) -> str:
    low = message.lower()
    for category, pattern in _RULES:
        if re.search(pattern, low):
            return category
    return "api_failed"


def check_duration(duration: Optional[float]) -> float:
    if duration is None:
        raise FetchError("parse", "无法获取视频时长")
    if duration <= 0:
        raise FetchError("parse", f"视频时长异常: {duration}")
    if duration > MAX_DURATION_S:
        raise FetchError("parse", f"视频超过 5 小时上限: {duration / 3600:.1f} 小时")
    return float(duration)


def subtitle_status(player: Optional[dict]) -> str:
    """Interpret a B站 x/player/wbi/v2 response."""
    if not player or player.get("code") != 0:
        return "api_failed"
    data = player.get("data") or {}
    subs = (data.get("subtitle") or {}).get("subtitles") or []
    if subs:
        return "ok"
    if data.get("need_login_subtitle") and not data.get("login_mid"):
        return "login_required"
    return "no_subs"


def pick_subtitle_langs(available: dict) -> list:
    """Real subtitle tracks only: drop danmaku / live chat."""
    return [lang for lang in available if lang not in ("danmaku", "live_chat")]


def douyin_detail_status(body: str) -> str:
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return "risk_control"  # empty body or HTML verify page
    if data.get("aweme_detail"):
        return "ok"
    if (data.get("filter_detail") or {}).get("filter_reason"):
        return "dead"
    return "risk_control"


class StallWatch:
    """True once `window` seconds pass without the byte counter moving."""

    def __init__(self, window: float = 10):
        self.window = window
        self._last_done = None
        self._last_t = None

    def update(self, t: float, done: int) -> bool:
        if self._last_done is None or done > self._last_done:
            self._last_done, self._last_t = done, t
            return False
        return t - self._last_t >= self.window


@dataclass
class FetchResult:
    audio: Path                      # 16 kHz mono wav
    duration: float                  # seconds, from the platform
    title: str
    subtitles: dict = field(default_factory=dict)  # lang -> srt path
    cookie_mode: str = "none"        # none | cookies.txt | browser
    notes: list = field(default_factory=list)
    author: str = ""                 # uploader / creator name, "" when the platform does not say


# ---------------------------------------------------------------- network side

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36")
DOWNLOAD_BUDGET_S = 600   # per attempt; long audio on a slow link still fits
STALL_WINDOW_S = 10


def fetch(target, settings) -> FetchResult:
    from vtx import douyin

    if target.needs_choice:
        raise FetchError("parse", "链接包含多个视频/分 P，请先选择要处理的一个")
    out_dir = settings.work_dir / f"{target.platform}-{target.video_id}-p{target.part or 0}"
    out_dir.mkdir(parents=True, exist_ok=True)

    if target.platform == "bilibili":
        return _fetch_bilibili(target, settings, out_dir)
    if target.platform == "youtube":
        return _fetch_youtube(target, settings, out_dir)
    if target.platform == "douyin":
        detail = douyin.fetch_detail(settings, target.video_id)
        duration = check_duration(detail["duration"])
        media = _download_direct(detail["play_urls"], out_dir / "media.mp4",
                                 referer="https://www.douyin.com/")
        wav = to_wav(media, out_dir / "audio.wav", settings.ffmpeg)
        return FetchResult(wav, duration, detail["title"], cookie_mode="browser", author=detail.get("author", ""))
    raise FetchError("parse", f"不支持的平台: {target.platform}")


# --- bilibili

def _bili_get(path, params, cookies):
    import httpx

    resp = httpx.get(f"https://api.bilibili.com{path}", params=params, cookies=cookies,
                     headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}, timeout=15)
    try:
        return resp.json()
    except ValueError:
        raise FetchError.from_message(f"B站接口返回非 JSON: HTTP {resp.status_code}")


def _load_cookies(path):
    if not path:
        return {}
    jar = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if not line.startswith("#") and len(fields) >= 7:
            jar[fields[5]] = fields[6]
    return jar


def _fetch_bilibili(target, settings, out_dir) -> FetchResult:
    cookie_file = settings.cookie_file("bilibili")
    cookies = _load_cookies(cookie_file)
    view = _bili_get("/x/web-interface/view", {"bvid": target.video_id}, cookies)
    if view.get("code") != 0:
        raise FetchError("dead", f"B站视频不可用: {target.video_id}（{view.get('message')}）")
    page = view["data"]["pages"][target.part - 1]
    duration = check_duration(page["duration"])
    title = view["data"]["title"] if len(view["data"]["pages"]) == 1 else f"{view['data']['title']} P{target.part} {page['part']}"

    notes = []
    player = _bili_get("/x/player/wbi/v2", {"bvid": target.video_id, "cid": page["cid"]}, cookies)
    status = subtitle_status(player)
    subtitles = {}
    if status == "ok":
        for sub in player["data"]["subtitle"]["subtitles"]:
            if sub.get("subtitle_url") and sub.get("lan") not in ("danmaku",):
                subtitles[sub["lan"]] = _bili_subtitle_to_srt(sub["subtitle_url"], out_dir / f"sub.{sub['lan']}.srt")
    else:
        notes.append(f"字幕: {status}")

    url = f"https://www.bilibili.com/video/{target.video_id}?p={target.part}"
    media = _ytdlp_audio(url, out_dir, cookie_file=cookie_file, proxy="")
    wav = to_wav(media, out_dir / "audio.wav", settings.ffmpeg)
    author = (view["data"].get("owner") or {}).get("name") or ""
    return FetchResult(wav, duration, title, subtitles, "cookies.txt" if cookie_file else "none", notes, author)


def _bili_subtitle_to_srt(url, path: Path) -> Path:
    import httpx

    body = httpx.get("https:" + url if url.startswith("//") else url,
                     headers={"User-Agent": UA}, timeout=15).json().get("body") or []
    lines = []
    for i, cue in enumerate(body, 1):
        lines += [str(i), f"{_srt_time(cue['from'])} --> {_srt_time(cue['to'])}", cue["content"], ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


# --- youtube

def _fetch_youtube(target, settings, out_dir) -> FetchResult:
    url = f"https://www.youtube.com/watch?v={target.video_id}"
    info = _ytdlp_info(url, cookie_file=settings.cookie_file("youtube"), proxy=settings.proxy_youtube)
    duration = check_duration(info.get("duration"))
    subtitles, notes = {}, []
    manual = pick_subtitle_langs(info.get("subtitles") or {})
    if manual:
        subtitles = _ytdlp_subtitles(url, manual, out_dir, settings)
    else:
        notes.append("字幕: no_subs（只有自动字幕时不下载，见设计 4.4）")
    media = _ytdlp_audio(url, out_dir, cookie_file=settings.cookie_file("youtube"),
                         proxy=settings.proxy_youtube)
    wav = to_wav(media, out_dir / "audio.wav", settings.ffmpeg)
    mode = "cookies.txt" if settings.cookie_file("youtube") else "none"
    author = info.get("uploader") or info.get("channel") or ""
    return FetchResult(wav, duration, info.get("title") or "", subtitles, mode, notes, author)


# --- yt-dlp wrappers (in-process; yt-dlp's own retries off, we retry once)

class _Stalled(Exception):
    pass


def _ytdlp_opts(cookie_file, proxy, **extra):
    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "retries": 0, "fragment_retries": 0, "extractor_retries": 0,
        "socket_timeout": 10, "js_runtimes": {"node": {}},
    }
    if cookie_file:
        opts["cookiefile"] = str(cookie_file)
    if proxy:
        opts["proxy"] = proxy
    opts.update(extra)
    return opts


def _ytdlp_info(url, cookie_file, proxy):
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(_ytdlp_opts(cookie_file, proxy, skip_download=True)) as ydl:
            return ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise FetchError.from_message(str(exc)) from None


def _ytdlp_subtitles(url, langs, out_dir, settings):
    import yt_dlp

    opts = _ytdlp_opts(settings.cookie_file("youtube"), settings.proxy_youtube, skip_download=True,
                       writesubtitles=True, subtitleslangs=langs, subtitlesformat="srt/vtt/best",
                       outtmpl=str(out_dir / "sub"))
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    return {p.suffixes[-2].lstrip("."): p for p in out_dir.glob("sub.*.*") if len(p.suffixes) >= 2}


def _ytdlp_audio(url, out_dir, cookie_file, proxy) -> Path:
    """Download the best audio-only stream; abort a stalled attempt and re-resolve once."""
    import time

    import yt_dlp

    last_error = None
    for attempt in (1, 2):
        watch, started = StallWatch(STALL_WINDOW_S), time.monotonic()

        def hook(d):
            now = time.monotonic() - started
            if d.get("status") == "downloading":
                if watch.update(now, d.get("downloaded_bytes") or 0) or now > DOWNLOAD_BUDGET_S:
                    raise _Stalled()

        opts = _ytdlp_opts(cookie_file, proxy, format="bestaudio/best", continuedl=True,
                           outtmpl=str(out_dir / "media.%(ext)s"), progress_hooks=[hook])
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                return Path(ydl.prepare_filename(info))
        except _Stalled:
            last_error = FetchError("api_failed", f"下载停滞或超时（第 {attempt} 次）")
        except yt_dlp.utils.DownloadError as exc:
            if "_Stalled" in repr(exc.exc_info):
                last_error = FetchError("api_failed", f"下载停滞或超时（第 {attempt} 次）")
            else:
                raise FetchError.from_message(str(exc)) from None
    raise last_error


def _download_direct(urls, path: Path, referer: str) -> Path:
    """Try each CDN URL in turn; same stall rule as yt-dlp downloads."""
    import time

    import httpx

    errors = []
    for url in urls:
        watch, started, done = StallWatch(STALL_WINDOW_S), time.monotonic(), 0
        try:
            with httpx.stream("GET", url, headers={"User-Agent": UA, "Referer": referer},
                              follow_redirects=True, timeout=10) as resp:
                if resp.status_code >= 400:
                    errors.append(f"HTTP {resp.status_code}")
                    continue
                with path.open("wb") as fh:
                    for chunk in resp.iter_bytes(1 << 16):
                        fh.write(chunk)
                        done += len(chunk)
                        now = time.monotonic() - started
                        if watch.update(now, done) or now > DOWNLOAD_BUDGET_S:
                            raise _Stalled()
            return path
        except (_Stalled, httpx.HTTPError) as exc:
            errors.append(type(exc).__name__)
    raise FetchError("api_failed", redact(f"所有播放地址都失败: {errors}"))


# --- audio conversion

def to_wav(media: Path, wav: Path, ffmpeg: str = "ffmpeg", timeout: float = 1800) -> Path:
    """16 kHz mono PCM; kill the whole process tree on timeout (cross-platform)."""
    import subprocess

    import psutil

    proc = subprocess.Popen(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        _, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid, psutil)
        raise FetchError("api_failed", "音频转换超时")
    if proc.returncode != 0:
        raise FetchError("api_failed", f"音频转换失败: {err.decode('utf-8', 'replace')[-300:]}")
    return wav


def kill_tree(pid: int, psutil) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=5)
