"""Orchestration (VLM long-format pipeline).

For each source: freeze it, screenshot every data table (HTML) or render every
page (PDF), and have the vision model transcribe the image into the normalized
long format -- one row per numeric cell with model/condition/benchmark/value/
units and its grid position. Provenance is the whole-table (or page) screenshot.

A source/table we cannot read is recorded and skipped, not allowed to crash the
whole run.
"""

import pathlib

from llm_metrics import corpus, db, extract_pdf, freeze, paths, runner, vlm_table


def _tables(src: corpus.Source, blob_path: str) -> list[dict]:
    if src.kind == "html":
        return runner.run_html_tables(src.origin_url)            # live page, all data tables
    # PDFs: render every page and let the VLM read it (no pdfplumber table detection).
    return extract_pdf.list_pages(blob_path, paths.CROPS, src.model_id)


def ingest(conn, src: corpus.Source) -> tuple[int, int]:
    """Freeze, screenshot each table/page, VLM-transcribe to long format, persist."""
    fr = freeze.freeze(src.origin_url)
    sid = db.upsert_source(conn, src.kind, src.origin_url, fr.sha256, fr.retrieved_at, fr.blob_path)
    n = 0
    for t in _tables(src, fr.blob_path):
        img = t["image"]
        try:
            csv_text = vlm_table.transcribe_raw(pathlib.Path(img),
                                                prompt=vlm_table._LONG_PROMPT, max_tokens=8000)
        except Exception as e:  # one unreadable table doesn't sink the source
            print(f"    ! {src.model_id} {t['section_key']}: {type(e).__name__}: {str(e)[:120]}", flush=True)
            continue
        for m in vlm_table.parse_long(csv_text):
            m["crop_path"] = img        # provenance = the whole-table/page screenshot
            m["section_key"] = t["section_key"]
            m["section_title"] = t.get("section_title", "")
            db.insert_metric(conn, sid, m, status="accepted")
            n += 1
    return sid, n


def run(conn, sources: tuple[corpus.Source, ...] = corpus.SOURCES) -> None:
    paths.ensure()
    for src in sources:
        try:
            _, n = ingest(conn, src)
            print(f"  + {src.model_id}: {n} metrics", flush=True)
        except Exception as e:  # in-domain failure: skip the source, keep going
            print(f"  ! {src.model_id}: failed ({type(e).__name__}: {str(e)[:140]})", flush=True)


def main() -> None:
    import sys
    ids = [a for a in sys.argv[1:] if a != "all"]
    srcs = corpus.SOURCES if not ids else tuple(s for s in corpus.SOURCES if s.model_id in ids)
    run(db.connect(), srcs)


if __name__ == "__main__":
    main()
