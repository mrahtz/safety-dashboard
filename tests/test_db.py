"""Tests for the persistence layer (P3): insert, status updates, attempts, and
idempotent re-ingest (same source -> candidates replaced, not duplicated)."""

import pathlib

from llm_metrics import db, ir


def _candidate(value="0.979") -> ir.Candidate:
    return ir.Candidate(
        value_string=value,
        source_ref=ir.SourceRef(kind="pdf", page=7, selector=None, bbox=(1.0, 2.0, 3.0, 4.0)),
        crop_path=pathlib.Path("/tmp/crop.png"),
        context=ir.Context(column_header="col", row_label="row", caption="", footnotes=("*",)),
    )


def _conn(tmp_path):
    return db.connect(tmp_path / "t.sqlite")


def test_insert_and_query(tmp_path):
    conn = _conn(tmp_path)
    sid = db.upsert_source(conn, "pdf", "http://x", "abc", "2026-06-06", "/b")
    cid = db.insert_candidate(conn, sid, _candidate())
    db.set_verification(conn, cid, structural_value=0.979, vlm_value=0.979, status="verified")
    rows = db.candidates(conn, status="verified")
    assert len(rows) == 1 and rows[0]["structural_value"] == 0.979


def test_attempts_logged(tmp_path):
    conn = _conn(tmp_path)
    sid = db.upsert_source(conn, "pdf", "http://x", "abc", "2026-06-06", "/b")
    cid = db.insert_candidate(conn, sid, _candidate())
    db.add_attempt(conn, cid, "vlm", "read number", "0.979")
    n = conn.execute("SELECT COUNT(*) n FROM attempts WHERE candidate_id=?", (cid,)).fetchone()["n"]
    assert n == 1


def test_idempotent_reingest_replaces(tmp_path):
    conn = _conn(tmp_path)
    sid1 = db.upsert_source(conn, "pdf", "http://x", "samehash", "2026-06-06", "/b")
    db.insert_candidate(conn, sid1, _candidate("1.0"))
    # Re-ingest the same sha256: same source id, prior candidates cleared.
    sid2 = db.upsert_source(conn, "pdf", "http://x", "samehash", "2026-06-07", "/b")
    db.insert_candidate(conn, sid2, _candidate("2.0"))
    assert sid1 == sid2
    rows = db.candidates(conn, status="all", source_id=sid2)
    assert [r["value_string"] for r in rows] == ["2.0"]
