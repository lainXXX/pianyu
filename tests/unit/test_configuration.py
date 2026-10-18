import json
from pathlib import Path

import pytest

from vtx.configuration import AppPaths, SettingsStore, atomic_write


def test_paths_production_test_and_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("VTX_CONFIG_DIR", raising=False)
    assert AppPaths.for_app(tmp_path / "work", tmp_path).config_dir == tmp_path / "config"
    isolated = tmp_path / "isolated"
    assert AppPaths.for_app(isolated, tmp_path).config_dir == isolated / "config"
    assert AppPaths(tmp_path, isolated).jobs == isolated / "jobs"
    assert AppPaths(tmp_path, isolated).logs == isolated / "logs"
    override = tmp_path / "custom"
    monkeypatch.setenv("VTX_CONFIG_DIR", str(override))
    assert AppPaths.for_app(isolated, tmp_path).config_dir == override


def test_migration_splits_settings_copies_bytes_and_preserves_sources(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    original = {"proxy_youtube": "http://example.invalid", "system_prompt": "虚构规则", "port": 8765}
    (work / "settings.json").write_text(json.dumps(original), encoding="utf-8")
    paths = AppPaths(tmp_path, work)
    pairs = {"engines.json": paths.asr_services, "engine_keys.json": paths.asr_secrets,
             "llm.json": paths.llm_services, "llm_keys.json": paths.llm_secrets,
             "outputs.json": paths.outputs, "prompts.json": paths.legacy_prompts,
             "poster_templates.json": paths.templates, "frame.json": paths.frame}
    for name in pairs:
        (work / name).write_bytes(b'{"synthetic": "placeholder"}\n')
    (tmp_path / "cookies-youtube.txt").write_bytes(b"synthetic cookie bytes\r\n")
    (tmp_path / ".env").write_text("SYNTHETIC=example", encoding="utf-8")
    copied = paths.migrate_legacy()
    assert len(copied) == 11
    assert json.loads(paths.app.read_text(encoding="utf-8")) == {"proxy_youtube": "http://example.invalid", "port": 8765}
    assert paths.system_prompt.read_text(encoding="utf-8") == "虚构规则"
    assert json.loads((work / "settings.json").read_text(encoding="utf-8")) == original
    for name, target in pairs.items():
        assert target.read_bytes() == (work / name).read_bytes()
    assert paths.cookie_file("youtube").read_bytes() == b"synthetic cookie bytes\r\n"
    assert not list(paths.config_dir.rglob(".env"))


def test_repeated_migration_does_not_overwrite_new_configuration(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "settings.json").write_text('{"system_prompt":"old","proxy_youtube":"old"}')
    (work / "engine_keys.json").write_bytes(b"old secret placeholder")
    paths = AppPaths(tmp_path, work)
    atomic_write(paths.app, b'{"proxy_youtube":"new"}')
    atomic_write(paths.system_prompt, b"new prompt")
    atomic_write(paths.asr_secrets, b"new secret placeholder")
    assert paths.migrate_legacy() == []
    assert paths.app.read_bytes() == b'{"proxy_youtube":"new"}'
    assert paths.system_prompt.read_bytes() == b"new prompt"
    assert paths.asr_secrets.read_bytes() == b"new secret placeholder"


def test_empty_migration_has_no_side_effects(tmp_path):
    paths = AppPaths(tmp_path, tmp_path / "isolated")
    assert paths.migrate_legacy() == []
    assert not paths.config_dir.exists()


def test_initialize_materializes_files_without_overwriting_or_skipping_legacy_prompts(tmp_path):
    paths = AppPaths(tmp_path, tmp_path / "work")
    atomic_write(paths.legacy_prompts, b"{}")
    atomic_write(paths.system_prompt, b"custom rules")
    paths.initialize()
    assert paths.app.exists() and paths.llm_secrets.read_text() == "{}"
    assert paths.system_prompt.read_text() == "custom rules"
    assert not paths.outputs.exists()
    assert paths.templates.exists() and paths.cookies_dir.exists()


def test_complete_new_configuration_does_not_parse_corrupt_legacy_settings(tmp_path):
    paths = AppPaths(tmp_path, tmp_path / "work")
    paths.work_dir.mkdir()
    (paths.work_dir / "settings.json").write_text("{bad legacy data")
    atomic_write(paths.app, b"{}")
    atomic_write(paths.system_prompt, b"new rules")
    assert paths.migrate_legacy() == []


def test_settings_legacy_signature_and_separate_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("VTX_PROXY_YOUTUBE", "http://fallback.invalid")
    legacy = SettingsStore(tmp_path / ".env", tmp_path / "cookies", tmp_path / "legacy.json")
    assert legacy.proxy() == "http://fallback.invalid"
    legacy.set_system_prompt("旧签名规则")
    assert legacy.system_prompt() == "旧签名规则"
    legacy.set_proxy("")
    assert legacy.proxy() == "http://fallback.invalid"
    monkeypatch.delenv("VTX_PROXY_YOUTUBE")
    assert legacy.proxy() == ""
    paths = AppPaths(tmp_path, tmp_path / "work")
    store = SettingsStore(tmp_path / ".env", paths.cookies_dir, paths.app, paths.system_prompt)
    store.set_system_prompt("独立规则")
    store.set_proxy("http://proxy.invalid")
    assert store.system_prompt() == "独立规则"
    assert json.loads(paths.app.read_text(encoding="utf-8")) == {"proxy_youtube": "http://proxy.invalid"}
    assert not list(paths.config_dir.rglob("*.tmp"))


def test_environment_proxy_overrides_saved_proxy_including_empty(tmp_path, monkeypatch):
    store = SettingsStore(tmp_path / ".env", tmp_path / "cookies", tmp_path / "app.json")
    store.set_proxy("http://saved.invalid")
    monkeypatch.setenv("VTX_PROXY_YOUTUBE", "http://override.invalid")
    assert store.proxy() == "http://override.invalid"
    monkeypatch.setenv("VTX_PROXY_YOUTUBE", "")
    assert store.proxy() == ""


def test_cookies_accept_http_only_without_changing_content(tmp_path):
    store = SettingsStore(tmp_path / ".env", tmp_path / "cookies", tmp_path / "app.json")
    text = "# Netscape HTTP Cookie File\n#HttpOnly_.example.invalid\tTRUE\t/\tTRUE\t0\tsession\tsynthetic\n"
    assert store.set_cookies("youtube", text.encode("utf-8-sig")) == 1
    assert (tmp_path / "cookies" / "cookies-youtube.txt").read_text(encoding="utf-8") == text
    assert store.public()["cookies"] == {"bilibili": False, "douyin": False, "youtube": True}
    with pytest.raises(ValueError):
        store.set_cookies("bilibili", b"# comment only")


def test_atomic_write_keeps_old_file_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "app.json"
    path.write_bytes(b"old")

    def fail(*args):
        raise OSError("synthetic failure")

    monkeypatch.setattr("vtx.configuration.os.replace", fail)
    with pytest.raises(OSError):
        atomic_write(path, b"new")
    assert path.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [path]
