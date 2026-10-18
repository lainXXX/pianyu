from vtx.reading import plain_text, render_markdown


def test_reader_renders_article_and_copies_readable_text():
    md = "# 标题\n\n**重点**与 *强调*。\n\n1. 第一步\n2. 第二步\n\n> 引用\n\n| 项目 | 数值 |\n| --- | --- |\n| 版本 | V4.1 |\n\n```py\nx = 1\n```"
    html = render_markdown(md)
    assert "<h1>标题</h1>" in html and "<table>" in html and "<strong>重点</strong>" in html
    text = plain_text(md)
    assert "# 标题" not in text and "**重点**" not in text
    assert "1. 第一步" in text and "2. 第二步" in text
    assert "版本\tV4.1" in text and "x = 1" in text


def test_reader_never_executes_html_or_dangerous_links():
    html = render_markdown('<script>alert(1)</script>\n\n[运行](javascript:alert(1))\n\n![图片](data:text/html,test)')
    assert "<script>" not in html and 'href="javascript:' not in html and 'src="data:' not in html
    assert "&lt;script&gt;" in html
