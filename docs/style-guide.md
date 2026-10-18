# 用 AI 创建输出风格

中文 · [English](style-guide.en.md)

把本页交给 AI，描述你想要的内容组织与视觉风格，让它输出一个 JSON 风格包。在 **设置 → 输出风格 → 导入** 选择文件即可；Prompt 与版式会一起保存为独立的新项，不覆盖已有内容。

[下载可导入示例](style-package.example.json) · [项目](https://github.com/lainXXX/pianyu)

## 先选内容类型

风格决定**写什么、如何组织**，版式决定**文字如何排成图片**。一个风格可绑定多个兼容版式，第一项为默认版式；阅读页只能选择该风格绑定的版式。

全局 `system_prompt` 负责通用的识别校正、事实与表达规则。风格 Prompt 只写自身需要的组织与表达方式，不重复全局规则，不要求忽略它。

| `kind` | 适用内容 | Prompt 变量 | 模型必须返回 | 版式 `group` |
| --- | --- | --- | --- | --- |
| `free` | 笔记、文章、问答、行动清单 | `{title}`、`{text}` | Markdown 正文 | `article` |
| `points` | 测评、资讯、概念卡片等结构化要点 | `{title}`、`{text}`、`{max_points}` | 下述要点 JSON | `points` |
| `source` | 保留原意的逐段精校 | `{title}`、`{paragraphs}` | 段落 JSON 数组 | `article` |
| `structure` | 原话分节、重点标注 | `{title}`、`{sentences}` | 编号组织 JSON | `article` |

建议先从 **`free` + `article`** 开始。`points` 适合需要图片摘要和完整阅读正文分别组织的场景；原文类不能随意改写输出格式。

## 可直接发给 AI 的指令

把下面指令、你的需求和[示例 JSON](style-package.example.json)一起发给 AI：

```text
为片语生成一个可导入的 JSON 风格包。

我的需求：<填写受众、内容组织、适合的视频、视觉偏好>

只输出 JSON，不加代码围栏和说明。
遵守示例的全部字段、类型与 version=1，不增加字段。
默认选 free + article；需要结构化要点时选 points + points。
包中包括一个风格及其全部版式，style.layouts 按默认优先顺序引用包内 id。

Prompt 简短、具体，用 Markdown 标题和必要的 XML 标签区分标题与原稿。
保留 {title} 和 {text} 变量；不展开成示例内容，不写 API Key 或服务配置。
只规定内容结构与风格，不重复或覆盖全局校正规则。
模型生成正文的格式必须符合所选 kind 的数据契约。

版式只用自包含 HTML、CSS 和声明式 Jinja for/if；不包含脚本、外部资源或网络请求。
文章版式必须有 class="content" 的容器，遍历 blocks 并输出 block.html，不能手写固定正文。
保留 footer_lines，显示来源与片语链接；使用 first/last/page/pages 控制标题与页脚。
让长文能自然分页，不给内容容器写固定高度、overflow:hidden 或行数截断。
中文字体使用设备现有字体，不引入远程字体；留出页边距，正文与页脚要易读。

最终自查 JSON 转义、Prompt 变量、Jinja 变量、group 匹配和全部版式引用。
```

保存为 UTF-8 `.json` 文件。JSON 字符串中的换行写成 `\n`，双引号写成 `\"`；用 JSON 序列化工具处理，不要手工拼接长 HTML。

## 风格包格式

```json
{
  "format": "pianyu-style",
  "version": 1,
  "style": {
    "name": "主题笔记",
    "kind": "free",
    "prompt": "# 主题笔记\n<video_title>{title}</video_title>\n按主题分节，保留例子与推理。用 Markdown 输出。\n<transcript>{text}</transcript>",
    "layouts": ["paper"]
  },
  "layouts": [
    {
      "id": "paper",
      "name": "纸页",
      "group": "article",
      "width": 1080,
      "height_mode": "fixed:1600",
      "html": "<!doctype html><html><head><meta charset=\"utf-8\"><style>body{margin:0;padding:64px;font:28px/1.8 sans-serif;color:#29251f;background:#faf8f3}h1{font-size:48px;line-height:1.3}h2{font-size:34px}footer{margin-top:40px;font-size:18px;color:#777;overflow-wrap:anywhere}</style></head><body>{% if first %}<h1>{{ title }}</h1><p>{{ kicker }}</p>{% endif %}<main class=\"content\">{% for block in blocks %}{{ block.html }}{% endfor %}</main>{% if last %}<footer>{% for line in footer_lines %}<div>{{ line }}</div>{% endfor %}</footer>{% endif %}</body></html>"
    }
  ]
}
```

这是风格包的完整结构，不能混入模型或密钥配置。包内版式 id 只用于引用，导入时系统会重新分配独立 id。

- 一个包包含一个风格、1～10 个版式，文件不超过 **1 MiB**。
- Prompt 不超过 20,000 字符；单个版式 HTML 不超过 100,000 字符。
- `width` 为 320～4000 的整数；`height_mode` 为 `auto` 或 `fixed:300`～`fixed:8000`。文章推荐 `fixed:1600`，要点长图推荐 `auto`。
- `style.layouts` 不得重复，必须恰好引用包中的全部版式；第一项为默认版式。
- 版式 id 为 1～80 个英文字母、数字、下划线或短横线。
- 每个字段均为必填；不支持未知字段、跨组绑定、脚本、事件处理器、外部图片、外部 CSS 和远程字体。
- Jinja 只使用简单变量、`for`、`if` 和基本格式过滤。不要使用函数、宏、赋值、私有属性、乘方或字符串膨胀。允许的两层循环应是“外层要点 → 该要点的明细/引文”，不要交叉遍历整个数据集。

## Prompt 的输入与输出契约

Prompt 中单大括号表示片语变量，例如 `{text}`。布局中双大括号表示 Jinja 变量，例如 `{{ title }}`；循环用 `{% for ... %}`。不要把两套语法混用。Prompt 内的 JSON 示例无需把所有大括号翻倍。

### `free`

输入为原稿文字 `{text}` 和标题 `{title}`。返回 Markdown，可以包含标题、段落、列表、引用、表格。阅读页会渲染 Markdown，不直接展示语法字符；图片渲染支持标题、段落、列表、引用和行内强调，复杂表格或任意 HTML 不保证与阅读页一致。

标题作为校准材料，不是补写视频未提及事实的许可。Prompt 最好明确保留相关例子、数据和条件，避免“全面详尽”“专业高级”等空泛要求。

### `points`

返回 JSON 对象，不加代码围栏：

```json
{
  "title": "主题",
  "conclusion": "核心结论",
  "points": [{
    "heading": "一个要点",
    "body": "适合图片的简短摘要",
    "text": "适合阅读的完整说明，保留数据条件与推理。",
    "key": true,
    "detail": ["正文未重复的必要补充"],
    "quotes": [{"quote": "整理后的引用", "source": "逐字复制的来源原句"}]
  }]
}
```

最多 `{max_points}` 项（当前为 10），重点 `key=true` 最多 3 项，引文最多 4 句。没有补充或可靠引文时，`detail`、`quotes` 用空数组。`source` 必须是原稿原句，不能让 AI 自己改写；引用会经过来源核验。

### `source`

`{paragraphs}` 是含 `n`、`text` 的 JSON 数组。返回同编号、同数量数组：

```json
[{"n": 1, "text": "精校后的段落", "removed": ["删除的完整原句"]}]
```

只作校正、去赘词与分段；保留数字、否定和关系。与主题无关的整句删除须在 `removed` 留原句。程序会检查改写，拒绝的更改会恢复；不要用它生成摘要。

### `structure`

`{sentences}` 是含 `i`、`s` 的编号句子数组。返回组织结构：

```json
{
  "sections": [{"heading": "小标题", "level": 2, "blocks": [
    {"type": "p", "ids": [1, 2]},
    {"type": "list", "items": [[3], [4, 5]]}
  ]}],
  "removed": [6],
  "fixes": {"2": "校正后的完整句子"},
  "key": [1]
}
```

每个编号恰好出现在正文或 `removed` 中一次，`fixes`、`key` 只能引用输入编号。程序从原句重建正文；不要返回自己另写的段落或编造编号。

## 布局可用变量

| `article` | 说明 |
| --- | --- |
| `title`、`kicker` | 标题与来源简述 |
| `blocks` | 正文块数组，`block.html` 已转义并由应用生成，直接输出 |
| `footer_lines` | 来源、声明与项目链接，逐行输出 |
| `page`、`pages` | 当前页与总页数 |
| `first`、`last` | 是否首页/末页 |

正文必须放在 `.content` 容器中。不要再次对 `block.html` 调用转义，也不要写死 `height:1600px` 使正文被截断。分页依据实际浏览器测量结果。

| `points` | 说明 |
| --- | --- |
| `title`、`conclusion`、`kicker` | 标题、结论、来源简述 |
| `points` | 要点数组，可使用上表 JSON 中的字段 |
| `left`、`right` | 为双栏版式平衡拆分的要点数组 |
| `quotes` | 所有要点的引文 |
| `footer_lines` | 图文页脚 |

例如 `{% for p in points %}<h2>{{ p.heading }}</h2><p>{{ p.body }}</p>{% endfor %}`。字段直接由 Jinja 自动转义，不把来源文字拼成 HTML 执行。

## 导入后的检查

1. 在“版式”列表点预览，检查长标题、正文、页脚与窄屏阅读。
2. 用一个短视频生成，再试一个长视频；核对数据、引用和主题取舍。
3. 在结果页切换绑定版式，确认未截断、页码合理；切换版式不重新调用模型。
4. 下载图片、复制文本和导出 Markdown，检查实际文件。
5. 满意后用风格旁的导出图标分享 JSON；包不携带 API Key、Cookie、全局规则或生成历史。

导入失败会保留原有风格与版式。若 AI 输出错误，优先把错误原文和这份规则交给 AI 修正，不要为导入而删除必要的正文或来源信息。
