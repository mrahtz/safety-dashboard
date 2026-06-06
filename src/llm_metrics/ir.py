"""Intermediate representation (FROZEN CONTRACT -- section 5.1).

This is the common shape that *both* the HTML and the PDF extractor emit.
Defining it so the two converge is the central architectural move: everything
downstream consumes this and does not care which source type produced it.

Do not change these dataclasses unilaterally. Propose changes to the
orchestrator (section 5).
"""

import dataclasses
import pathlib


@dataclasses.dataclass(frozen=True)
class Context:
    column_header: str
    row_label: str
    caption: str
    footnotes: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class SourceRef:
    kind: str               # "html" | "pdf"
    page: int | None        # pdf page index, else None
    selector: str | None    # html element locator, else None
    bbox: tuple[float, float, float, float]


@dataclasses.dataclass(frozen=True)
class Candidate:
    value_string: str       # raw, un-normalized, as it appears in the source
    source_ref: SourceRef
    crop_path: pathlib.Path
    context: Context
