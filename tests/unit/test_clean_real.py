"""T8 acceptance 3: real sentences from the sample transcripts, expected output annotated by hand.

Annotation by Claude (2026-10-14) following design doc 4.6: delete only certain fillers and
stutters; 哎/诶, 嗯 inside a clause, expressive repeats (最最最最) and anything ambiguous stay.
The user spot-checks real outputs in T12.
"""
import pytest

from vtx.clean import clean, protected_spans

REAL = [
    # (sample, original, expected)
    ("bili-3", "在W 0这个位置的呃这个ersurface的前线斜率，也就是这一条蓝色的虚线它的斜率。",
     "在W 0这个位置的这个ersurface的前线斜率，也就是这一条蓝色的虚线它的斜率。"),
    ("bili-2", "呃，2006年啊某地就发现了121个头骨骷髅，后被鉴定为人头骨啊。",
     "2006年啊某地就发现了121个头骨骷髅，后被鉴定为人头骨啊。"),
    ("bili-3", "呃，loss这个函数是你自己定义的。", "loss这个函数是你自己定义的。"),
    ("bili-3", "第一种状况是呃你失去耐心的。", "第一种状况是你失去耐心的。"),
    ("bili-3", "举例来说，呃，我们这边先做一个最初步的猜测，这个F长什么样子呢？",
     "举例来说，我们这边先做一个最初步的猜测，这个F长什么样子呢？"),
    ("bili-3", "哎，这个世界不是外面是有一个黑暗大陆的。", "哎，这个世界不是外面是有一个黑暗大陆的。"),
    ("bili-2", "嗯，因为这里的问题是啊，如果标准不明确，有没有可能有一天你也成为坏人呢？",
     "因为这里的问题是啊，如果标准不明确，有没有可能有一天你也成为坏人呢？"),
    ("dy-2", "哎，饭来了，吃饭啊。", "哎，饭来了，吃饭啊。"),
    ("bili-1", "咱们运动大量出汗后，再补充水分的同时也要补充电解质，就是这个道理。",
     "咱们运动大量出汗后，再补充水分的同时也要补充电解质，就是这个道理。"),
    ("bili-3", "那你可能也知道说啊，机器学习就是跟今天很热门的AI好像有那么一点关联。",
     "那你可能也知道说啊，机器学习就是跟今天很热门的AI好像有那么一点关联。"),
    ("bili-3", "你可能设说嗯我的上限就是设定100万次，所以我参数更新100万次以后我就不再更新了。",
     "你可能设说嗯我的上限就是设定100万次，所以我参数更新100万次以后我就不再更新了。"),
    ("bili-2", "呃，大家好啊，今天我们想讲一个非常严肃的话题，就是侮辱骨灰是不是犯罪。",
     "大家好啊，今天我们想讲一个非常严肃的话题，就是侮辱骨灰是不是犯罪。"),
    ("bili-3", "好，加起来然后取一个平均。", "好，加起来然后取一个平均。"),
    ("bili-3", "这个下标J代表是几天前，然后这个J等于1到7，也就是从一天前两天前一直考虑到7天前。",
     "这个下标J代表是几天前，然后这个J等于1到7，也就是从一天前两天前一直考虑到7天前。"),
    ("dy-2", "然后这个是我们的最最最最招牌大虾滑蛋河粉。", "然后这个是我们的最最最最招牌大虾滑蛋河粉。"),
    ("dy-1", "而洪龙和黄伟现在面临的最大困难就是他们完全没有办法做到十点就睡着。",
     "而洪龙和黄伟现在面临的最大困难就是他们完全没有办法做到十点就睡着。"),
    ("dy-3", "因此，弗洛伊顿说，文明必须限制性、压抑性，没有一定程度的性压抑，就没有家庭法律合作。",
     "因此，弗洛伊顿说，文明必须限制性、压抑性，没有一定程度的性压抑，就没有家庭法律合作。"),
    ("bili-2", "那那也许是构成这个侮辱尸体事故骨灰罪的。", "那也许是构成这个侮辱尸体事故骨灰罪的。"),
    ("bili-3", "那不知道为什么他还考虑前三天前三天是0.12，然后前六天是0.3，前七天是0.18。",
     "那不知道为什么他还考虑前三天前三天是0.12，然后前六天是0.3，前七天是0.18。"),
    ("bili-3", "也许我们觉得说呃这个呃呃今天的点阅次数", "也许我们觉得说这个今天的点阅次数"),
]


@pytest.mark.parametrize("sample,original,expected", REAL)
def test_real_sentence(sample, original, expected):
    result = clean(original)
    assert result.text == expected
    for a, b, kind in protected_spans(original):
        assert original[a:b] in result.text, (kind, original[a:b])


def test_answer_after_question_kept_with_real_context():
    original = "嗯，因为这里的问题是啊，如果标准不明确，有没有可能有一天你也成为坏人呢？"
    assert clean(original, context_before="那为什么我们不能这样说话呢？").text == original


def test_real_set_size():
    assert len(REAL) == 20
