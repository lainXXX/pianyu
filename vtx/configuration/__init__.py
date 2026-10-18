"""Persistent user configuration, separate from disposable runtime files."""
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@dataclass(frozen=True)
class AppPaths:
    root: Path
    work_dir: Path
    config_dir: Optional[Path] = None

    def __post_init__(self):
        root, work = Path(self.root), Path(self.work_dir)
        config = self.config_dir
        if config is None:
            config = root / "config" if work.resolve() == (root / "work").resolve() else work / "config"
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "work_dir", work)
        object.__setattr__(self, "config_dir", Path(config))

    @classmethod
    def for_app(cls, work_dir: Path, root: Path):
        return cls(root, work_dir, os.environ.get("VTX_CONFIG_DIR") or None)

    @property
    def app(self):
        return self.config_dir / "app.json"

    @property
    def asr_services(self):
        return self.config_dir / "services" / "asr.json"

    @property
    def llm_services(self):
        return self.config_dir / "services" / "llm.json"

    @property
    def asr_secrets(self):
        return self.config_dir / "secrets" / "asr.json"

    @property
    def llm_secrets(self):
        return self.config_dir / "secrets" / "llm.json"

    @property
    def system_prompt(self):
        return self.config_dir / "prompts" / "system.md"

    @property
    def outputs(self):
        return self.config_dir / "prompts" / "outputs.json"

    @property
    def legacy_prompts(self):
        return self.config_dir / "prompts" / "legacy.json"

    @property
    def templates(self):
        return self.config_dir / "layouts" / "templates.json"

    @property
    def frame(self):
        return self.config_dir / "layouts" / "frame.json"

    @property
    def cookies_dir(self):
        return self.config_dir / "cookies"

    @property
    def workflow(self):
        return self.config_dir / "workflow.json"

    @property
    def jobs(self):
        return self.work_dir / "jobs"

    @property
    def logs(self):
        return self.root / "logs" if self.work_dir.resolve() == (self.root / "work").resolve() else self.work_dir / "logs"

    def cookie_file(self, platform: str):
        return self.cookies_dir / f"cookies-{platform}.txt"

    def migrate_legacy(self):
        """Copy legacy files once. Never overwrite destinations or remove originals."""
        copied = []

        def put(target, content):
            if not target.exists():
                atomic_write(target, content)
                copied.append(target)

        settings = self.work_dir / "settings.json"
        if settings.exists() and (not self.app.exists() or not self.system_prompt.exists()):
            data = json.loads(settings.read_text(encoding="utf-8"))
            prompt = data.pop("system_prompt", None)
            put(self.app, json.dumps(data, ensure_ascii=False).encode("utf-8"))
            if isinstance(prompt, str):
                put(self.system_prompt, prompt.encode("utf-8"))
        for source, target in (
            ("engines.json", self.asr_services), ("engine_keys.json", self.asr_secrets),
            ("llm.json", self.llm_services), ("llm_keys.json", self.llm_secrets),
            ("outputs.json", self.outputs), ("prompts.json", self.legacy_prompts),
            ("poster_templates.json", self.templates), ("frame.json", self.frame),
            ("workflow.json", self.workflow),
        ):
            legacy = self.work_dir / source
            if legacy.exists() and not target.exists():
                put(target, legacy.read_bytes())
        for platform in SettingsStore.PLATFORMS if self.work_dir.resolve() == (self.root / "work").resolve() else ():
            legacy = self.root / f"cookies-{platform}.txt"
            target = self.cookie_file(platform)
            if legacy.exists() and not target.exists():
                put(target, legacy.read_bytes())
        return copied

    def initialize(self):
        """Materialize editable defaults once, leaving migrated and custom values intact."""
        from vtx.system_prompt import DEFAULT_SYSTEM_PROMPT, LEGACY_SYSTEM_PROMPT_HASHES

        defaults = {
            self.app: {"host": "127.0.0.1", "port": 8765, "proxy_youtube": "", "ffmpeg": "ffmpeg"},
            self.llm_services: {"services": [], "last_used": None},
            self.asr_secrets: {}, self.llm_secrets: {},
            self.outputs: {"overrides": {}, "hidden": [], "custom": []},
            self.templates: {"overrides": {}, "custom": []}, self.frame: {},
        }
        for path, data in defaults.items():
            # Legacy prompt entries must be handled by OutputStore before initializing outputs.
            if path == self.outputs and self.legacy_prompts.exists():
                continue
            if not path.exists():
                atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        if not self.system_prompt.exists() or hashlib.sha256(self.system_prompt.read_bytes()).hexdigest() in LEGACY_SYSTEM_PROMPT_HASHES:
            atomic_write(self.system_prompt, DEFAULT_SYSTEM_PROMPT.encode("utf-8"))
        self.cookies_dir.mkdir(parents=True, exist_ok=True)


