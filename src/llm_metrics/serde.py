"""Serialization between the frozen IR (section 5.1) and plain dicts/JSON.

The HTML extractor runs in a subprocess (to isolate Playwright's event loop from
Flask), so the IR has to cross a JSON boundary; the same dict shape is what the
persistence layer (P3) stores. Keeping the conversion in one place means the IR
is serialized identically everywhere.
"""

import pathlib

from llm_metrics import ir


def candidate_to_dict(c: ir.Candidate) -> dict:
    return {
        "value_string": c.value_string,
        "source_ref": {
            "kind": c.source_ref.kind,
            "page": c.source_ref.page,
            "selector": c.source_ref.selector,
            "bbox": list(c.source_ref.bbox),
        },
        "crop_path": str(c.crop_path),
        "context": {
            "column_header": c.context.column_header,
            "row_label": c.context.row_label,
            "caption": c.context.caption,
            "footnotes": list(c.context.footnotes),
        },
    }


def candidate_from_dict(d: dict) -> ir.Candidate:
    sr = d["source_ref"]
    ctx = d["context"]
    return ir.Candidate(
        value_string=d["value_string"],
        source_ref=ir.SourceRef(
            kind=sr["kind"],
            page=sr["page"],
            selector=sr["selector"],
            bbox=tuple(sr["bbox"]),  # type: ignore[arg-type]
        ),
        crop_path=pathlib.Path(d["crop_path"]),
        context=ir.Context(
            column_header=ctx["column_header"],
            row_label=ctx["row_label"],
            caption=ctx["caption"],
            footnotes=tuple(ctx["footnotes"]),
        ),
    )
