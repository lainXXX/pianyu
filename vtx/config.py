"""Runtime configuration. Environment overrides config/app.json."""
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _default_browser() -> str:
    if platform.system() == "Windows":
        for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if base and (Path(base) / "Microsoft/Edge/Application/msedge.exe").exists():
                return str(Path(base) / "Microsoft/Edge/Application/msedge.exe")
    return "chromium"


@dataclass(frozen=True)
class Settings:
    work_dir: Path
    cookies_dir: Path
    proxy_youtube: str        # "" = no proxy
    browser: str              # path or command of Edge / Chromium
    browser_headless: bool
    cdp_port: int
    ffmpeg: str

    def cookie_file(self, platform_name: str):
        path = self.cookies_dir / f"cookies-{platform_name}.txt"
        return path if path.exists() else None


def load() -> Settings:
    env = os.environ.get
    from vtx.configuration import AppPaths

    work = Path(env("VTX_WORK_DIR", ROOT / "work"))
    paths = AppPaths.for_app(work, ROOT)
    data = json.loads(paths.app.read_text(encoding="utf-8")) if paths.app.exists() else {}
    return Settings(
        work_dir=work,
        cookies_dir=Path(env("VTX_COOKIES_DIR", paths.cookies_dir)),
        proxy_youtube=env("VTX_PROXY_YOUTUBE", data.get("proxy_youtube", "")),
        browser=env("VTX_BROWSER", data.get("browser") or _default_browser()),
        browser_headless=env("VTX_BROWSER_HEADLESS", "1" if data.get("browser_headless", False) else "0") == "1",
        cdp_port=int(env("VTX_CDP_PORT", data.get("cdp_port", 9333))),
        ffmpeg=env("VTX_FFMPEG", data.get("ffmpeg", "ffmpeg")),
    )
