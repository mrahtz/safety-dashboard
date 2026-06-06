"""Tests for value normalization (section 4.4 / 6a)."""

from llm_metrics import normalize


def test_strip_decorations():
    assert normalize.strip_decorations("0.868*") == "0.868"
    assert normalize.strip_decorations("+0.2% (non-egregious)") == "+0.2"
    assert normalize.strip_decorations("51.8% (57.2%, 3818)") == "51.8"


def test_to_float_signs_and_percent():
    assert normalize.to_float("-10.4%") == -10.4
    assert normalize.to_float("+7.9%") == 7.9
    assert normalize.to_float("56.5 (58.4, 2313)") == 56.5


def test_to_float_precision_rounds():
    # 92.30 and 92.3 must compare equal once rounded to the source precision.
    assert normalize.to_float("92.30", precision=1) == normalize.to_float("92.3", precision=1)


def test_fractions_have_no_scalar():
    assert normalize.to_float("11/12") is None
    assert normalize.to_float("0/13") is None


def test_non_numeric_is_none():
    assert normalize.to_float("PASS") is None


def test_precision_of():
    assert normalize.precision_of("0.868") == 3
    assert normalize.precision_of("-10.4%") == 1
    assert normalize.precision_of("11/12") == 0
