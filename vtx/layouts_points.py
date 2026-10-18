"""Built-in point layouts beyond A / B (design 4.12). Jinja2 sources; variables as in vtx.poster_templates."""

_HEAD = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{{ title }}</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
footer{font-size:16px;line-height:1.5;color:#a39a8c}
footer div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
"""
_FOOTER = '<footer>{% for line in footer_lines %}<div>{{ line }}</div>{% endfor %}</footer>'

BRIEF = _HEAD + """
.poster{width:1080px;background:#fff;color:#2a2622;padding:80px 90px 50px;font-size:30px;line-height:1.7}
h1{font-size:48px;line-height:1.35}
.tl{margin:30px 0 40px;font-size:36px;font-weight:800;color:#1a5fb4}
.row{display:grid;grid-template-columns:44px 1fr;gap:16px;margin:0 0 22px}
.row i{font-style:normal;width:44px;height:44px;border-radius:10px;background:#e8f0fb;color:#1a5fb4;font-weight:800;
  display:flex;align-items:center;justify-content:center;font-size:24px;margin-top:6px}
.row.key i{background:#1a5fb4;color:#fff}
footer{margin-top:40px;border-top:1px solid #eee;padding-top:18px}
</style></head><body><div class="poster">
<h1>{{ title }}</h1><div class="tl">{{ conclusion }}</div>
{% for p in points %}<div class="row{{ ' key' if p.key }}"><i>{{ loop.index }}</i><div><b>{{ p.heading }}</b>：{{ p.body }}</div></div>
{% endfor %}""" + _FOOTER + "</div></body></html>"

COVER_EDITORIAL = _HEAD + """
.poster{width:1080px;height:1440px;overflow:hidden;position:relative;background:#f4efe6;color:#1d1a16;padding:96px}
.top{display:flex;justify-content:space-between;font-size:34px;letter-spacing:.15em;border-bottom:4px solid #1d1a16;padding-bottom:22px}
.top span:first-child{font-weight:800;color:#c0392b}
h1{font-family:"Noto Serif SC","Source Han Serif SC","SimSun",serif;font-weight:900;font-size:108px;line-height:1.18;margin:60px 0 24px;
  display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.sub{font-size:44px;color:#6b6259;margin-bottom:46px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
ol{list-style:none;font-size:46px;line-height:1.45}
li{padding:20px 0;border-top:2px solid #d8cfc0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
li b{color:#c0392b;font-family:Georgia,serif;margin-right:34px}
footer{position:absolute;left:96px;right:96px;bottom:60px;font-size:26px;color:#8f857a}
</style></head><body><div class="poster">
<div class="top"><span>知识卡</span><span>{{ kicker }}</span></div>
<h1>{{ title }}</h1><div class="sub">{{ conclusion }}</div>
<ol>{% for p in points[:4] %}<li><b>{{ "%02d"|format(loop.index) }}</b>{{ p.heading }}</li>{% endfor %}</ol>
""" + _FOOTER + "</div></body></html>"

COVER_QA = _HEAD + """
.poster{width:1080px;height:1440px;overflow:hidden;position:relative;background:#16233a;color:#fff;padding:90px 96px}
.q{font-family:Georgia,serif;font-size:180px;line-height:1;color:#f5c542;font-weight:700}
h1{font-size:88px;line-height:1.25;margin:20px 0 50px;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.a{font-size:48px;line-height:1.45;background:#f5c542;color:#16233a;border-radius:28px;padding:34px 44px;font-weight:800}
.a div{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.a span{font-family:Georgia,serif;margin-right:24px}
.list{margin-top:50px;font-size:44px;line-height:1.9;color:#c9d4e6}
.list div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.list div::before{content:"▸ ";color:#f5c542}
footer{position:absolute;left:96px;right:96px;bottom:60px;font-size:26px;color:#6f7f99}
</style></head><body><div class="poster">
<div class="q">Q</div><h1>{{ title }}</h1>
<div class="a"><div><span>A</span>{{ conclusion }}</div></div>
<div class="list">{% for p in points[:3] %}<div>{{ p.heading }}</div>{% endfor %}</div>
""" + _FOOTER + "</div></body></html>"

COVER_NOTE = _HEAD + """
.poster{width:1080px;height:1440px;overflow:hidden;position:relative;background:#e9e4da;padding:110px 90px}
.note{position:relative;height:100%;background-color:#fffdf7;background-image:linear-gradient(#e7eef7 2px,transparent 2px),
  linear-gradient(90deg,#e7eef7 2px,transparent 2px);background-size:60px 60px;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.12);
  padding:110px 80px;transform:rotate(-1.2deg)}
.tape{position:absolute;top:-30px;left:50%;width:280px;height:70px;margin-left:-140px;background:rgba(244,190,90,.75);transform:rotate(2deg)}
.date{font-size:32px;color:#8a8273;letter-spacing:.1em}
h1{font-size:104px;line-height:1.3;margin:30px 0 60px;color:#2b2a28;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
h1 span{background:linear-gradient(transparent 60%,#ffb4a2 0)}
ul{list-style:none;font-size:54px;line-height:1.9;color:#3a3833}
li{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
li::before{content:"✓";color:#e2725b;font-weight:900;margin-right:28px}
footer{position:absolute;left:80px;right:80px;bottom:60px;font-size:26px;color:#9a9282}
</style></head><body><div class="poster"><div class="note"><div class="tape"></div>
<div class="date">{{ kicker }}</div><h1><span>{{ title }}</span></h1>
<ul>{% for p in points[:4] %}<li>{{ p.heading }}</li>{% endfor %}</ul>
""" + _FOOTER + "</div></div></body></html>"

STEPS = _HEAD + """
.poster{width:1080px;background:#f6f8f7;color:#2a2622;padding:70px 70px 40px;font-size:29px;line-height:1.65}
h1{font-size:52px;line-height:1.3}
.need{margin:26px 0 40px;background:#fff;border-radius:16px;padding:22px 28px;color:#3d5a52;border:2px solid #dfe9e4}
.st{display:grid;grid-template-columns:96px 1fr;gap:0 26px;position:relative;padding-bottom:34px}
.st::before{content:"";position:absolute;left:47px;top:96px;bottom:0;width:3px;background:#cfe0d8}
.st:last-of-type::before{display:none}
.n{width:96px;height:96px;border-radius:50%;background:#3d7a66;color:#fff;font-size:42px;font-weight:800;display:flex;align-items:center;justify-content:center}
.st.key .n{background:#c7681e}
h2{font-size:36px;margin:14px 0 6px}
.details{margin-top:12px;padding-left:1.2em;font-size:25px;color:#63746d}
.details li{margin-top:5px}
blockquote{margin-top:10px;font-size:25px;color:#3d5a52}
footer{border-top:2px solid #dfe9e4;padding-top:20px;margin-top:20px}
</style></head><body><div class="poster">
<h1>{{ title }}</h1><div class="need">{{ conclusion }}</div>
{% for p in points %}<div class="st{{ ' key' if p.key }}"><div class="n">{{ loop.index }}</div><div><h2>{{ p.heading }}</h2>{{ p.body }}
{% if p.detail %}<ul class="details">{% for d in p.detail %}<li>{{ d }}</li>{% endfor %}</ul>{% endif %}
{% for q in p.quotes %}<blockquote>“{{ q.quote }}”</blockquote>{% endfor %}</div></div>
{% endfor %}""" + _FOOTER + "</div></body></html>"

CARDS = _HEAD + """
.poster{width:1080px;background:#eef1f7;color:#2a2622;padding:60px 56px 36px;font-size:24px;line-height:1.6}
h1{font-size:48px;line-height:1.3}
.one{margin:22px 0 34px;font-size:28px;color:#33415c}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.c{background:#fff;border-radius:18px;padding:24px 26px;box-shadow:0 2px 0 #d7ddea}
.c.key{grid-column:span 2;border:3px solid #4f6bd6}
.q{font-size:30px;font-weight:800;color:#33415c}
.a{margin-top:8px;color:#4a4f5c}
.ex{margin-top:10px;font-size:21px;color:#6b7180;background:#f4f6fb;border-radius:10px;padding:8px 12px}
footer{margin-top:30px}
</style></head><body><div class="poster">
<h1>{{ title }}</h1><div class="one">一句话记住：{{ conclusion }}</div>
<div class="grid">{% for p in points %}<div class="c{{ ' key' if p.key }}"><div class="q">{{ p.heading }}</div><div class="a">{{ p.body }}</div>
{% for d in p.detail %}<div class="ex">{{ d }}</div>{% endfor %}</div>{% endfor %}</div>
""" + _FOOTER + "</div></body></html>"

COMPARE = _HEAD + """
.poster{width:1600px;background:#fbfbfa;color:#2a2622;padding:56px 60px 36px;font-size:22px;line-height:1.55}
h1{font-size:44px}
.verdict{margin:20px 0 30px;background:#20303c;color:#fff;border-radius:14px;padding:20px 28px;font-size:27px}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 1px 0 #e3e3e3}
th{background:#eef2f5;text-align:left;padding:16px 20px;font-size:23px}
td{padding:16px 20px;border-top:1px solid #edf0f2;vertical-align:top}
td.good{color:#1e6b34}td.bad{color:#b3261e}
tr.key td:first-child{font-weight:800;border-left:8px solid #c98a2b}
footer{margin-top:24px}
</style></head><body><div class="poster">
<h1>{{ title }}</h1><div class="verdict">结论：{{ conclusion }}</div>
<table><tr><th style="width:20%">项目</th><th style="width:32%">结论</th><th>优点</th><th>缺点</th></tr>
{% for p in points %}
<tr class="{{ 'key' if p.key }}"><td>{{ p.heading }}</td><td>{{ p.body }}</td>
<td class="good">{% for d in p.detail if not d.startswith("缺") %}<div>{{ d|replace("优点：", "") }}</div>{% endfor %}</td>
<td class="bad">{% for d in p.detail if d.startswith("缺") %}<div>{{ d|replace("缺点：", "") }}</div>{% endfor %}</td></tr>{% endfor %}
</table>""" + _FOOTER + "</div></body></html>"

ARGUE = _HEAD + """
.poster{width:1080px;background:#f7f4ef;color:#2a2622;padding:70px 70px 40px;font-size:27px;line-height:1.65}
.issue{font-size:24px;color:#8a7f6e;letter-spacing:.1em}
h1{font-size:50px;line-height:1.3;margin:8px 0 26px}
.claim{background:#3b2f2a;color:#fff;border-radius:18px;padding:28px 32px;font-size:34px;font-weight:700}
.arg{margin:26px 0 0 40px;padding:20px 26px;background:#fff;border-radius:14px;border-left:8px solid #b3683a;position:relative}
.arg::before{content:"";position:absolute;left:-40px;top:34px;width:40px;height:3px;background:#d9c6b4}
.arg.key{border-left-color:#7a2e12}
h2{font-size:30px;margin:0 0 6px}
.why{color:#5a4f47}
.counter{margin-top:10px;font-size:23px;color:#6b5530;background:#fbf3e4;border-radius:8px;padding:8px 12px}
blockquote{margin:12px 0 0;font-size:25px;color:#7a4a2a}
footer{margin-top:34px;border-top:2px solid #e3d9c8;padding-top:18px}
</style></head><body><div class="poster">
<div class="issue">讨论的问题</div><h1>{{ title }}</h1>
<div class="claim">核心立场：{{ conclusion }}</div>
{% for p in points %}<div class="arg{{ ' key' if p.key }}"><h2>{{ "①②③④⑤⑥⑦⑧⑨⑩"[loop.index0] }} {{ p.heading }}</h2><div class="why">{{ p.body }}</div>
{% for d in p.detail %}<div class="counter">{{ d }}</div>{% endfor %}
{% for q in p.quotes %}<blockquote>“{{ q.quote }}”</blockquote>{% endfor %}</div>
{% endfor %}""" + _FOOTER + "</div></body></html>"

NEWS = _HEAD + """
.poster{width:1080px;background:#fff;color:#111;padding:60px 64px 36px;font-size:26px;line-height:1.6}
.mast{display:flex;justify-content:space-between;align-items:baseline;border-bottom:6px solid #111;padding-bottom:12px;gap:30px}
.mast b{font-size:52px;letter-spacing:.03em;line-height:1.25}
.mast span{font-size:22px;color:#666;white-space:nowrap}
.lead{font-size:30px;font-weight:700;margin:26px 0 20px}
.it{display:grid;grid-template-columns:70px 1fr;gap:18px;padding:22px 0;border-bottom:1px solid #e5e5e5}
.no{font-size:46px;font-weight:900;color:#d0482f}
h2{font-size:31px;margin:0 0 4px}
.fact{color:#333}
.num{display:inline-block;margin:8px 8px 0 0;font-size:22px;background:#fdecea;color:#b3261e;border-radius:6px;padding:2px 10px}
blockquote{margin-top:8px;font-size:23px;color:#5f6368;border-left:4px solid #ddd;padding-left:12px}
footer{margin-top:24px}
</style></head><body><div class="poster">
<div class="mast"><b>{{ title }}</b><span>{{ kicker }}</span></div>
<div class="lead">{{ conclusion }}</div>
{% for p in points %}<div class="it"><div class="no">{{ loop.index }}</div><div><h2>{{ p.heading }}</h2><div class="fact">{{ p.body }}</div>
{% for d in p.detail %}<span class="num">{{ d }}</span>{% endfor %}
{% for q in p.quotes %}<blockquote>“{{ q.quote }}”</blockquote>{% endfor %}</div></div>
{% endfor %}""" + _FOOTER + "</div></body></html>"

POINTS = [
    {"id": "brief", "name": "一页速览", "html": BRIEF, "width": 1080, "height_mode": "auto"},
    {"id": "cover-editorial", "name": "小红书封面 · 杂志编辑", "html": COVER_EDITORIAL, "width": 1080, "height_mode": "fixed:1440"},
    {"id": "cover-qa", "name": "小红书封面 · 问答大字", "html": COVER_QA, "width": 1080, "height_mode": "fixed:1440"},
    {"id": "cover-note", "name": "小红书封面 · 手账笔记", "html": COVER_NOTE, "width": 1080, "height_mode": "fixed:1440"},
    {"id": "steps", "name": "步骤时间线", "html": STEPS, "width": 1080, "height_mode": "auto"},
    {"id": "cards", "name": "卡片网格", "html": CARDS, "width": 1080, "height_mode": "auto"},
    {"id": "compare", "name": "对比表", "html": COMPARE, "width": 1600, "height_mode": "auto"},
    {"id": "argue", "name": "论证结构", "html": ARGUE, "width": 1080, "height_mode": "auto"},
    {"id": "news", "name": "资讯版", "html": NEWS, "width": 1080, "height_mode": "auto"},
]
