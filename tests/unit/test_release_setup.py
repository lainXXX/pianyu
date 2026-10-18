"""First-install checks without real network access or private model files."""
import hashlib
from io import BytesIO

import pytest

from scripts import download_vad
from vtx import asr


def test_missing_vad_model_has_install_instruction(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "VAD_MODEL", tmp_path / "missing.onnx")
    with pytest.raises(FileNotFoundError, match="scripts/download_vad.py"):
        asr.silero_spans(tmp_path / "unused.wav")


def test_model_download_validates_before_installing(tmp_path, monkeypatch):
    content = b"synthetic-model-for-install-test"
    monkeypatch.setattr(download_vad, "SHA256", hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(download_vad, "urlopen", lambda *args, **kwargs: BytesIO(content))
    destination = tmp_path / "models" / "silero_vad.onnx"
    assert download_vad.download(destination) == destination
    assert destination.read_bytes() == content
    monkeypatch.setattr(download_vad, "urlopen", lambda *args, **kwargs: pytest.fail("already installed"))
    assert download_vad.download(destination) == destination


def test_invalid_model_download_is_not_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(download_vad, "urlopen", lambda *args, **kwargs: BytesIO(b"invalid"))
    destination = tmp_path / "models" / "silero_vad.onnx"
    with pytest.raises(ValueError, match="checksum mismatch"):
        download_vad.download(destination)
    assert not destination.exists()


def test_existing_unknown_model_is_not_overwritten(tmp_path, monkeypatch):
    destination = tmp_path / "silero_vad.onnx"
    destination.write_bytes(b"user-model")
    monkeypatch.setattr(download_vad, "urlopen", lambda *args, **kwargs: pytest.fail("do not overwrite"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        download_vad.download(destination)
    assert destination.read_bytes() == b"user-model"
