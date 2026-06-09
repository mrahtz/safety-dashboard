"""Contract tests for the database schema. Verifies the tables exist, the
CHECK constraints work, foreign keys are enforced, and init is idempotent."""

import sqlite3

import pytest

from llm_metrics import schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    schema.init_db(conn)
    return conn


def _insert_source(conn: sqlite3.Connection, kind: str = "pdf") -> int:
    cur = conn.execute(
        "INSERT INTO sources(kind, origin_url, sha256, blob, retrieved_at)"
        " VALUES(?, 'http://x', 'deadbeef', ?, '2026-06-06')", (kind, b"test"))
    return int(cur.lastrowid)


def _insert_metric(conn: sqlite3.Connection, source_id: int, accepted: bool = False) -> int:
    cur = conn.execute(
        "INSERT INTO metrics(source_id, model, benchmark, value, accepted)"
        " VALUES(?, 'gpt-oss-120b', 'AIME 2024', '95.8', ?)", (source_id, accepted))
    return int(cur.lastrowid)


def test_tables_exist():
    conn = _conn()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "metrics", "reviews"} <= names


def test_idempotent_init():
    conn = _conn()
    schema.init_db(conn)  # second call must not raise
    sid = _insert_source(conn)
    assert _insert_metric(conn, sid, True) > 0


def test_bad_source_kind_rejected():
    conn = _conn()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_source(conn, kind="docx")


def test_foreign_key_enforced():
    conn = _conn()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_metric(conn, source_id=999)


def test_review_status_constraint():
    conn = _conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO reviews(table_key, status) VALUES('x::y', 'bogus')")
