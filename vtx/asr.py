"""Shared ASR pieces: the Segment type, tag stripping, long-span splitting and Silero VAD.

Engine contract (cloud engines in asr_cloud / asr_openai / asr_tencent):
    engine.transcribe(wav_path) -> list[Segment]
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

import soundfile as sf

from vtx.config import ROOT

SR = 16000
MAX_SPEECH_S = 29.5   # + up to 2 x PAD_S keeps every window <= 30 s
MODELS = ROOT / "models"
_TAG = re.compile(r"<\|[^|>]*\|>")


@dataclass
class Segment:
    index: int
    start: float                 # speech start on the video timeline, seconds
    end: float
    text: str
    token_times: List[float] = field(default_factory=list)  # video-timeline seconds, if the model gives them


def strip_tags(text: str) -> str:
    return _TAG.sub("", text).strip()


def split_long_spans(
    spans: Sequence[Tuple[int, int]],
    max_len: int,
    read: Callable[[int, int], "object"],
    frame: int = 1600,
) -> List[Tuple[int, int]]:
    """Cut spans longer than max_len at the quietest frame in their last third.

    The VAD's own max_speech_duration is not reliable (seen 94 s spans), so it is enforced here.
    """
    import numpy as np

    out = []
    for start, end in spans:
        while end - start > max_len:
            lo, hi = start + max_len * 2 // 3, start + max_len
            samples = np.asarray(read(lo, hi), dtype="float32")
            n = len(samples) // frame
            if n < 1:
                cut = hi
            else:
                energy = (samples[: n * frame].reshape(n, frame) ** 2).mean(axis=1)
                cut = lo + int(energy.argmin()) * frame + frame // 2
            out.append((start, cut))
            start = cut
        out.append((start, end))
    return out


# ---------------------------------------------------------------- Silero VAD (sherpa-onnx)

VAD_MODEL = MODELS / "silero_vad.onnx"


def silero_spans(wav: Path, threads: int = 1, chunk_s: int = 30) -> List[Tuple[int, int]]:
    """Speech spans in samples, streaming the file so long audio never sits in memory twice."""
    if not VAD_MODEL.is_file():
        raise FileNotFoundError("缺少语音分段模型，请先运行 python scripts/download_vad.py")
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(VAD_MODEL)
    cfg.silero_vad.min_silence_duration = 0.4
    cfg.silero_vad.min_speech_duration = 0.25
    cfg.silero_vad.max_speech_duration = MAX_SPEECH_S
    cfg.sample_rate = SR
    cfg.num_threads = threads
    vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=180)

    spans = []

    def drain():
        while not vad.empty():
            seg = vad.front
            spans.append((seg.start, seg.start + len(seg.samples)))
            vad.pop()

    window = cfg.silero_vad.window_size
    with sf.SoundFile(str(wav)) as fh:
        assert fh.samplerate == SR and fh.channels == 1, "expect 16 kHz mono"
        while True:
            block = fh.read(SR * chunk_s, dtype="float32")
            if not len(block):
                break
            for i in range(0, len(block), window):
                vad.accept_waveform(block[i:i + window])
            drain()
    vad.flush()
    drain()
    return spans
