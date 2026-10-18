# 配置存储

`AppPaths.for_app(work_dir, root)` 默认使用项目根目录的 `config/`；非默认工作目录使用 `work_dir/config/`，避免测试与项目配置混用。`VTX_CONFIG_DIR` 可指定配置目录。

| 路径 | 内容 |
| --- | --- |
| `app.json` | 运行参数与代理，不含模型密钥或提示词 |
| `workflow.json` | 最近选择的输出风格 |
| `services/asr.json`、`services/llm.json` | 识别服务、模型配置与默认选择 |
| `secrets/asr.json`、`secrets/llm.json` | 对应服务注册表的密钥，保留原注册表 schema |
| `prompts/system.md` | 全局规则原文 |
| `prompts/outputs.json` | 输出风格及绑定版式，保留 OutputStore schema |
| `prompts/legacy.json` | 旧提示词存储，用于兼容迁移 |
| `layouts/templates.json`、`layouts/frame.json` | 版式与画框，保留各存储 schema |
| `cookies/cookies-{platform}.txt` | 平台 Netscape cookies，支持 HttpOnly 行 |

`app.json` 可包含以下字段，未设置的参数由运行配置的默认值或兼容环境变量提供：

```json
{
  "proxy_youtube": "",
  "browser": "chromium",
  "browser_headless": false,
  "cdp_port": 9333,
  "ffmpeg": "ffmpeg",
  "host": "127.0.0.1",
  "port": 8765
}
```

识别与 AI 任务仍保存在 `work/jobs/`。默认项目日志位于 `logs/`；隔离工作目录的日志位于 `work_dir/logs/`。

首次启动调用 `paths.migrate_legacy()`，从旧工作目录复制配置及项目根目录的平台 cookies。旧文件不删除，已存在的目标文件不覆盖，字节内容原样保留；仅 `settings.json` 拆分为 `app.json` 和 `prompts/system.md`。`.env` 仅兼容读取，不复制。迁移和设置写入使用临时文件加原子替换，不打印配置内容。

迁移后 `initialize()` 补齐可编辑默认文件。精确匹配已淘汰默认版本的系统规则升级为当前默认，自定义规则保留；风格及版式文件保存自定义与覆盖项，内置项由代码提供。`services/asr.json`、`services/llm.json` 的 `services` 数组保存服务；`secrets` 下对应 JSON 使用服务 id 到密钥字符串的映射，腾讯双字段沿用注册表序列化格式。设置页可避免手动处理 id。

环境变量优先于 `app.json`，包括空的 `VTX_PROXY_YOUTUBE` 会关闭已保存的代理。登录浏览器使用 `work/browser-profiles/{platform}/`；Cookie 文件与 browser profile 均包含登录资料，不提交版本库。隔离工作目录不会自动复制项目根目录的真实 Cookie。
