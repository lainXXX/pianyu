"""T1 acceptance 1-3: sample list shape and coverage matrix."""
from pathlib import Path

import yaml

DATA = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "experiments" / "samples.yaml").read_text(encoding="utf-8")
)
SAMPLES = DATA["samples"]
FIELDS = ["platform", "url", "link_form", "subs", "lang", "has_numbers_or_negation"]


def test_three_per_platform():
    for platform in ["bilibili", "douyin", "youtube"]:
        assert sum(s["platform"] == platform for s in SAMPLES) == 3, platform


def test_every_sample_fully_labelled():
    for s in SAMPLES:
        missing = [f for f in FIELDS if s.get(f) in ("", None)]
        assert not missing, f"{s['id']} missing {missing}"


def test_long_and_edge_samples_present():
    (long,) = DATA["long"]
    assert long["url"] and 50 <= (long["duration_min"] or 0) <= 70
    kinds = {e["kind"] for e in DATA["edge"] if e["url"]}
    assert kinds == {"silence", "music", "dead_link", "multi_part_unspecified"}


def test_coverage_matrix():
    count = lambda pred: sum(1 for s in SAMPLES if pred(s))
    assert count(lambda s: s["link_form"] == "short") >= 1
    assert count(lambda s: s["platform"] == "bilibili" and "p=" in s["url"]) >= 1
    assert count(lambda s: s["subs"] == "none") >= 2
    assert count(lambda s: s["subs"] not in ("none", "")) >= 2
    for lang in ["zh", "en", "mix"]:
        assert count(lambda s, l=lang: s["lang"] == l) >= 1, lang
    assert count(lambda s: s["has_numbers_or_negation"] is True) >= 3
