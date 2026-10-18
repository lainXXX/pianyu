"""Built-in output templates beyond the core three (design 4.12).

Prompts as approved by the user in experiments/gallery/index.html. Points prompts end with
vtx.knowledge.REPLY_SPEC so every one of them feeds the same layouts."""
from vtx.knowledge import REPLY_SPEC

EXTRA = [
    {"id": "steps", "name": "操作步骤", "kind": "points", "layouts": ['steps', 'A', 'brief'],
     "prompt": """# 操作步骤
<video_title>{title}</video_title>

- 按实际操作顺序组织，最多 {max_points} 步；闲聊、试吃、拍摄动作不算步骤。没有主要流程依据时，points 留空。
- title 写目标；conclusion 写结果与必要前提；heading 用动作命名。
- body 简述做法，保留来源中的用量、时间、温度与参数；text 完整说明动作、结果及有依据的原因，不重复表述。
- detail 只补必要提醒、错误或替代做法，不重复正文；关键步骤设 key=true，最多 3 项。
- 不把现场状态或吐槽改成操作条件。quotes 只选完整、清楚的单人提醒，最多 3 句，可为空。

""" + REPLY_SPEC},
    {"id": "cards", "name": "学习卡片", "kind": "points", "layouts": ['cards', 'A', 'brief'],
     "prompt": """# 学习卡片
<video_title>{title}</video_title>

- 按讲解顺序整理核心概念，最多 {max_points} 项，不凑数量。
- title 写主题；conclusion 写最值得记住的结论；heading 用概念名或具体问题。
- body 写简短定义或答案；text 解释概念、原有例子及概念间关系。
- detail 补充来源中的公式、易混淆点或例子，不重复正文；核心概念设 key=true，最多 3 项。
- quotes 只选有助理解的完整原话，最多 3 句，可为空。

""" + REPLY_SPEC},
    {"id": "review", "name": "测评对比", "kind": "points", "layouts": ['compare', 'A', 'brief'],
     "prompt": """# 测评对比
<video_title>{title}</video_title>

- 按视频顺序整理产品、方案或评测维度，最多 {max_points} 项，不凑数量。
- title 写测评对象；conclusion 写来源支持的结论与适用人群；heading 写对象或维度。
- body 写独立可读的图片摘要：数据须带测试项目与关键设置；放不下时少列数据，不能丢条件。
- text 说明过程与理由，明确每组数据的测试名称、对象和模式，不串用不同测试的数据。
- detail 只列来源提及的优缺点；重点讨论或推荐项设 key=true，最多 3 项。
- quotes 只选完整评价，最多 3 句，可为空。

""" + REPLY_SPEC},
    {"id": "debate", "name": "观点论证", "kind": "points", "layouts": ['argue', 'A'],
     "prompt": """# 观点论证
<video_title>{title}</video_title>

- 按论证顺序整理，最多 {max_points} 个论点，不凑数量。
- title 写问题；conclusion 写核心立场，多人讨论则写主要分歧或共识。
- heading 写明确判断，多人讨论时标明观点归属；body 写主要理由。
- text 保留推理、前提及来源中的事实、例子和类比；detail 补充论据或讲述者提出的反方观点与回应。
- 核心论点设 key=true，最多 3 项；quotes 只选完整且有代表性的原话，最多 4 句，可为空。
- 不替讲述者补论据或加评价。

""" + REPLY_SPEC},
    {"id": "news", "name": "资讯速览", "kind": "points", "layouts": ['news', 'A', 'brief'],
     "prompt": """# 资讯速览
<video_title>{title}</video_title>

- 按视频顺序每件事一项，最多 {max_points} 项，不凑数量。
- title 写本期主题；conclusion 写最重要的信息；heading 写具体事件。
- body 简述事件，保留关键时间、金额和比例；text 说明背景、影响与讲述者解读，区分事实和判断。
- detail 只补来源中的关键数据或后续关注点；重点事件设 key=true，最多 3 项。
- quotes 只选完整的关键判断，最多 3 句，可为空。

""" + REPLY_SPEC},
    {"id": "notes", "name": "学习笔记", "kind": "free", "layouts": ['book', 'magazine', 'print'],
     "prompt": """# 学习笔记
<video_title>{title}</video_title>

用 Markdown 输出：
- 一个准确的一级标题，按主题分节，保留例子、数字、条件和推理。
- 同一内容只讲一次，不另设要点复述、原话摘录、延伸问题或机械总结。
- 多人对话按主题归纳；角色不明时不猜身份或对话关系。
- 只保留必要的人物姓名，旁支人物可用关系或职务表达。

<transcript>
{text}
</transcript>"""},
    {"id": "qa", "name": "问答对", "kind": "free", "layouts": ['book', 'print'],
     "prompt": """# 问答对
<video_title>{title}</video_title>

用 Markdown 按内容顺序输出，覆盖主要信息，不凑数量：
**Q：具体问题**
A：有来源依据的答案。

问题应适合复习或查询，避免“视频讲了什么”等空泛提问。

<transcript>
{text}
</transcript>"""},
    {"id": "actions", "name": "行动清单", "kind": "free", "layouts": ['book', 'print'],
     "prompt": """# 行动清单
<video_title>{title}</video_title>

- 只提取讲述者明确建议、推荐或演示的做法，按强调程度排序，最多 12 条，不凑数量。
- 用 Markdown 任务列表：`- [ ] 具体动作`；仅在来源解释时补充原因。
- 明确提醒不要做的事列入 `## 避免`。
- 无可执行建议时只写：这个视频没有具体的行动建议。

<transcript>
{text}
</transcript>"""},
    {"id": "article", "name": "公众号文章", "kind": "free", "layouts": ['magazine', 'book'],
     "prompt": """# 公众号文章
<video_title>{title}</video_title>

用 Markdown 改写为适合公众号或博客的文章：
- 标题准确，开头直接进入主题。
- 按内容逻辑分节，保留核心观点、例子、数据和讲述者有特色的表达。
- 将口语整理成通顺书面语，不添加事实或自己的观点。
- 页面已附来源，正文不重复来源声明或自我介绍。

<transcript>
{text}
</transcript>"""},
    {"id": "xhs", "name": "小红书笔记", "kind": "free", "layouts": ['book'],
     "prompt": """# 小红书笔记
<video_title>{title}</video_title>

- 标题简短自然，不夸大；正文适合快速阅读，按内容分点，可少量使用 emoji。
- 保留有依据的数字、做法与结论，不虚构亲身经历、效果或情绪。
- 篇幅随内容，不凑字数；不强加痛点、互动提问或套话结尾。
- 末尾只留少量相关话题标签；页面已附来源，不重复声明。

<transcript>
{text}
</transcript>"""},
    {"id": "chapters", "name": "简介与分段标题", "kind": "free", "layouts": ['book'],
     "prompt": """# 简介与分段标题
<video_title>{title}</video_title>

用 Markdown 输出：
- `## 简介`：简述主题及主要内容，不夸大。
- `## 分段标题`：按内容自然分段，每行“第 N 段：小标题 —— 内容概述”；不编造时间点，不凑段数。
- `## 备选标题`：直白型、提问型、结论型各一个，均须准确。

<transcript>
{text}
</transcript>"""},
]
