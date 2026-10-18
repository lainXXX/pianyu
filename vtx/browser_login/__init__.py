"""Explicit website login in an isolated, visible browser profile.

The browser and cookie values stay in the worker thread. Public session objects
contain only progress and short, fixed messages.
"""
from contextlib import contextmanager
import logging
import math
from pathlib import Path
import threading
import time
import uuid
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request

logger = logging.getLogger(__name__)

PLATFORMS = {
    "bilibili": {
        "url": "https://passport.bilibili.com/login",
        "domains": ("bilibili.com",),
        "markers": ("SESSDATA",),
    },
    "douyin": {
        "url": "https://www.douyin.com/",
        "domains": ("douyin.com",),
        "markers": ("sessionid", "sessionid_ss"),
    },
    "youtube": {
        "url": "https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2F",
        "domains": ("youtube.com", "google.com"),
        "markers": ("SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID"),
    },
}
ACTIVE = {"opening", "waiting"}


def _belongs(domain, roots):
    domain = domain.lstrip(".").lower()
    return any(domain == root or domain.endswith("." + root) for root in roots)


def _platform_cookies(platform, cookies):
    now = time.time()
    return [cookie for cookie in cookies
            if _belongs(cookie.get("domain", ""), PLATFORMS[platform]["domains"])
            and cookie.get("value")
            and (cookie.get("expires", -1) <= 0 or cookie["expires"] > now)]


def _logged_in(platform, cookies):
    # A Google session alone does not establish that YouTube accepted the login.
    roots = ("youtube.com",) if platform == "youtube" else PLATFORMS[platform]["domains"]
    return any(cookie["name"] in PLATFORMS[platform]["markers"]
               and _belongs(cookie["domain"], roots) for cookie in cookies)


def netscape_cookies(cookies):
    """Serialize browser cookies without losing session or HttpOnly semantics."""
    lines = ["# Netscape HTTP Cookie File", "# Created by 片语 website login"]
    for cookie in cookies:
        domain = cookie["domain"]
        expiry = cookie.get("expires", -1)
        expiry = int(expiry) if math.isfinite(expiry) and expiry > 0 else 0
        fields = [domain, "TRUE" if domain.startswith(".") else "FALSE",
                  cookie.get("path", "/"), "TRUE" if cookie.get("secure") else "FALSE",
                  str(expiry), cookie["name"], cookie["value"]]
        if any("\t" in value or "\r" in value or "\n" in value for value in fields):
            raise ValueError("Invalid browser cookie")
        line = "\t".join(fields)
        lines.append("#HttpOnly_" + line if cookie.get("httpOnly") else line)
    return ("\n".join(lines) + "\n").encode("utf-8")


@contextmanager
def _browser(profile_dir, browser=None):
    from playwright.sync_api import sync_playwright

    profile_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        options = {"user_data_dir": str(profile_dir), "headless": False,
                   "timeout": 20000, "args": ["--no-first-run", "--no-default-browser-check"]}
        if browser:
            context = playwright.chromium.launch_persistent_context(executable_path=str(browser), **options)
        else:
            try:
                context = playwright.chromium.launch_persistent_context(channel="msedge", **options)
            except Exception:
                context = playwright.chromium.launch_persistent_context(**options)
        try:
            yield context
        finally:
            context.close()


