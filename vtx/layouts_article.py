"""Built-in article layouts (design 4.12): 书页 / 杂志 / 夜读 / 打印. Jinja2 sources, see vtx.article."""

_HEAD = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>{{ title }}</title><style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:BG}
"""
_FOOT = """.content>p{margin-bottom:1em}
.content>h3{padding-top:.55em;line-height:1.55}
.content>p.cont{text-indent:0}
</style></head><body><article class="page">
{% if first %}<h1>{{ title }}</h1><div class="kicker">{{ kicker }}</div>{% else %}<div class="running">{{ title }}</div>{% endif %}
<main class="content{% if first %} first{% endif %}">{% for b in blocks %}{{ b.html }}{% endfor %}</main>
{% if last %}<footer>{% for line in footer_lines %}<div>{{ line }}</div>{% endfor %}</footer>{% endif %}
{% if pages > 1 %}<div class="pg">{{ page }} / {{ pages }}</div>{% endif %}
</article></body></html>"""

BOOK = _HEAD.replace("BG", "#fbf8f1") + """
.page{width:1080px;padding:96px 110px 70px;color:#2a2622;font:34px/1.95 "Noto Serif SC","Source Han Serif SC","Songti SC","SimSun",serif}
h1{font-size:56px;line-height:1.35;font-weight:700}
.kicker{font:24px/1.5 "Microsoft YaHei",sans-serif;color:#8a7f6e;margin:18px 0 40px;padding-bottom:30px;border-bottom:2px solid #e6dcc8}
.running{font:22px "Microsoft YaHei",sans-serif;color:#b0a593;margin-bottom:36px;padding-bottom:16px;border-bottom:1px solid #e6dcc8}
.content>*{margin-bottom:30px}
h2{font-size:40px;line-height:1.4;color:#5a3e1b;padding-top:24px}
h2::before{content:"";display:inline-block;width:10px;height:36px;background:#c9a227;margin-right:18px;vertical-align:-4px}
h3{font-size:36px;color:#7a5a2b;padding-top:10px}
p{text-indent:2em}
b{background:linear-gradient(transparent 62%,#f6e2a6 0)}
ul,ol{padding-left:1.4em}
ul.tasks{list-style:none;padding-left:0}
.box{display:inline-block;width:1.4em;color:#8a6d3b}
blockquote{border-left:8px solid #c9a227;background:#f4ecd9;padding:16px 26px;border-radius:0 10px 10px 0}
hr{border:0;border-top:2px dashed #e6dcc8}
code{font-family:Consolas,monospace;font-size:.85em;background:#efe7d6;padding:0 .3em;border-radius:4px}
footer{font:22px/1.6 "Microsoft YaHei",sans-serif;color:#a39a8c;border-top:2px solid #e6dcc8;margin-top:40px;padding-top:24px}
.pg{font:20px "Microsoft YaHei",sans-serif;color:#b0a593;text-align:center;margin-top:24px}
""" + _FOOT

MAGAZINE = _HEAD.replace("BG", "#ffffff") + """
.page{width:1080px;padding:80px 90px 60px;color:#222;font:31px/1.85 "Microsoft YaHei","PingFang SC",sans-serif}
h1{font-size:62px;line-height:1.2;font-weight:800}
.kicker{font-size:22px;color:#d0482f;letter-spacing:.12em;font-weight:700;margin:20px 0 44px}
.running{font-size:20px;color:#999;letter-spacing:.1em;margin-bottom:34px}
.content>*{margin-bottom:26px}
.content.first>p:first-child::first-letter{float:left;font-size:120px;line-height:.9;margin:8px 16px 0 0;color:#d0482f;font-weight:800}
h2{font-size:36px;display:inline-block;border-bottom:5px solid #222;padding:20px 0 8px}
h3{font-size:32px;color:#d0482f}
b{color:#d0482f}
ul,ol{padding-left:1.3em}
ul.tasks{list-style:none;padding-left:0}
.box{display:inline-block;width:1.4em;color:#d0482f}
blockquote{border-left:8px solid #d0482f;padding:6px 0 6px 30px;font-size:36px;font-weight:700;line-height:1.5}
hr{border:0;border-top:1px solid #ddd}
code{font-family:Consolas,monospace;font-size:.85em;background:#f3f3f3;padding:0 .3em}
footer{font-size:20px;line-height:1.6;color:#999;border-top:1px solid #ddd;margin-top:40px;padding-top:20px}
.pg{font-size:20px;color:#bbb;text-align:right;margin-top:20px}
""" + _FOOT

NIGHT = _HEAD.replace("BG", "#1e2226") + """
.page{width:1080px;padding:96px 100px 70px;color:#d8d4cc;font:33px/1.95 "Noto Serif SC","Source Han Serif SC","SimSun",serif}
h1{font-size:54px;line-height:1.35;color:#f1ead9}
.kicker{font:23px "Microsoft YaHei",sans-serif;color:#8b9198;margin:18px 0 44px}
.running{font:21px "Microsoft YaHei",sans-serif;color:#6f757c;margin-bottom:36px}
.content>*{margin-bottom:28px}
h2{font-size:38px;color:#e7c770;padding-top:24px}
h3{font-size:34px;color:#cdb57a}
b{color:#f1ead9;border-bottom:3px solid #e7c770}
ul,ol{padding-left:1.4em}
ul.tasks{list-style:none;padding-left:0}
.box{display:inline-block;width:1.4em;color:#e7c770}
blockquote{border-left:6px solid #e7c770;background:#272c31;padding:16px 26px}
hr{border:0;border-top:1px solid #383e44}
code{font-family:Consolas,monospace;font-size:.85em;background:#2d3338;padding:0 .3em}
footer{font:21px/1.6 "Microsoft YaHei",sans-serif;color:#6f757c;border-top:1px solid #383e44;margin-top:40px;padding-top:22px}
.pg{font:20px "Microsoft YaHei",sans-serif;color:#6f757c;text-align:center;margin-top:22px}
""" + _FOOT

PRINT = _HEAD.replace("BG", "#ffffff") + """
.page{width:794px;padding:64px 72px;color:#000;font:19px/1.8 "SimSun","Songti SC",serif}
h1{font-size:30px;text-align:center;font-family:"SimHei","Microsoft YaHei",sans-serif}
.kicker{text-align:center;font-size:14px;color:#444;margin:8px 0 26px}
.running{font-size:13px;color:#666;border-bottom:1px solid #000;padding-bottom:6px;margin-bottom:20px}
.content>*{margin-bottom:12px}
h2{font-size:22px;font-family:"SimHei","Microsoft YaHei",sans-serif;padding-top:10px}
h3{font-size:20px;font-family:"SimHei","Microsoft YaHei",sans-serif}
p{text-indent:2em}
b{font-family:"SimHei","Microsoft YaHei",sans-serif}
ul,ol{padding-left:1.6em}
ul.tasks{list-style:none;padding-left:0}
.box{display:inline-block;width:1.4em}
blockquote{border-left:3px solid #000;padding:2px 14px}
hr{border:0;border-top:1px dashed #000}
footer{font-size:12px;color:#444;border-top:1px solid #000;margin-top:24px;padding-top:8px}
.pg{font-size:12px;text-align:center;margin-top:10px}
""" + _FOOT

# id, name, html, width, height_mode ("fixed:<page height>" = page size for pagination)
ARTICLE = [
    {"id": "book", "name": "书页", "html": BOOK, "width": 1080, "height_mode": "fixed:2400"},
    {"id": "magazine", "name": "杂志", "html": MAGAZINE, "width": 1080, "height_mode": "fixed:2400"},
    {"id": "night", "name": "夜读", "html": NIGHT, "width": 1080, "height_mode": "fixed:2400"},
    {"id": "print", "name": "打印", "html": PRINT, "width": 794, "height_mode": "fixed:1123"},
]
