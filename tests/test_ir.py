"""Contract tests for the IR (section 5.1). These pin the frozen shape: if a
field name, count, or immutability guarantee changes, these fail."""

import dataclasses
import pathlib

import pytest

from llm_metrics import ir


def _field_names(cls: type) -> tuple[str, ...]:
    return tuple(f.name for f in dataclasses.fields(cls))


def test_field_names_are_frozen_contract():
    assert _field_names(ir.Context) == ("column_header", "row_label", "caption", "footnotes")
    assert _field_names(ir.SourceRef) == ("kind", "page", "selector", "bbox")
    assert _field_names(ir.Candidate) == ("value_string", "source_ref", "crop_path", "context")


def test_dataclasses_are_immutable():
    ref = ir.SourceRef(kind="pdf", page=7, selector=None, bbox=(0.0, 1.0, 2.0, 3.0))
    with pytest.raises(dataclasses.FrozenInstanceError):
        ref.page = 8  # type: ignore[misc]


def test_candidate_composes_the_pieces():
    cand = ir.Candidate(
        value_string="0.868*",
        source_ref=ir.SourceRef(kind="html", page=None, selector="table:nth-of-type(1) td", bbox=(1.0, 2.0, 3.0, 4.0)),
        crop_path=pathlib.Path("/tmp/crop.png"),
        context=ir.Context(column_header="gpt-5.5", row_label="hate", caption="", footnotes=("*",)),
    )
    assert cand.value_string == "0.868*"
    assert cand.context.footnotes == ("*",)
    assert cand.source_ref.bbox == (1.0, 2.0, 3.0, 4.0)
