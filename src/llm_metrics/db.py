"""Persistence layer (P3). Stores sources/candidates/attempts per the frozen
schema (section 5.2) and answers the queries the review UI and dashboard need.

Re-ingesting the same frozen source (same sha256) clears that source's prior
candidates and re-inserts, so ingest is idempotent: identical bytes -> identical
candidate set.
"""

import datetime
import json
import pathlib
import sqlite3

from llm_metrics import ir, paths, schema


def connect(path: pathlib.Path | None = None) -> sqlite3.Connection:
    path = path or paths.DB_PATH
    # The db may be opened before paths.ensure() runs (e.g. a fresh CI checkout
    # has no var/ dir yet); SQLite won't create the parent, so do it here.
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    schema.init_db(conn)
    return conn


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def upsert_source(conn, kind, origin_url, sha256, retrieved_at, blob_path) -> int:
    row = conn.execute("SELECT id FROM sources WHERE sha256=?", (sha256,)).fetchone()
    if row:
        sid = row["id"]
        conn.execute("DELETE FROM attempts WHERE candidate_id IN "
                     "(SELECT id FROM candidates WHERE source_id=?)", (sid,))
        conn.execute("DELETE FROM candidates WHERE source_id=?", (sid,))
        conn.execute("UPDATE sources SET origin_url=?, retrieved_at=?, blob_path=? WHERE id=?",
                     (origin_url, retrieved_at, blob_path, sid))
        conn.commit()
        return sid
    cur = conn.execute("INSERT INTO sources(kind,origin_url,sha256,retrieved_at,blob_path)"
                       " VALUES(?,?,?,?,?)", (kind, origin_url, sha256, retrieved_at, blob_path))
    conn.commit()
    return int(cur.lastrowid)


def insert_candidate(conn, source_id: int, c: ir.Candidate, status: str = "pending",
                     section: dict | None = None) -> int:
    sr = c.source_ref
    ctx = {"column_header": c.context.column_header, "row_label": c.context.row_label,
           "caption": c.context.caption, "footnotes": list(c.context.footnotes)}
    # Section metadata (which table this number lives in + that table's
    # screenshot) rides inside context_json -- the IR itself is a frozen
    # contract, so we attach it here rather than widening the IR.
    if section:
        for k in ("section_key", "section_title", "section_crop_path", "table_csv_path"):
            if section.get(k):
                ctx[k] = section[k]
    cur = conn.execute(
        "INSERT INTO candidates(source_id,value_string,kind,page,selector,bbox,crop_path,"
        "context_json,status) VALUES(?,?,?,?,?,?,?,?,?)",
        (source_id, c.value_string, sr.kind, sr.page, sr.selector, json.dumps(list(sr.bbox)),
         str(c.crop_path), json.dumps(ctx), status))
    conn.commit()
    return int(cur.lastrowid)


def add_attempt(conn, candidate_id: int, kind: str, instruction: str, result_string: str) -> None:
    conn.execute("INSERT INTO attempts(candidate_id,kind,instruction,result_string,created_at)"
                 " VALUES(?,?,?,?,?)", (candidate_id, kind, instruction, result_string, _now()))
    conn.commit()


def set_verification(conn, candidate_id: int, structural_value, vlm_value, status: str) -> None:
    conn.execute("UPDATE candidates SET structural_value=?, vlm_value=?, status=? WHERE id=?",
                 (structural_value, vlm_value, status, candidate_id))
    conn.commit()


def set_status(conn, candidate_id: int, status: str) -> None:
    conn.execute("UPDATE candidates SET status=? WHERE id=?", (status, candidate_id))
    conn.commit()


def pending_candidates(conn, source_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM candidates WHERE source_id=? AND status='pending' ORDER BY id",
                        (source_id,)).fetchall()


def sources(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM candidates c WHERE c.source_id=s.id) n_candidates,"
        " (SELECT COUNT(*) FROM candidates c WHERE c.source_id=s.id AND c.status='verified') n_verified"
        " FROM sources s ORDER BY s.id").fetchall()


def candidates(conn, status: str | None = None, source_id: int | None = None) -> list[sqlite3.Row]:
    q = ("SELECT c.*, s.origin_url, s.kind src_kind FROM candidates c JOIN sources s ON s.id=c.source_id")
    where, args = [], []
    if status and status != "all":
        where.append("c.status=?"); args.append(status)
    if source_id:
        where.append("c.source_id=?"); args.append(source_id)
    if where:
        q += " WHERE " + " AND ".join(where)
    return conn.execute(q + " ORDER BY c.source_id, c.id", args).fetchall()


def status_counts(conn) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) n FROM candidates GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
