"""Download the pinned Silero VAD model from the sherpa-onnx release."""
import argparse
import hashlib
import os
from pathlib import Path
from urllib.request import urlopen

URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
SHA256 = "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
DEFAULT_PATH = Path(__file__).resolve().parents[1] / "models" / "silero_vad.onnx"


def download(destination: Path = DEFAULT_PATH) -> Path:
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest() != SHA256:
            raise ValueError(f"Model checksum mismatch: {destination}. Move it aside and retry.")
        return destination
    with urlopen(URL, timeout=60) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise ValueError("Downloaded model checksum mismatch; no model was saved.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".onnx.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    try:
        print(download(args.output))
    except (OSError, ValueError) as error:
        parser.exit(1, f"VAD model: {error}\n")
