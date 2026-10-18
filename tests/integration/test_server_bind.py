"""T13 acceptance 1: a real server on 127.0.0.1 is unreachable via this machine's LAN address,
and refuses to start on 0.0.0.0 without a token."""
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("223.5.5.5", 53))       # no packet is sent for UDP connect
        return s.getsockname()[0]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(env):
    return subprocess.Popen([sys.executable, "-m", "vtx.server"], cwd=ROOT, env={**os.environ, **env},
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")


def test_loopback_only():
    port = _free_port()
    proc = _start({"VTX_HOST": "127.0.0.1", "VTX_PORT": str(port), "VTX_TOKEN": ""})
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/jobs/x", timeout=1)
            except urllib.error.HTTPError as e:
                assert e.code == 404           # server answers on loopback
                break
            except OSError:
                time.sleep(0.3)
        else:
            pytest.fail("server did not come up")
        ip = _lan_ip()
        assert not ip.startswith("127.")
        with pytest.raises(OSError):
            urllib.request.urlopen(f"http://{ip}:{port}/api/jobs/x", timeout=2)
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_public_bind_without_token_exits():
    proc = _start({"VTX_HOST": "0.0.0.0", "VTX_PORT": str(_free_port()), "VTX_TOKEN": ""})
    out, _ = proc.communicate(timeout=30)
    assert proc.returncode != 0 and "访问令牌" in out
