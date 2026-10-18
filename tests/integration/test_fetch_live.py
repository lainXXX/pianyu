"""T3 acceptance 3-5 and 2: real fetches for B站 / 抖音, dead link, no secret leakage.

YouTube samples are deferred by the user (2026-10-14): blocked by bot check, needs a login cookie.
"""
from pathlib import Path

import pytest
import soundfile as sf

from vtx import config
from vtx.fetch import FetchError, fetch
from vtx.redact import COOKIE_NAMES
from vtx.target import UnsupportedURL, resolve

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]

SAMPLES = [
    "https://www.bilibili.com/video/BV1wFZ8YBEt4",
    "https://b23.tv/BV1WVfcYFEiX",
    "https://www.bilibili.com/video/BV1Wv411h7kN?p=3",
    "https://www.douyin.com/video/7665606762753510710",
    "https://www.douyin.com/video/7681626630355258665",
    "https://www.douyin.com/video/7689755493836819739",
]


@pytest.fixture(scope="module")
def settings():
    return config.load()


@pytest.mark.parametrize("url", SAMPLES)
def test_fetch_sample(url, settings):
    result = fetch(resolve(url), settings)
    info = sf.info(str(result.audio))
    assert info.samplerate == 16000 and info.channels == 1
    assert abs(info.frames / info.samplerate - result.duration) <= 2


def test_bilibili_ai_subtitles_listed(settings):
    result = fetch(resolve("https://www.bilibili.com/video/BV1wFZ8YBEt4"), settings)
    assert "ai-zh" in result.subtitles and "danmaku" not in result.subtitles
    assert result.subtitles["ai-zh"].read_text(encoding="utf-8").strip()


def test_dead_link_reports_dead_and_writes_nothing(settings):
    before = set(settings.work_dir.iterdir()) if settings.work_dir.exists() else set()
    with pytest.raises((FetchError, UnsupportedURL)) as info:
        fetch(resolve("https://www.bilibili.com/video/BV1zW411t7YE"), settings)
    assert "不可用" in str(info.value)
    assert set(settings.work_dir.iterdir()) == before


def _cookie_values():
    """Values of the session cookies listed in vtx.redact (nameless/short ones are not secrets)."""
    values = set()
    for path in ROOT.glob("cookies-*.txt"):
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split("\t")
            if (not line.startswith("#") and len(fields) >= 7
                    and fields[5] in COOKIE_NAMES and len(fields[6]) >= 8):
                values.add(fields[6])
    return values


def test_no_cookie_values_in_outputs_or_logs():
    secrets = _cookie_values()
    assert secrets, "no cookie files to check against"
    scanned = list(ROOT.glob("experiments/fetch_log.*"))
    for d in (ROOT / "work").glob("*-*-p*"):
        scanned += [p for p in d.iterdir() if p.suffix in (".srt", ".vtt", ".json", ".log", ".txt")]
    leaks = [p.name for p in scanned if any(s in p.read_text(encoding="utf-8", errors="ignore") for s in secrets)]
    assert not leaks, f"cookie values found in {leaks}"
