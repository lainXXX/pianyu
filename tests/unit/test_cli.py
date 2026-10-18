"""T11: CLI exit codes and messages (pipeline stubbed)."""
from pathlib import Path

import pytest

from vtx.asr_cloud import CloudASRError
from vtx.cli import main
from vtx.fetch import FetchError
from vtx.pipeline import RunResult
from vtx.target import UnsupportedURL


def ok_run(url, out, progress):
    progress("fetch")
    meta = {"title": "示例", "engine": "dashscope/paraformer-v2", "billed_seconds": 194}
    return RunResult({"reading": Path(out) / "x.reading.txt"}, meta, issues=1, edits=3, cached_asr=False)


def raising(exc):
    def run(url, out, progress):
        raise exc
    return run


def test_success(capsys):
    assert main(["extract", "https://b23.tv/x"], run=ok_run) == 0
    out = capsys.readouterr()
    assert "计费 194 秒" in out.out and "删除口语 3 处" in out.out and "获取音频" in out.err


@pytest.mark.parametrize("exc,code", [
    (UnsupportedURL("不支持的链接: x"), 2),
    (FetchError("dead", "B站视频不可用"), 3),
    (CloudASRError("ASR_ACCESS_DENIED", "bad key"), 4),
])
def test_failures_have_distinct_exit_codes(exc, code, capsys):
    assert main(["extract", "https://x"], run=raising(exc)) == code
    assert capsys.readouterr().err.strip()


def test_cache_hit_message(capsys):
    def cached(url, out, progress):
        r = ok_run(url, out, progress)
        r.cached_asr = True
        return r
    assert main(["extract", "https://b23.tv/x"], run=cached) == 0
    assert "未重复计费" in capsys.readouterr().out
