# 第三方说明

项目根目录的 [MIT 协议](LICENSE)适用于片语的原创代码，不更改第三方软件、模型或内容的许可证。
源码仓库不捆绑 Python 依赖、浏览器、FFmpeg、模型权重、系统字体及平台视频。

## 主要依赖

安装版本见 [requirements.txt](requirements.txt)，完整许可应以所安装版本的上游源码、发行包及其许可证为准。

| 项目 | 上游许可 | 来源 |
| --- | --- | --- |
| FastAPI | MIT | <https://github.com/fastapi/fastapi> |
| Starlette | BSD-3-Clause | <https://github.com/encode/starlette> |
| Uvicorn | BSD-3-Clause | <https://github.com/encode/uvicorn> |
| Pydantic | MIT | <https://github.com/pydantic/pydantic> |
| HTTPX | BSD-3-Clause | <https://github.com/encode/httpx> |
| Playwright | Apache-2.0 | <https://github.com/microsoft/playwright-python> |
| yt-dlp | Unlicense，部分组件另有许可 | <https://github.com/yt-dlp/yt-dlp> |
| F2 | Apache-2.0 | <https://github.com/Johnserf-Seed/f2> |
| browser-cookie3 | LGPL，具体版本见上游 | <https://github.com/borisbabic/browser_cookie3> |
| sherpa-onnx | Apache-2.0 | <https://github.com/k2-fsa/sherpa-onnx> |
| OpenCC | Apache-2.0 | <https://github.com/BYVoid/OpenCC> |
| NumPy | BSD-3-Clause，捆绑组件另有许可 | <https://github.com/numpy/numpy> |
| SoundFile | BSD-3-Clause，libsndfile 另有许可 | <https://github.com/bastibe/python-soundfile> |
| Jinja2 / MarkupSafe | BSD-3-Clause | <https://palletsprojects.com/> |
| markdown-it-py | MIT | <https://github.com/executablebooks/markdown-it-py> |
| jiwer | Apache-2.0 | <https://github.com/jitsi/jiwer> |

## Silero VAD 模型

语音分段使用 [Silero VAD](https://github.com/snakers4/silero-vad)，上游采用 MIT 协议。
安装脚本从 [sherpa-onnx 官方发行页](https://github.com/k2-fsa/sherpa-onnx/releases/tag/asr-models)下载
`silero_vad.onnx`，不随源码分发。

- SHA-256：`9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6`
- 重新分发模型时，需保留其上游版权及许可；不要用项目 MIT 声明替代模型许可。

## 外部程序、字体及内容

- FFmpeg 由用户自行安装，其许可取决于具体构建及所启用组件，参见 <https://ffmpeg.org/legal.html>。
- Edge、Chromium 和 Playwright 下载的浏览器遵循各自发行条款。
- 界面使用设备已有字体，仓库不分发字体文件。
- `experiments/samples.yaml` 只列公开视频链接与元数据，用于可选测试，不包含对应音视频或转写授权。
- 测试中引用的少量原句仅作行为回归样例，相关原始内容的权利仍归原作者。
- 用户下载的视频、字幕及生成内容的权利归属，不由项目软件许可证决定；平台条款和内容许可仍适用。
