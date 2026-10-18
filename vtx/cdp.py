"""Minimal Chrome DevTools Protocol client for a dedicated local browser."""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets.sync.client as ws_client


class CDPError(RuntimeError):
    pass


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as resp:
        return json.load(resp)


def is_up(port: int) -> bool:
    try:
        _get(port, "/json/version")
        return True
    except OSError:
        return False


def ensure_browser(browser: str, port: int, profile_dir: Path, start_url: str, headless: bool) -> None:
    """Start the dedicated browser if it is not already listening on `port`."""
    if is_up(port):
        return
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        browser,
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        args.append("--headless=new")
    subprocess.Popen(args + [start_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if is_up(port):
            return
        time.sleep(0.5)
    raise CDPError(f"浏览器没有在 30 秒内启动调试端口 {port}")


def _call(ws_url: str, method: str, params: dict, timeout: float = 60):
    with ws_client.connect(ws_url, max_size=None, open_timeout=10) as conn:
        conn.send(json.dumps({"id": 1, "method": method, "params": params}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            msg = json.loads(conn.recv(timeout=max(0.1, deadline - time.monotonic())))
            if msg.get("id") == 1:
                if "error" in msg:
                    raise CDPError(msg["error"].get("message", "CDP error"))
                return msg["result"]
    raise CDPError(f"{method} 超时")


def on_host(url: str, host: str) -> bool:
    """True only when the page itself is served from `host` (not just mentioning it)."""
    from urllib.parse import urlparse

    hostname = (urlparse(url).hostname or "").lower()
    return hostname == host or hostname.endswith("." + host)


def page_for(port: int, host: str, open_url: str, settle_s: float = 6) -> str:
    """Return the websocket URL of a page on `host`, opening one if needed."""
    for tab in _get(port, "/json"):
        if tab.get("type") == "page" and on_host(tab.get("url", ""), host):
            return tab["webSocketDebuggerUrl"]
    browser_ws = _get(port, "/json/version")["webSocketDebuggerUrl"]
    _call(browser_ws, "Target.createTarget", {"url": open_url})
    time.sleep(settle_s)
    for tab in _get(port, "/json"):
        if tab.get("type") == "page" and on_host(tab.get("url", ""), host):
            return tab["webSocketDebuggerUrl"]
    raise CDPError(f"无法打开 {host} 页面")


def reload(ws_url: str, url: str, settle_s: float = 6) -> None:
    _call(ws_url, "Page.navigate", {"url": url})
    time.sleep(settle_s)


def evaluate(ws_url: str, expression: str, timeout: float = 60):
    result = _call(ws_url, "Runtime.evaluate",
                   {"expression": expression, "returnByValue": True, "awaitPromise": True}, timeout)
    if result.get("exceptionDetails"):
        raise CDPError(result["exceptionDetails"].get("text", "JS exception"))
    return result.get("result", {}).get("value")
