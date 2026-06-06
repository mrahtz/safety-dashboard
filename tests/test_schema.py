"""Contract tests for the database schema (section 5.2). Verifies the tables
exist, the enum CHECK constraints reject out-of-domain values (fail loudly,
section 9), foreign keys are enforced, and init is idempotent."""

import sqlite3

import pytest

from llm_metrics import schema


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    schema.init_db(conn)
    return conn


def _insert_source(conn: sqlite3.Connection, kind: str = "pdf") -> int:
    cur = conn.execute(
        "INSERT INTO sources(kind, origin_url, sha256, retrieved_at, blob_path)"
        " VALUES(?, 'http://x', 'deadbeef', '2026-06-06', '/blobs/deadbeef')",
        (kind,),
    )
    return int(cur.lastrowid)


def _insert_candidate(conn: sqlite3.Connection, source_id: int, status: str) -> int:
    cur = conn.execute(
        "INSERT INTO candidates(source_id, value_string, kind, page, selector, bbox,"
        " crop_path, context_json, status) VALUES(?, '0.979', 'pdf', 7, NULL,"
        " '[0,1,2,3]', '/crops/x.png', '{}', ?)",
        (source_id, status),
    )
    return int(cur.lastrowid)


def test_tables_exist():
    conn = _conn()
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "candidates", "attempts"} <= names


def test_idempotent_init():
    conn = _conn()
    schema.init_db(conn)  # second call must not raise
    sid = _insert_source(conn)
    assert _insert_candidate(conn, sid, "pending") > 0


@pytest.mark.parametrize("status", schema.STATUSES)
def test_all_declared_statuses_accepted(status):
    conn = _conn()
    sid = _insert_source(conn)
    assert _insert_candidate(conn, sid, status) > 0


def test_bad_status_rejected():
    conn = _conn()
    sid = _insert_source(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_candidate(conn, sid, "totally-bogus")


def test_bad_attempt_kind_rejected():
    conn = _conn()
    sid = _insert_source(conn)
    cid = _insert_candidate(conn, sid, "pending")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO attempts(candidate_id, kind, instruction, result_string, created_at)"
            " VALUES(?, 'guess', 'i', 'r', '2026-06-06')",
            (cid,),
        )


def test_bad_source_kind_rejected():
    conn = _conn()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_source(conn, kind="docx")


def test_foreign_key_enforced():
    conn = _conn()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_candidate(conn, source_id=999, status="pending")
