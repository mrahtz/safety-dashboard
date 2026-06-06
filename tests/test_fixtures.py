"""Tests for the Phase 0 eval fixtures (section 7, Phase 0).

Beyond "does it load", these guard the *internal consistency* of the hand
transcription: where a fixture declares a normalized float, the leading numeric
token of its raw value_string must agree. That catches a typo introduced while
transcribing (e.g. value_string '0.979' paired with normalized 0.97)."""

import re

import pytest

from llm_metrics import fixtures

_LEADING_NUMBER = re.compile(r"^[+-]?\d+(?:\.\d+)?")

FX = fixtures.load()


def test_loads_and_is_nonempty():
    assert len(FX.fixtures) >= 20
    assert set(FX.sources) == {"gpt-5.5-hub", "gemini-3-pro"}


@pytest.mark.parametrize("source", ["gpt-5.5-hub", "gemini-3-pro"])
def test_roughly_fifteen_per_source(source):
    n = sum(1 for fx in FX.fixtures if fx.source == source)
    assert n >= 11, f"{source} has only {n} fixtures (target ~15)"


def test_source_sha256_present():
    for info in FX.sources.values():
        assert re.fullmatch(r"[0-9a-f]{64}", info.sha256), info.sha256


def test_normalized_matches_leading_token_of_raw():
    for fx in FX.fixtures:
        if fx.normalized is None:
            continue
        m = _LEADING_NUMBER.match(fx.value_string)
        assert m, f"normalized set but no leading number in {fx.value_string!r}"
        assert float(m.group()) == pytest.approx(fx.normalized), fx


def test_footnote_glued_case_is_present():
    # The load-bearing stress case (section 4.2/4.4): '0.868*' -> 0.868.
    glued = [fx for fx in FX.fixtures if fx.value_string == "0.868*"]
    assert len(glued) == 1
    assert glued[0].normalized == 0.868


def test_pdf_fixtures_carry_pages_html_do_not():
    for fx in FX.fixtures:
        kind = FX.sources[fx.source].kind
        if kind == "pdf":
            assert fx.page is not None, fx
        else:
            assert fx.page is None, fx
