"""T9 acceptance 1, 3: each issue type caught on a constructed case; QC never blocks."""
from vtx.asr import Segment
from vtx.qc import check

S = lambda i, a, b, t: Segment(i, a, b, t)


def kinds(issues):
    return sorted({i.kind for i in issues})


def loud(ranges):
    """Fake audio activity: fraction of 'speech-like' frames, 1.0 inside given ranges else 0.0."""
    def active(a, b):
        if b <= a:
            return 0.0
        covered = sum(max(0, min(b, y) - max(a, x)) for x, y in ranges)
        return covered / (b - a)
    return active


CLEAN = [S(0, 0, 5, "今天我们来讲一个问题。"), S(1, 5.5, 10, "这个问题和盐有关系。"), S(2, 10.5, 15, "大家先想一想。")]


def test_clean_input_has_no_issues():
    assert check(CLEAN, duration=15.5, activity=loud([(0, 15)])) == []


def test_missed_speech_gap():
    segs = [CLEAN[0], S(1, 60, 64, "我们继续。")]
    issues = check(segs, duration=65, activity=loud([(0, 64)]))
    assert "gap" in kinds(issues)
    gap = next(i for i in issues if i.kind == "gap")
    assert gap.start == 5 and gap.end == 60


def test_quiet_gap_is_fine():
    segs = [CLEAN[0], S(1, 60, 64, "我们继续。")]
    assert check(segs, duration=65, activity=loud([(0, 5), (60, 64)])) == []


def test_ends_early():
    issues = check(CLEAN, duration=120, activity=loud([(0, 120)]))
    assert "ends_early" in kinds(issues)


def test_junction_repeat_and_loop():
    segs = [S(0, 0, 5, "我们来看下一个例子。"), S(1, 5, 9, "我们来看下一个例子。"),
            S(2, 9, 20, "好的好的好的好的好的好的好的好的")]
    issues = check(segs, duration=20, activity=loud([(0, 20)]))
    assert {"repeat", "loop"} <= set(kinds(issues))


def test_text_in_silence():
    segs = [CLEAN[0], S(1, 20, 25, "谢谢大家观看。")]
    issues = check(segs, duration=25, activity=loud([(0, 5)]))
    assert "text_in_silence" in kinds(issues)


def test_number_divergence_with_second_source():
    second = [(0, 5, "今天我们来讲一个问题。"), (5.5, 10, "每次服用15毫升。")]
    segs = [CLEAN[0], S(1, 5.5, 10, "每次服用50毫升。")]
    issues = check(segs, duration=10.5, activity=loud([(0, 10)]), second=second)
    div = [i for i in issues if i.kind == "number_mismatch"]
    assert div and "15" in div[0].detail and "50" in div[0].detail


SAME = "今天我们来讲机器学习的基本概念和损失函数的定义"


def test_text_divergence_with_second_source():
    # 3 windows agree, 1 is completely different -> only that window is flagged
    segs = [S(k, k * 30, k * 30 + 25, SAME) for k in range(4)]
    second = [(k * 30, k * 30 + 25, SAME) for k in range(3)] + [(90, 115, "完全不同的另一段话讲的是天气和旅行还有吃饭的事情")]
    issues = check(segs, duration=120, activity=loud([(0, 120)]), second=second)
    flagged = [i for i in issues if i.kind == "source_mismatch"]
    assert [i.start for i in flagged] == [90]


def test_unreliable_second_source_gives_one_note_not_noise():
    segs = [S(k, k * 30, k * 30 + 25, SAME) for k in range(4)]
    second = [(k * 30, k * 30 + 25, "完全不同的另一段话讲的是天气和旅行还有吃饭的事情") for k in range(4)]
    issues = check(segs, duration=120, activity=loud([(0, 120)]), second=second)
    assert kinds(issues) == ["second_source_unreliable"]


def test_number_mismatch_ignores_word_vs_number_and_equal_values():
    second = [(0, 10, "今天我们来讲第3个问题和期望值0.30的含义")]
    segs = [S(0, 0, 10, "今天我们来讲第三个问题和七望值0.3的含义")]
    issues = check(segs, duration=10, activity=loud([(0, 10)]), second=second)
    assert "number_mismatch" not in kinds(issues)


def test_small_divergence_not_flagged():
    second = [(0, 5, "今天我们来讲一个问题"), (5.5, 10, "这个问题跟盐有关系"), (10.5, 15, "大家先想一想")]
    assert check(CLEAN, duration=15.5, activity=loud([(0, 15)]), second=second) == []


def test_non_monotonic_and_out_of_range():
    segs = [S(0, 10, 12, "第二句。"), S(1, 5, 8, "第一句。"), S(2, 30, 35, "超出时长。")]
    issues = check(segs, duration=20, activity=loud([(0, 40)]))
    assert {"time_order", "out_of_range"} <= set(kinds(issues))


def test_speech_rate_outlier():
    segs = [S(0, 0, 2, "这是一段在两秒之内说完的非常非常非常长而且根本不可能这么快的句子内容。")]
    assert "speech_rate" in kinds(check(segs, duration=2, activity=loud([(0, 2)])))


def test_issue_has_time_and_is_sorted():
    segs = [S(0, 0, 5, "我们来看下一个例子。"), S(1, 5, 9, "我们来看下一个例子。"), S(2, 60, 62, "完。")]
    issues = check(segs, duration=62, activity=loud([(0, 62)]))
    assert all(i.start is not None for i in issues)
    assert [i.start for i in issues] == sorted(i.start for i in issues)
