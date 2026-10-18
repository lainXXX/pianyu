"""T5 acceptance 1-2: normalisation, CER/WER, number errors."""
import pytest

from vtx.textnorm import error_rate, number_errors, tokens


def test_traditional_and_simplified_are_equal():
    assert tokens("一個簡單的網絡模型") == tokens("一个简单的网络模型")
    assert error_rate("训练模型", "訓練模型") == 0


def test_punctuation_fullwidth_case():
    assert tokens("Ｈｅｌｌｏ，世界！") == ["hello", "世", "界"]
    assert tokens("“GPU” 很快。") == ["gpu", "很", "快"]


@pytest.mark.parametrize("a,b", [
    ("每次服用1.5毫升", "每次服用一点五毫升"),
    ("15个人", "十五个人"),
    ("2026年", "两千零二十六年"),
    ("2026年", "二零二六年"),
    ("零下-5度", "零下负五度"),
    ("增长50%", "增长百分之五十"),
    ("第3次", "第三次"),
])
def test_equivalent_numbers(a, b):
    assert tokens(a) == tokens(b)


@pytest.mark.parametrize("a,b", [
    ("1.5毫升", "15毫升"),
    ("-5度", "5度"),
    ("50%", "50"),
    ("一百", "一千"),
])
def test_different_numbers_stay_different(a, b):
    assert tokens(a) != tokens(b)


@pytest.mark.parametrize("word", ["统一一下", "我们一起", "一样", "一下", "万一", "千万不要", "十分好", "一一列举"])
def test_numerals_inside_ordinary_words_untouched(word):
    assert not any(t[0].isdigit() for t in tokens(word)), tokens(word)


@pytest.mark.parametrize("text,number", [
    ("最多三次", "3"), ("两个人", "2"), ("第一", "1"), ("第十", "10"),
    ("三分钟", "3"), ("一千万", "10000000"), ("二零二六年", "2026"),
])
def test_quantities_converted(text, number):
    assert number in tokens(text)


def test_no_false_number_error_from_ordinary_words():
    assert number_errors("我们统一一下", "我们统计一下") == []


def test_sign_and_percent_kept_as_part_of_number():
    assert tokens("跌了-3.2%") == ["跌", "了", "-3.2%"]


def test_cer_matches_hand_count():
    # 1 substitution (气->汽) + 1 deletion (好) over 6 reference chars
    assert error_rate("今天天气很好", "今天天汽很") == pytest.approx(2 / 6, abs=1e-3)


def test_wer_matches_hand_count():
    # 1 substitution (sat->sit) + 1 insertion (down) over 3 reference words
    assert error_rate("The cat sat.", "the cat sit down") == pytest.approx(2 / 3, abs=1e-3)


def test_mixed_language_tokens():
    assert tokens("用GPU训练模型") == ["用", "gpu", "训", "练", "模", "型"]
    assert error_rate("用GPU训练", "用CPU训练") == pytest.approx(1 / 4, abs=1e-3)


def test_identical_is_zero_and_empty_reference():
    assert error_rate("你好。", "你好") == 0
    assert error_rate("", "") == 0
    with pytest.raises(ValueError):
        error_rate("", "多出来的字")


def test_number_substitution_listed():
    assert number_errors("每次服用15毫升", "每次服用50毫升") == [{"ref": "15", "hyp": "50"}]


def test_number_deletion_and_insertion_listed():
    assert number_errors("最多三次", "最多次") == [{"ref": "3", "hyp": ""}]
    assert number_errors("最多次", "最多3次") == [{"ref": "", "hyp": "3"}]


def test_equivalent_number_is_not_an_error():
    assert number_errors("一点五毫升", "1.5毫升") == []
