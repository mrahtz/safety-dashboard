"""Tests for the persistence layer: insert metrics, status updates, and
idempotent re-ingest (same source sha -> metrics replaced, not duplicated)."""

from llm_metrics import db


def _metric(value="0.979") -> dict:
    return {"model": "gpt-oss-120b", "condition": "high", "benchmark": "hate",
            "value": value, "units": "", "row_idx": 0, "col_idx": 1,
            "crop_path": "/tmp/crop.png", "section_key": "t0", "section_title": "Table 1"}


def _conn(tmp_path):
    return db.connect(tmp_path / "t.sqlite")


def test_insert_and_query(tmp_path):
    conn = _conn(tmp_path)
    sid = db.upsert_source(conn, "html", "http://x", "abc", "2026-06-06", "/b")
    db.insert_metric(conn, sid, _metric())
    rows = db.metrics(conn)
    assert len(rows) == 1
    assert rows[0]["model"] == "gpt-oss-120b" and rows[0]["value"] == "0.979"
    assert rows[0]["condition"] == "high" and rows[0]["origin_url"] == "http://x"


def test_set_status(tmp_path):
    conn = _conn(tmp_path)
    sid = db.upsert_source(conn, "html", "http://x", "abc", "2026-06-06", "/b")
    mid = db.insert_metric(conn, sid, _metric())
    db.set_status(conn, mid, "verified")
    assert db.status_counts(conn) == {"verified": 1}


def test_idempotent_reingest_replaces(tmp_path):
    conn = _conn(tmp_path)
    sid = db.upsert_source(conn, "html", "http://x", "sha1", "t", "/b")
    db.insert_metric(conn, sid, _metric("1"))
    db.insert_metric(conn, sid, _metric("2"))
    assert len(db.metrics(conn, sid)) == 2
    # Same sha -> reuse the source id and clear its prior metrics.
    sid2 = db.upsert_source(conn, "html", "http://x", "sha1", "t2", "/b2")
    assert sid2 == sid
    assert len(db.metrics(conn, sid)) == 0
    db.insert_metric(conn, sid, _metric("3"))
    assert len(db.metrics(conn, sid)) == 1
