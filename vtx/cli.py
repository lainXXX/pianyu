"""Command line: vtx extract <url> [-o DIR]

Exit codes: 0 ok, 2 bad input / unsupported URL, 3 fetch failed, 4 ASR failed.
"""
import argparse
import sys
from pathlib import Path

from vtx.asr_cloud import CloudASRError
from vtx.fetch import FetchError
from vtx.target import UnsupportedURL

STAGES = {"resolve": "解析链接", "fetch": "获取音频", "asr": "云端识别", "qc": "自动质检", "output": "写出文件"}


def main(argv=None, run=None) -> int:
    if run is None:
        from vtx.pipeline import run
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # Windows consoles default to GBK
    ap = argparse.ArgumentParser(prog="vtx", description="视频链接 → 原文文本")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="提取一个视频的文本")
    ex.add_argument("url", help="B站 / 抖音 / YouTube 视频链接（可直接粘贴分享文案）")
    ex.add_argument("-o", "--out", default="output", help="输出目录（默认 ./output）")
    args = ap.parse_args(argv)

    try:
        result = run(args.url, Path(args.out), progress=lambda s, info=None: print(f"… {STAGES.get(s, s)}", file=sys.stderr))
    except UnsupportedURL as exc:
        print(f"链接无法处理：{exc}", file=sys.stderr)
        return 2
    except FetchError as exc:
        print(f"获取失败（{exc.category}）：{exc}", file=sys.stderr)
        return 3
    except CloudASRError as exc:
        print(f"识别失败（{exc.code}）：{exc}", file=sys.stderr)
        return 4

    m = result.meta
    billed = "命中缓存，未重复计费" if result.cached_asr else f"计费 {m['billed_seconds']} 秒"
    print(f"完成：{m['title']}")
    print(f"来源：{m['engine']}，{billed}；删除口语 {result.edits} 处；疑点 {result.issues} 处")
    for name, path in result.files.items():
        print(f"  {name:8} {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
