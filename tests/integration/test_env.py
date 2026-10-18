"""T0 acceptance 1: toolchain present."""
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.integration


def _version(cmd):
    exe = shutil.which(cmd[0])
    assert exe, f"{cmd[0]} not on PATH"
    out = subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()[0]


def test_python_is_312():
    assert sys.version_info[:2] == (3, 12)


def test_ffmpeg_and_ytdlp_available():
    assert _version(["ffmpeg", "-version"]).startswith("ffmpeg version")
    assert _version(["yt-dlp", "--version"])
