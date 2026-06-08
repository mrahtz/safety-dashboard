"""Persistence layer. Stores sources + long-format metrics per the schema.

Re-ingesting the same frozen source (same sha256) clears that source's prior
metrics and re-inserts, so ingest is idempotent.
"""

import pathlib
import sqlite3

from llm_metrics import paths, schema


def connect(path: pathlib.Path | None = None) -> sqlite3.Connection:
    path = path or paths.DB_PATH
    pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    schema.init_db(conn)
    return conn


def upsert_source(conn, kind, origin_url, sha256, retrieved_at, blob_path) -> int:
    row = conn.execute("SELECT id FROM sources WHERE sha256=?", (sha256,)).fetchone()
    if row:
        sid = row["id"]
        conn.execute("DELETE FROM metrics WHERE source_id=?", (sid,))   # idempotent re-ingest
        conn.execute("UPDATE sources SET origin_url=?, retrieved_at=?, blob_path=? WHERE id=?",
                     (origin_url, retrieved_at, blob_path, sid))
        conn.commit()
        return sid
    cur = conn.execute("INSERT INTO sources(kind,origin_url,sha256,retrieved_at,blob_path)"
                       " VALUES(?,?,?,?,?)", (kind, origin_url, sha256, retrieved_at, blob_path))
    conn.commit()
    return int(cur.lastrowid)


def insert_metric(conn, source_id: int, m: dict, status: str = "accepted") -> int:
    """Insert one long-format metric row. ``m`` has model/condition/benchmark/
    value/units/row_idx/col_idx and optionally crop_path/section_key/section_title."""
    cur = conn.execute(
        "INSERT INTO metrics(source_id,model,condition,benchmark,value,units,row_idx,col_idx,"
        "crop_path,section_key,section_title,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (source_id, m["model"], m.get("condition", ""), m["benchmark"], m["value"],
         m.get("units", ""), m.get("row_idx"), m.get("col_idx"),
         str(m.get("crop_path", "")), m.get("section_key"), m.get("section_title"), status))
    conn.commit()
    return int(cur.lastrowid)


def set_status(conn, metric_id: int, status: str) -> None:
    conn.execute("UPDATE metrics SET status=? WHERE id=?", (status, metric_id))
    conn.commit()


def sources(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM metrics m WHERE m.source_id=s.id) n_metrics"
        " FROM sources s ORDER BY s.id").fetchall()


def metrics(conn, source_id: int | None = None) -> list[sqlite3.Row]:
    q = "SELECT m.*, s.origin_url, s.kind src_kind FROM metrics m JOIN sources s ON s.id=m.source_id"
    args: list = []
    if source_id:
        q += " WHERE m.source_id=?"
        args.append(source_id)
    return conn.execute(q + " ORDER BY m.source_id, m.id", args).fetchall()


def status_counts(conn) -> dict[str, int]:
    rows = conn.execute("SELECT status, COUNT(*) n FROM metrics GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
