"""Orchestration (VLM-transcription pipeline).

For each source: freeze it, screenshot every data table, and have the vision
model transcribe each table image to CSV. Every numeric cell becomes a candidate
whose provenance is that whole-table screenshot (table-level, not a per-cell
box). The model is the reader; trust is established by human review (P5), not by
a structural cross-check.

A source/table we cannot read is recorded and skipped (a normal outcome,
section 9), not allowed to crash the whole run.
"""

import pathlib

from llm_metrics import corpus, db, extract_pdf, freeze, ir, paths, runner, vlm_table


def _tables(src: corpus.Source, blob_path: str) -> list[dict]:
    if src.kind == "html":
        return runner.run_html_tables(src.origin_url)            # live page, all data tables
    return extract_pdf.list_tables(blob_path, paths.CROPS, src.model_id)


def ingest(conn, src: corpus.Source) -> tuple[int, int]:
    """Freeze, screenshot each table, VLM-transcribe it, persist the cells."""
    fr = freeze.freeze(src.origin_url)
    sid = db.upsert_source(conn, src.kind, src.origin_url, fr.sha256, fr.retrieved_at, fr.blob_path)
    n = 0
    for t in _tables(src, fr.blob_path):
        img = t["image"]
        try:
            rows = vlm_table.transcribe(pathlib.Path(img))
        except Exception as e:  # one unreadable table doesn't sink the source (section 9)
            print(f"    ! {src.model_id} table {t['section_key']}: {type(e).__name__}: {str(e)[:120]}", flush=True)
            continue
        bbox = tuple(t.get("bbox") or (0.0, 0.0, 0.0, 0.0))
        section = {"section_key": t["section_key"], "section_title": t.get("section_title", ""),
                   "section_crop_path": img}
        for col, row_label, value in rows:
            cand = ir.Candidate(
                value_string=value,
                source_ref=ir.SourceRef(kind=src.kind, page=t.get("page"), selector=None, bbox=bbox),
                crop_path=pathlib.Path(img),          # provenance = the whole-table screenshot
                context=ir.Context(column_header=col, row_label=row_label, caption="", footnotes=()))
            # VLM-read, not yet human-reviewed -> 'accepted' (the schema's machine-trust
            # status). Page 5 review upgrades/overrides via the reviews table.
            db.insert_candidate(conn, sid, cand, status="accepted", section=section)
            n += 1
    return sid, n


def run(conn, sources: tuple[corpus.Source, ...] = corpus.SOURCES) -> None:
    paths.ensure()
    for src in sources:
        try:
            _, n = ingest(conn, src)
            print(f"  + {src.model_id}: {n} numbers transcribed", flush=True)
        except Exception as e:  # in-domain failure: skip the source, keep going (section 9)
            print(f"  ! {src.model_id}: failed ({type(e).__name__}: {str(e)[:140]})", flush=True)


def main() -> None:
    """Ingest the given model_ids (or the whole corpus)."""
    import sys
    ids = [a for a in sys.argv[1:] if a != "all"]
    srcs = corpus.SOURCES if not ids else tuple(s for s in corpus.SOURCES if s.model_id in ids)
    run(db.connect(), srcs)


if __name__ == "__main__":
    main()
