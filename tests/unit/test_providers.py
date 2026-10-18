"""Catalog entries must be accepted by their actual registry and adapter."""
import pytest

from vtx.engines import Registry
from vtx.llm import LLMRegistry
from vtx.providers import ASR_PRESETS, LLM_PRESETS


@pytest.mark.parametrize("preset", [p for p in ASR_PRESETS if p["preset"] != "custom"], ids=lambda p: p["preset"])
def test_asr_presets_configure_existing_adapters(tmp_path, preset):
    registry = Registry(tmp_path / "engines.json", tmp_path / "keys.json", resolve=lambda _: ["93.184.216.34"])
    sid = registry.add(preset, "fixture-key")
    service = registry.get(sid)
    assert service["configured"]
    assert service["kind"] == preset["kind"]
    assert service["model"] == preset["model"]
    if preset["preset"].startswith("openai-transcribe"):
        assert service["timestamps"] == "approx"  # Transcribe does not accept verbose_json.
    assert set(("official_url", "console_url", "docs_url")) <= preset.keys()


@pytest.mark.parametrize("preset", [p for p in LLM_PRESETS if p["preset"] != "custom"], ids=lambda p: p["preset"])
def test_llm_presets_configure_chat_client(tmp_path, preset):
    registry = LLMRegistry(tmp_path / "llm.json", tmp_path / "keys.json", fallback_keys={"dashscope": lambda: "fixture-key"},
                           resolve=lambda _: ["93.184.216.34"])
    fields = {**preset, "model": preset["model"] or "ep-user-model"}
    sid = registry.add(fields, "fixture-key")
    client = registry.client(sid)
    assert client.model == fields["model"]
    assert client.base_url == fields["base_url"]
    assert set(("official_url", "console_url", "docs_url")) <= preset.keys()