class SettingsStore:
    """Editable settings with optional standalone system prompt storage."""

    PLATFORMS = ("bilibili", "douyin", "youtube")

    def __init__(self, env_file: Path, cookies_dir: Path, settings_file: Path,
                 system_prompt_file: Optional[Path] = None):
        self.env_file, self.cookies_dir, self.settings_file = Path(env_file), Path(cookies_dir), Path(settings_file)
        self.system_prompt_file = Path(system_prompt_file) if system_prompt_file is not None else None

    def _env(self) -> dict:
        if not self.env_file.exists():
            return {}
        pairs = (line.split("=", 1) for line in self.env_file.read_text(encoding="utf-8").splitlines() if "=" in line)
        return {key.strip(): value.strip() for key, value in pairs}

    def _data(self):
        return json.loads(self.settings_file.read_text(encoding="utf-8")) if self.settings_file.exists() else {}

    def proxy(self) -> str:
        if "VTX_PROXY_YOUTUBE" in os.environ:
            return os.environ["VTX_PROXY_YOUTUBE"]
        if self.settings_file.exists():
            return self._data().get("proxy_youtube", "")
        return os.environ.get("VTX_PROXY_YOUTUBE", "")

    def public(self) -> dict:
        return {"proxy_youtube": self.proxy(),
                "cookies": {platform: (self.cookies_dir / f"cookies-{platform}.txt").exists()
                            for platform in self.PLATFORMS}}

    def system_prompt(self) -> str:
        from vtx.system_prompt import DEFAULT_SYSTEM_PROMPT, LEGACY_SYSTEM_PROMPT_HASHES

        if self.system_prompt_file is not None and self.system_prompt_file.exists():
            value = self.system_prompt_file.read_text(encoding="utf-8")
        else:
            value = self._data().get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        return DEFAULT_SYSTEM_PROMPT if hashlib.sha256(value.encode()).hexdigest() in LEGACY_SYSTEM_PROMPT_HASHES else value

    def set_system_prompt(self, value: str) -> None:
        if self.system_prompt_file is None:
            self._update({"system_prompt": value})
        else:
            atomic_write(self.system_prompt_file, value.encode("utf-8"))

    def _update(self, fields: dict) -> None:
        data = self._data()
        data.update(fields)
        if self.system_prompt_file is not None:
            data.pop("system_prompt", None)
        atomic_write(self.settings_file, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def set_proxy(self, proxy: str) -> None:
        self._update({"proxy_youtube": proxy})

    def set_cookies(self, platform: str, content: bytes) -> int:
        text = content.decode("utf-8-sig")
        rows = [line for line in text.splitlines()
                if line and (not line.startswith("#") or line.startswith("#HttpOnly_"))
                and len(line.removeprefix("#HttpOnly_").split("\t")) >= 7]
        if not rows:
            raise ValueError("不是 Netscape 格式的 cookies.txt（应为 7 列、制表符分隔）")
        atomic_write(self.cookies_dir / f"cookies-{platform}.txt", text.encode("utf-8"))
        return len(rows)
