"""T29 acceptance 1: poster HTML is self-contained, escaped, timestamp/model/date free, one-line footer."""
import re

import pytest

from vtx.poster import footer_text, render_html

DATA = {
    "title": "为什么人类需要不断吃盐，<而>动物们却不用？",
    "conclusion": "所有生物都离不开电解质 & 这是生命的底层代码。",
    "points": [
        {"heading": "动物也缺盐", "body": "长颈鹿舔钢管，羊追着人舔。", "key": False, "detail": [], "quotes": []},
        {"heading": "骆驼喂盐不喂水", "body": "出远门前直接喂盐。", "key": True,
         "detail": ["做法：喂盐让它大量喝水", "原因：直接喂水越喂越缺水"],
         "quotes": [{"quote": "喝一份水就得喝一份的盐。", "source": "x"}]},
        {"heading": "钠的作用", "body": "神经信号、肌肉收缩、渗透压。", "key": True, "detail": ["缺钠会四肢无力"],
         "quotes": [{"quote": "<script>alert(1)</script>", "source": "x"}]},
        {"heading": "生命起源于海洋", "body": "电解质是底层代码。", "key": False, "detail": [], "quotes": []},
    ],
}
META = {"platform": "bilibili", "video_id": "BV1wFZ8YBEt4", "part": 1,
        "title": "为什么人类需要不断吃盐，而动物们却不用？", "author": "示例UP",
        "duration": 197.0, "engine": "dashscope/paraformer-v2", "llm_model": "deepseek-chat"}


@pytest.fixture(params=["A", "B"])
def html(request):
    return render_html(DATA, META, layout=request.param)


def test_self_contained(html):
    assert "<script" not in html.lower()
    assert not re.search(r"""(src|href)\s*=\s*["']?(https?:)?//""", html, re.I)
    assert "@import" not in html and "url(" not in html


def test_escaped(html):
    assert "&lt;而&gt;" in html and "&amp; 这是" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_no_timestamps_model_or_dates(html):
    visible = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    assert not re.search(r"\b\d{1,2}:\d{2}\b", visible)
    assert "paraformer" not in visible and "deepseek" not in visible.lower()
    assert not re.search(r"20\d\d[-年./]\d", visible)


def test_footer_short_lines_with_link(html):
    m = re.search(r'<footer[^>]*>(.*?)</footer>', html, re.S)
    assert m
    rows = [re.sub(r"<[^>]+>", "", r) for r in re.findall(r"<div>(.*?)</div>", m.group(1))]
    assert len(rows) == 3 and all(len(r) <= 90 for r in rows)
    assert "B站" in rows[0] and "@示例UP" in rows[0] and "版权" in rows[0] and "AI" in rows[0]
    assert rows[1] == "https://www.bilibili.com/video/BV1wFZ8YBEt4"
    assert rows[2] == "片语 · https://github.com/lainXXX/pianyu"


def test_custom_footer_used():
    html = render_html(DATA, META, "A", footer="我的页脚 & 第二段")
    assert "<div>我的页脚 &amp; 第二段</div>" in html


def test_content_present(html):
    for s in ["所有生物都离不开电解质", "骆驼喂盐不喂水", "做法：喂盐让它大量喝水", "喝一份水就得喝一份的盐。", "生命起源于海洋"]:
        assert s.replace("&", "&amp;") in html


def test_key_points_marked(html):
    assert html.count('class="pt key"') == 2


def test_b_columns_balanced_by_content():
    from vtx.poster import balanced_split, _weight

    short = {"heading": "h", "body": "短。", "key": False, "detail": []}
    long = {"heading": "h", "body": "长" * 60, "key": True, "detail": ["细节" * 15] * 4}
    pts = [short, short, short, long, long, long]
    k = balanced_split(pts)
    left, right = sum(_weight(p) for p in pts[:k]), sum(_weight(p) for p in pts[k:])
    assert k > 3 and abs(left - right) < max(left, right) * 0.5
    assert balanced_split([short]) == 1 and balanced_split([]) == 0


def test_long_title_never_clips_disclaimer():
    t = footer_text({"platform": "bilibili", "title": "(强推)李宏毅2021/2022春机器学习课程 P3 第一节 2021 - (上) - 机器学习基本概念简介"})
    first = t.split("\n")[0]
    assert first.endswith("AI 整理可能有误") and "…》" in first and len(first) <= 90


def test_footer_text_without_uploader():
    t = footer_text({"platform": "douyin", "title": "标题", "video_id": "7"})
    assert t.startswith("来源：抖音 《标题》") and "@" not in t and "版权" in t
    assert "https://www.douyin.com/video/7" in t.splitlines()
    assert t.endswith("https://github.com/lainXXX/pianyu")


def test_duration_shown_in_header_only_as_minutes():
    html = render_html(DATA, META, layout="A")
    assert "3 分 17 秒" in html
