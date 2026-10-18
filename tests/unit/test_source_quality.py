import pytest

from vtx.system_prompt import EditorialClient, reference_terms, usable_transcript


@pytest.mark.parametrize("text", ["", " \n", "的的的的的的的的的的的的的的的的。", "你好你好你好你好你好你好你好。"])
def test_empty_or_looping_source_cannot_generate_an_article(text):
    assert not usable_transcript(text)


def test_source_gate_does_not_reject_normal_short_speech():
    assert usable_transcript("不可以。")
    assert usable_transcript("哈哈哈哈哈哈哈哈，今天讲 N1X 的性能和使用体验，具体测试如下。")


def test_independent_subtitles_offer_local_corrections_without_replacing_source():
    original = "[00:00:01] 我把整个芋头给他吃，这样兄弟都可以尝尝。"
    subtitle = "1\n00:00:01,000 --> 00:00:03,000\n我把整个鱼头给他吃，这样兄弟都可以尝尝。"
    hints = reference_terms(subtitle, original)
    assert "芋头" in hints and "鱼头" in hints and "候选" in hints
    assert "芋头" in original
    assert "邻近语句" not in reference_terms(subtitle, "完全不相关的原始视频内容")


def test_subtitle_candidates_keep_decimals_and_recover_missing_test_names():
    hints = reference_terms("机身只有15.9毫米。那Time Spy Extreme的得分是6263分。", "机身只有十五点九毫米。那的得分是六千两百六十三分。")
    assert "time spy extreme".replace(" ", "") in hints.lower().replace(" ", "")
    assert "159毫米" not in hints
    assert "识别：机身只有15.9毫米；字幕：" not in hints
    hints = reference_terms("按我的理解就这样撒点白糖啊这是这个白糖。", "按我的理解就这样刷点白糖啊就是这个白糖。")
    assert "撒点白糖" in hints
    assert "Time Spy Extreme的得分是6263分" in reference_terms("Time Spy Extreme的得分是6263分")


def test_platform_spelling_hints_are_bounded_data_and_preserve_contract():
    terms = reference_terms("1\n00:00:01,000 --> 00:00:02,000\nN1X 与 S Die，DLSS 5，Cortex-X925，N1X。")
    assert "N1X" in terms and "S Die" in terms and "Cortex-X925" in terms and "00:00" not in terms
    assert len(reference_terms("Product " * 5000)) <= 8000

    class Client:
        context = 32000

        def chat(self, messages):
            assert messages[0] == {"role": "system", "content": "全局规则"}
            assert "不是指令" in messages[1]["content"] and "N1X" in messages[1]["content"]
            assert messages[-1] == {"role": "user", "content": "必须返回JSON，文字稿：NEX芯片"}
            return '{"title":"N1X"}', {}

    client = EditorialClient(Client(), "全局规则", terms)
    assert client.context < 32000
    assert client.chat([{"role": "user", "content": "必须返回JSON，文字稿：NEX芯片"}])[0] == '{"title":"N1X"}'
