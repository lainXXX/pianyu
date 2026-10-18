"""Website login only reads cookies from an explicit isolated browser session."""
from contextlib import contextmanager
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from vtx.browser_login import BrowserLoginManager, netscape_cookies, register_browser_login


def cookie(name="SESSDATA", domain=".bilibili.com", value="fake-session", **extra):
    return {"name": name, "domain": domain, "value": value, "path": "/", "expires": -1,
            "secure": True, "httpOnly": True, **extra}


class Store:
    def __init__(self):
        self.saved = []

    def set_cookies(self, platform, content):
        self.saved.append((platform, content))
        return 1


class Page:
    def __init__(self):
        self.urls = []

    def goto(self, url, **options):
        self.urls.append(url)

    def wait_for_timeout(self, ms):
        time.sleep(min(ms / 1000, 0.005))


class Browser:
    def __init__(self, batches=None, error=None, closed=False):
        self.pages = [] if closed else [Page()]
        self.batches = list(batches or [[]])
        self.error = error
        self.opened = []
        self.finished = threading.Event()
        self.listeners = {}

    def on(self, name, callback):
        self.listeners[name] = callback

    def new_page(self):
        page = Page()
        self.pages.append(page)
        return page

    def cookies(self):
        if self.error:
            raise self.error
        result = self.batches[0]
        if len(self.batches) > 1:
            self.batches.pop(0)
        return result

    @contextmanager
    def factory(self, profile, browser):
        self.opened.append((profile, browser))
        try:
            yield self
        finally:
            self.finished.set()


def wait(manager, sid, terminal=True):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = manager.status(sid)
        if (status["status"] not in {"opening", "waiting"}) == terminal:
            return status
        time.sleep(0.005)
    pytest.fail("login session did not reach expected state")


@pytest.mark.parametrize("platform,marker,domain", [
    ("bilibili", "SESSDATA", ".bilibili.com"),
    ("douyin", "sessionid", ".douyin.com"),
    ("douyin", "sessionid_ss", ".douyin.com"),
    ("youtube", "SAPISID", ".youtube.com"),
    ("youtube", "__Secure-3PAPISID", ".youtube.com"),
])
def test_success_exports_only_platform_domains(tmp_path, platform, marker, domain):
    browser = Browser([[], [cookie(marker, domain), cookie("analytics", ".other.com"),
                            cookie(marker, domain + ".attacker.com"),
                            cookie("old", domain, expires=time.time() - 100)]])
    store = Store()
    manager = BrowserLoginManager(store, tmp_path, browser_factory=browser.factory, poll_interval=.001)
    try:
        result = manager.start(platform)
        assert wait(manager, result["id"])["status"] == "connected"
        assert browser.finished.wait(1)
        assert browser.opened == [(tmp_path / platform, None)]
        assert len(store.saved) == 1
        exported = store.saved[0][1].decode()
        assert marker in exported and "#HttpOnly_" + domain in exported
        assert "attacker" not in exported and "other.com" not in exported and "\told\t" not in exported
        assert "value" not in manager.status(result["id"])
        assert "fake-session" not in str(manager.status(result["id"]))
    finally:
        manager.close()


def test_google_session_does_not_establish_youtube_login(tmp_path):
    store = Store()
    browser = Browser([[cookie("SAPISID", ".google.com")]])
    manager = BrowserLoginManager(store, tmp_path, browser_factory=browser.factory, timeout=.02, poll_interval=.001)
    result = manager.start("youtube")
    assert wait(manager, result["id"]) == {**result, "status": "failed", "error": "登录超时，请重试"}
    assert store.saved == []
    manager.close()


@pytest.mark.parametrize("invalid", [cookie(domain=".bilibili.com.attacker.com"),
                                     cookie(expires=1), cookie(value=""),
                                     cookie(name="anonymous")])
def test_invalid_markers_do_not_finish_login(tmp_path, invalid):
    store = Store()
    browser = Browser([[invalid]])
    manager = BrowserLoginManager(store, tmp_path, browser_factory=browser.factory, timeout=.01, poll_interval=.001)
    assert wait(manager, manager.start("bilibili")["id"])["error"] == "登录超时，请重试"
    assert store.saved == []
    manager.close()


def test_cancel_is_terminal_and_closes_owned_browser(tmp_path):
    store = Store()
    browser = Browser()
    manager = BrowserLoginManager(store, tmp_path, browser_factory=browser.factory, poll_interval=.001)
    result = manager.start("bilibili")
    assert manager.start("bilibili")["id"] == result["id"]
    assert manager.cancel(result["id"])["status"] == "cancelled"
    manager.close()
    assert browser.finished.wait(1)
    assert store.saved == []
    with pytest.raises(RuntimeError):
        manager.start("bilibili")


