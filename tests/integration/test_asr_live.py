"""T4 / T11b acceptance on the cloud engine: timing, silence, music, a long file.

Local sherpa-onnx engines were dropped from the product on 2026-10-14 (user decision),
so these run against DashScope paraformer-v2 and need DASHSCOPE_API_KEY.
"""
import json
import subprocess
from pathlib import Path

import pytest

from vtx import config
from vtx.asr_cloud import DashScopeEngine
from vtx.fetch import fetch
from vtx.target import resolve

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"
MUSIC_URL = "https://www.bilibili.com/video/BV1f3411f7BE"  # 治愈纯音乐 钢琴曲, 6:52
LONG_RESULT = ROOT / "experiments" / "asr" / "dashscope_paraformer-v2" / "bili-3.json"  # 50 min, from run_asr


@pytest.fixture(scope="module")
def engine():
    return DashScopeEngine()


@pytest.fixture(scope="module")
def near_silence(tmp_path_factory):
    path = tmp_path_factory.mktemp("sil") / "near_silence_60s.wav"
    subprocess.run([config.load().ffmpeg, "-loglevel", "error", "-y", "-f", "lavfi", "-t", "60", "-i",
                    "anoisesrc=r=16000:color=pink:amplitude=0.002", "-ac", "1", "-c:a", "pcm_s16le", str(path)],
                   check=True)
    return path


@pytest.fixture(scope="module")
def music_clip(tmp_path_factory):
    settings = config.load()
    full = fetch(resolve(MUSIC_URL), settings).audio
    clip = tmp_path_factory.mktemp("music") / "music_120s.wav"
    subprocess.run([settings.ffmpeg, "-loglevel", "error", "-y", "-i", str(full), "-t", "120", str(clip)], check=True)
    return clip


def test_first_sentence_starts_near_10s(engine):
    segs = engine.transcribe(FIX / "sil10_speech_sil5.wav")
    assert segs, "no speech recognised"
    assert 9.5 <= segs[0].start <= 10.7, segs[0].start
    text = "".join(s.text for s in segs)
    assert "毫升" in text and ("1.5" in text or "一点五" in text)
    assert engine.last_billed_seconds > 0


def test_near_silence_produces_no_text(engine, near_silence):
    assert engine.transcribe(near_silence) == []


def test_pure_music_produces_no_text(engine, music_clip):
    segs = engine.transcribe(music_clip)
    assert not segs, [s.text for s in segs][:5]


def test_long_result_reaches_the_end():
    """50-minute sample transcribed by run_asr.py: last sentence near the end, order kept."""
    segs = json.loads(LONG_RESULT.read_text(encoding="utf-8"))
    assert segs[-1]["end"] >= 3000 - 60, segs[-1]["end"]
    starts = [s["start"] for s in segs]
    assert starts == sorted(starts)
    indices = [s["index"] for s in segs]
    assert indices == sorted(set(indices))
