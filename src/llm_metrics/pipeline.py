"""Orchestration: freeze -> extract -> persist -> verify, source by source.

A source we cannot structurally parse is recorded and skipped (a normal outcome,
section 9), not allowed to crash the whole run. Verification fans the VLM reads
out across threads (network-bound) and writes results back on the main thread
(SQLite is single-writer).
"""

import pathlib

from llm_metrics import corpus, db, extract_pdf, freeze, paths, runner, verify

MAX_CELLS = 30


def ingest(conn, src: corpus.Source, max_cells: int = MAX_CELLS) -> tuple[int, int]:
    """Freeze the source, extract candidates, persist them as 'pending'."""
    fr = freeze.freeze(src.origin_url)
    sid = db.upsert_source(conn, src.kind, src.origin_url, fr.sha256, fr.retrieved_at, fr.blob_path)
    if src.kind == "html":
        pairs = runner.run_html_sections(src.origin_url)  # live page, all tables + sections
    else:
        pairs = extract_pdf.extract_all_with_sections(fr.blob_path, paths.CROPS, f"{src.model_id}", max_cells)
    for c, section in pairs:
        db.insert_candidate(conn, sid, c, status="pending", section=section)
    return sid, len(pairs)


def verify_source(conn, sid: int) -> dict[str, int]:
    # Sequential by design: the only network step (the VLM) is independent per
    # candidate, and spawning many concurrent OCR subprocesses is fragile in
    # constrained environments. One source is a short job; we run many of them.
    counts: dict[str, int] = {}
    for r in db.pending_candidates(conn, sid):
        cid, vs, cp = r["id"], r["value_string"], pathlib.Path(r["crop_path"])
        db.add_attempt(conn, cid, "structural", "structural extraction", vs)
        try:
            res = verify.verify_candidate(vs, cp)
        except Exception as e:  # a failed read is not a broken invariant (section 9)
            db.add_attempt(conn, cid, "vlm", "read number in red box", f"ERROR:{type(e).__name__}")
            db.set_status(conn, cid, "needs_review")
            counts["needs_review"] = counts.get("needs_review", 0) + 1
            continue
        db.add_attempt(conn, cid, "vlm", "read number in red box", res.vlm_raw)
        db.set_verification(conn, cid, res.structural_value, res.vlm_value, res.status)
        counts[res.status] = counts.get(res.status, 0) + 1
    return counts


def run(conn, sources: tuple[corpus.Source, ...] = corpus.SOURCES, do_verify: bool = True) -> None:
    paths.ensure()
    for src in sources:
        try:
            sid, n = ingest(conn, src)
            line = f"  + {src.model_id}: {n} candidates"
            if do_verify and n:
                counts = verify_source(conn, sid)
                line += "  -> " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        except Exception as e:  # in-domain failure: skip the source, keep going (section 9)
            print(f"  ! {src.model_id}: failed ({type(e).__name__}: {str(e)[:140]})", flush=True)
            continue
        print(line, flush=True)


def main() -> None:
    """Ingest the given model_ids (or the whole corpus). Run one per process so a
    long corpus stays a series of short, isolated jobs."""
    import sys
    ids = [a for a in sys.argv[1:] if a != "all"]
    srcs = corpus.SOURCES if not ids else tuple(s for s in corpus.SOURCES if s.model_id in ids)
    run(db.connect(), srcs)


if __name__ == "__main__":
    main()