class BrowserLoginManager:
    def __init__(self, store, profile_dir: Path, browser=None, *, browser_factory=None,
                 timeout=300, poll_interval=0.75, events=None):
        self.store = store
        self.profile_dir = Path(profile_dir)
        self.browser = browser
        self.browser_factory = browser_factory or _browser
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.events = events
        self._lock = threading.RLock()
        self._sessions = {}
        self._stopped = False

    def start(self, platform):
        if platform not in PLATFORMS:
            raise ValueError("未知平台")
        with self._lock:
            if self._stopped:
                raise RuntimeError("登录服务已关闭")
            for session in self._sessions.values():
                if session["public"]["platform"] == platform and session["public"]["status"] in ACTIVE:
                    return dict(session["public"])
            # Keep polling handles bounded without evicting an active session.
            for sid in list(self._sessions):
                if len(self._sessions) < 32:
                    break
                if self._sessions[sid]["public"]["status"] not in ACTIVE:
                    del self._sessions[sid]
            sid = uuid.uuid4().hex
            public = {"id": sid, "platform": platform, "status": "opening"}
            worker = threading.Thread(target=self._run, args=(sid,), name=f"website-login-{platform}", daemon=True)
            self._sessions[sid] = {"public": public, "cancel": threading.Event(), "worker": worker}
            self._update(sid, "opening")
            worker.start()
            return dict(public)

    def status(self, sid):
        with self._lock:
            return dict(self._sessions[sid]["public"])

    def cancel(self, sid):
        with self._lock:
            session = self._sessions[sid]
            if session["public"]["status"] in ACTIVE:
                session["cancel"].set()
                self._update(sid, "cancelled")
            return dict(session["public"])

    def close(self):
        with self._lock:
            self._stopped = True
            workers = [session["worker"] for session in self._sessions.values()]
            for sid in self._sessions:
                self.cancel(sid)
        deadline = time.monotonic() + 3
        for worker in workers:
            worker.join(max(0, deadline - time.monotonic()))

    def _update(self, sid, status, error=None):
        with self._lock:
            public = self._sessions[sid]["public"]
            if public["status"] not in ACTIVE:
                return
            public["status"] = status
            if error:
                public["error"] = error
            logger.info("website_login platform=%s session=%s status=%s", public["platform"], sid, status)
            if self.events:
                self.events.event("cookie.login." + status, phase=public["platform"], status=status)

    def _run(self, sid):
        session = self._sessions[sid]
        platform = session["public"]["platform"]
        cancel = session["cancel"]
        closed = threading.Event()
        phase = "opening"
        try:
            with self.browser_factory(self.profile_dir / platform, self.browser) as context:
                context.on("close", lambda *_: closed.set())
                if cancel.is_set():
                    return
                page = context.pages[0] if context.pages else context.new_page()
                # A slow login page may still become usable after a navigation timeout.
                from playwright.sync_api import TimeoutError as BrowserTimeout

                try:
                    page.goto(PLATFORMS[platform]["url"], wait_until="domcontentloaded", timeout=30000)
                except BrowserTimeout:
                    pass
                self._update(sid, "waiting")
                phase = "waiting"
                deadline = time.monotonic() + self.timeout
                while not cancel.is_set() and time.monotonic() < deadline:
                    if not context.pages:
                        self._update(sid, "failed", "登录窗口已关闭")
                        return
                    cookies = _platform_cookies(platform, context.cookies())
                    if _logged_in(platform, cookies):
                        with self._lock:
                            if cancel.is_set():
                                return
                            phase = "saving"
                            self.store.set_cookies(platform, netscape_cookies(cookies))
                            self._update(sid, "connected")
                        return
                    # Pump browser events so closing the window is observed promptly.
                    context.pages[0].wait_for_timeout(self.poll_interval * 1000)
                if not cancel.is_set():
                    self._update(sid, "failed", "登录超时，请重试")
        except Exception:
            # Browser exceptions can contain page URLs and cookies; never expose them.
            message = {"opening": "浏览器启动失败，请安装 Edge 后重试",
                       "waiting": "登录未完成，请重试",
                       "saving": "登录信息保存失败，请重试"}[phase]
            if closed.is_set() and phase != "saving":
                message = "登录窗口已关闭"
            self._update(sid, "failed", message)


def register_browser_login(app, store, profile_dir, auth, *, enabled=False, manager=None):
    """Register local-only login endpoints; return the manager for app lifecycle."""
    manager = manager or BrowserLoginManager(store, profile_dir, events=getattr(app.state, "events", None))

    def available(request: Request):
        if not enabled:
            raise HTTPException(403, "请在本机设置中登录")
        origin = request.headers.get("origin")
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                raise HTTPException(403, "请求来源不匹配")
            if parsed.scheme != request.url.scheme or parsed.netloc != request.url.netloc:
                raise HTTPException(403, "请求来源不匹配")

    @app.post("/api/settings/cookies/{platform}/login", dependencies=[Depends(auth), Depends(available)])
    def start(platform: str):
        if platform not in PLATFORMS:
            raise HTTPException(404, "未知平台")
        try:
            return manager.start(platform)
        except RuntimeError:
            raise HTTPException(503, "登录服务已关闭")

    @app.get("/api/settings/cookie-logins/{sid}", dependencies=[Depends(auth), Depends(available)])
    def status(sid: str):
        try:
            return manager.status(sid)
        except KeyError:
            raise HTTPException(404, "登录记录不存在")

    @app.delete("/api/settings/cookie-logins/{sid}", dependencies=[Depends(auth), Depends(available)])
    def cancel(sid: str):
        try:
            return manager.cancel(sid)
        except KeyError:
            raise HTTPException(404, "登录记录不存在")

    app.add_event_handler("shutdown", manager.close)
    return manager
