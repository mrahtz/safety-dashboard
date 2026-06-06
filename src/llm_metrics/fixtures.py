"""Typed loader for the Phase 0 eval fixtures (section 7, Phase 0).

The fixture *data* lives in ``fixtures/eval_fixtures.json`` so a human can read
and edit it without touching code. This module loads it into immutable
dataclasses and validates structure on load, so a malformed fixtures file fails
loudly (section 9) instead of silently feeding garbage into a later regression
check.
"""

import dataclasses
import json
import pathlib

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_PATH = _REPO_ROOT / "fixtures" / "eval_fixtures.json"


@dataclasses.dataclass(frozen=True)
class SourceInfo:
    kind: str               # "html" | "pdf"
    origin_url: str
    captured_at: str
    sha256: str
    note: str


@dataclasses.dataclass(frozen=True)
class Fixture:
    source: str             # key into the sources mapping
    table_index: int | None
    page: int | None        # 0-based pdf page index, else None
    row_label: str
    column_header: str
    value_string: str       # raw, exactly as it appears in the source
    normalized: float | None
    context_note: str


@dataclasses.dataclass(frozen=True)
class FixtureSet:
    sources: dict[str, SourceInfo]
    fixtures: tuple[Fixture, ...]


def load(path: pathlib.Path = DEFAULT_PATH) -> FixtureSet:
    raw = json.loads(path.read_text())
    sources = {
        key: SourceInfo(**{f.name: info[f.name] for f in dataclasses.fields(SourceInfo)})
        for key, info in raw["sources"].items()
    }
    fixtures = tuple(
        Fixture(**{f.name: row[f.name] for f in dataclasses.fields(Fixture)})
        for row in raw["fixtures"]
    )
    _validate(sources, fixtures)
    return FixtureSet(sources=sources, fixtures=fixtures)


def _validate(sources: dict[str, SourceInfo], fixtures: tuple[Fixture, ...]) -> None:
    """Fail loudly on a fixtures file that violates its own invariants."""
    if not fixtures:
        raise ValueError("eval fixtures are empty")
    for fx in fixtures:
        if fx.source not in sources:
            raise ValueError(f"fixture references unknown source {fx.source!r}: {fx}")
        if not fx.value_string:
            raise ValueError(f"fixture has empty value_string: {fx}")
        src = sources[fx.source]
        # A PDF fixture must point at a page; an HTML fixture must not.
        if src.kind == "pdf" and fx.page is None:
            raise ValueError(f"pdf fixture is missing a page: {fx}")
        if src.kind == "html" and fx.page is not None:
            raise ValueError(f"html fixture should not have a page: {fx}")
