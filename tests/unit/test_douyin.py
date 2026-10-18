"""抖音 browser channel: tab selection and retry (CDP stubbed)."""
import pytest

from vtx import cdp, douyin
from vtx.fetch import FetchError

OK = '{"aweme_detail": {"aweme_id": "1", "duration": 5000, "desc": "t", "video": {"play_addr": {"url_list": ["https://v.douyinvod.com/x"]}}}}'


@pytest.mark.parametrize("url,expected", [
    ("https://www.douyin.com/jingxuan", True),
    ("https://douyin.com/", True),
    ("file:///D:/work/review/index.html?from=douyin.com", False),
    ("https://example.com/?u=https://www.douyin.com/", False),
    ("about:blank", False),
])
def test_on_host(url, expected):
    assert cdp.on_host(url, "douyin.com") is expected


class Settings:
    browser, cdp_port, browser_headless = "edge", 9333, False

    class work_dir:
        @staticmethod
        def __truediv__(other):
            return other


@pytest.fixture
def stub(monkeypatch):
    calls = {"evaluate": [], "reload": 0}
    monkeypatch.setattr(cdp, "ensure_browser", lambda *a, **k: None)
    monkeypatch.setattr(cdp, "page_for", lambda *a, **k: "ws://x")
    monkeypatch.setattr(cdp, "reload", lambda *a, **k: calls.__setitem__("reload", calls["reload"] + 1))
    return monkeypatch, calls


def _settings(tmp_path):
    s = Settings()
    s.work_dir = tmp_path
    return s


def test_retry_once_after_empty_response(stub, tmp_path):
    monkeypatch, calls = stub
    answers = iter(["", OK])
    monkeypatch.setattr(cdp, "evaluate", lambda ws, js: calls["evaluate"].append(js) or next(answers))
    detail = douyin.fetch_detail(_settings(tmp_path), "1")
    assert detail["duration"] == 5 and calls["reload"] == 1 and len(calls["evaluate"]) == 2
    assert calls["evaluate"][0].lstrip().startswith("fetch('https://www.douyin.com/")


def test_retry_once_after_cdp_error(stub, tmp_path):
    monkeypatch, calls = stub
    answers = iter([cdp.CDPError("Failed to parse URL"), OK])

    def evaluate(ws, js):
        a = next(answers)
        if isinstance(a, Exception):
            raise a
        return a

    monkeypatch.setattr(cdp, "evaluate", evaluate)
    assert douyin.fetch_detail(_settings(tmp_path), "1")["play_urls"]


def test_gives_up_after_two_attempts(stub, tmp_path):
    monkeypatch, calls = stub
    monkeypatch.setattr(cdp, "evaluate", lambda ws, js: "")
    with pytest.raises(FetchError) as info:
        douyin.fetch_detail(_settings(tmp_path), "1")
    assert info.value.category == "risk_control" and calls["reload"] == 1


def test_multiline_desc_becomes_one_line_title(stub, tmp_path):
    monkeypatch, _ = stub
    body = OK.replace('"desc": "t"', '"desc": "第一行 #话题  \\n注：第二行"')
    monkeypatch.setattr(cdp, "evaluate", lambda ws, js: body)
    assert douyin.fetch_detail(_settings(tmp_path), "1")["title"] == "第一行 #话题 注：第二行"


def test_dead_is_not_retried(stub, tmp_path):
    monkeypatch, calls = stub
    monkeypatch.setattr(cdp, "evaluate", lambda ws, js: '{"aweme_detail": null, "filter_detail": {"filter_reason": "x"}}')
    with pytest.raises(FetchError) as info:
        douyin.fetch_detail(_settings(tmp_path), "1")
    assert info.value.category == "dead" and calls["reload"] == 0
