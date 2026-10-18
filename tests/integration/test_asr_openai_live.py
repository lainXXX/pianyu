"""T19 acceptance 3: real OpenAI-compatible service, only when the user configured one.

Set VTX_TEST_OPENAI_BASE, VTX_TEST_OPENAI_KEY, VTX_TEST_OPENAI_MODEL to run.
"""
import os
from pathlib import Path

import pytest

from vtx.asr_openai import OpenAICompatEngine

pytestmark = pytest.mark.integration
FIX = Path(__file__).resolve().parents[1] / "fixtures"
BASE, KEY, MODEL = (os.environ.get(f"VTX_TEST_OPENAI_{k}") for k in ("BASE", "KEY", "MODEL"))


@pytest.mark.skipif(not (BASE and KEY and MODEL), reason="用户未提供 OpenAI 兼容服务的 key（设计 4.8：无 key 只做模拟测试）")
def test_live_openai_compatible():
    engine = OpenAICompatEngine("live", BASE, MODEL, KEY)
    segs = engine.transcribe(FIX / "sil10_speech_sil5.wav")
    assert segs and "毫升" in "".join(s.text for s in segs)
    assert 9.0 <= segs[0].start <= 11.0 or engine.timestamps_mode == "approx"