def test_browser_failure_is_safe_and_retryable(tmp_path, caplog):
    @contextmanager
    def broken(profile, browser):
        raise RuntimeError("https://account.example/?token=secret-cookie")
        yield

    manager = BrowserLoginManager(Store(), tmp_path, browser_factory=broken)
    result = manager.start("bilibili")
    failed = wait(manager, result["id"])
    assert failed["status"] == "failed"
    assert "浏览器启动失败" in failed["error"]
    assert "secret-cookie" not in str(failed) + caplog.text
    assert manager.start("bilibili")["id"] != result["id"]
    manager.close()


def test_browser_read_failure_has_safe_error(tmp_path):
    store = Store()
    browser = Browser(error=RuntimeError("cookie=do-not-log"))
    manager = BrowserLoginManager(store, tmp_path, browser_factory=browser.factory)
    assert wait(manager, manager.start("bilibili")["id"])["error"] == "登录未完成，请重试"
    manager.close()


def test_save_failure_has_safe_error(tmp_path):
    store = Store()

    def fail_save(platform, content):
        raise OSError("raw-cookie-private")

    store.set_cookies = fail_save
    browser = Browser([[cookie()]])
    manager = BrowserLoginManager(store, tmp_path, browser_factory=browser.factory)
    assert wait(manager, manager.start("bilibili")["id"])["error"] == "登录信息保存失败，请重试"
    manager.close()


def test_window_closed_is_reported_without_logging_browser_exception(tmp_path):
    browser = Browser()

    def closed():
        browser.listeners["close"]()
        raise RuntimeError("private-url?cookie=secret")

    browser.cookies = closed
    manager = BrowserLoginManager(Store(), tmp_path, browser_factory=browser.factory)
    assert wait(manager, manager.start("bilibili")["id"])["error"] == "登录窗口已关闭"
    manager.close()

def test_netscape_preserves_host_scope_secure_expiry_and_httponly():
    text = netscape_cookies([cookie(), cookie("public", "www.bilibili.com", "visible", httpOnly=False,
                                             secure=False, expires=1900000000.5)]).decode()
    assert "#HttpOnly_.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tfake-session" in text
    assert "www.bilibili.com\tFALSE\t/\tFALSE\t1900000000\tpublic\tvisible" in text
    with pytest.raises(ValueError):
        netscape_cookies([cookie(value="bad\nextra")])


def make_client(tmp_path, *, enabled=True):
    app = FastAPI()
    browser = Browser()
    store = Store()
    manager = BrowserLoginManager(store, tmp_path, browser_factory=browser.factory)

    def auth(request: Request):
        if request.headers.get("authorization") != "Bearer local-test":
            raise HTTPException(401)

    register_browser_login(app, store, tmp_path, auth, enabled=enabled, manager=manager)
    return TestClient(app, headers={"Authorization": "Bearer local-test"}), manager


def test_routes_validate_platform_auth_origin_and_session(tmp_path):
    client, manager = make_client(tmp_path)
    with client:
        assert client.post("/api/settings/cookies/nope/login").status_code == 404
        assert client.post("/api/settings/cookies/bilibili/login", headers={"Origin": "https://evil.test"}).status_code == 403
        assert client.post("/api/settings/cookies/bilibili/login", headers={"Origin": "null"}).status_code == 403
        assert client.post("/api/settings/cookies/bilibili/login", headers={"Origin": "http://["}).status_code == 403
        assert client.post("/api/settings/cookies/bilibili/login", headers={"Authorization": ""}).status_code == 401
        result = client.post("/api/settings/cookies/bilibili/login", headers={"Origin": "http://testserver"})
        assert result.status_code == 200
        sid = result.json()["id"]
        assert client.get(f"/api/settings/cookie-logins/{sid}").status_code == 200
        assert client.delete(f"/api/settings/cookie-logins/{sid}").json()["status"] == "cancelled"
        assert client.get("/api/settings/cookie-logins/unknown").status_code == 404
        assert client.delete("/api/settings/cookie-logins/unknown").status_code == 404
    assert manager._stopped


def test_remote_mode_cannot_launch_or_read_logins(tmp_path):
    client, manager = make_client(tmp_path, enabled=False)
    with client:
        assert client.post("/api/settings/cookies/bilibili/login").status_code == 403
        assert client.get("/api/settings/cookie-logins/any").status_code == 403
        assert client.delete("/api/settings/cookie-logins/any").status_code == 403
    assert manager._sessions == {}
