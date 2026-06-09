"""Tests for the persistence layer: insert metrics and dedup by sha256."""

from llm_metrics import db


def _metric(value="0.979") -> dict:
    return {"model": "gpt-oss-120b", "condition": "high", "benchmark": "hate",
            "value": value, "units": "", "row_idx": 0, "col_idx": 1,
            "section_key": "t0"}


def _conn(tmp_path):
    return db.connect(tmp_path / "t.sqlite")


def test_insert_and_query(tmp_path):
    conn = _conn(tmp_path)
    blob = b"<html>test</html>"
    sid = db.upsert_source(conn, "html", "http://x", "abc", blob, "2026-06-06")
    db.insert_metric(conn, sid, _metric(), accepted=True)
    rows = db.metrics(conn)
    assert len(rows) == 1
    assert rows[0]["model"] == "gpt-oss-120b" and rows[0]["value"] == "0.979"
    assert rows[0]["condition"] == "high" and rows[0]["origin_url"] == "http://x"
    assert rows[0]["accepted"] == 1


def test_dedup_by_sha256(tmp_path):
    conn = _conn(tmp_path)
    blob = b"<html>test</html>"
    sid = db.upsert_source(conn, "html", "http://x", "sha1", blob, "2026-06-06")
    db.insert_metric(conn, sid, _metric("1"), accepted=True)
    db.insert_metric(conn, sid, _metric("2"), accepted=False)
    assert len(db.metrics(conn, sid)) == 2
    # Same sha -> reuse the source id.
    sid2 = db.upsert_source(conn, "html", "http://x", "sha1", blob, "2026-06-06")
    assert sid2 == sid
    assert len(db.metrics(conn, sid)) == 2
