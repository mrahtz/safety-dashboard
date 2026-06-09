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


def upsert_source(conn, kind, origin_url, sha256, blob, retrieved_at) -> int:
    row = conn.execute("SELECT id FROM sources WHERE sha256=?", (sha256,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO sources(kind,origin_url,sha256,blob,retrieved_at)"
                       " VALUES(?,?,?,?,?)", (kind, origin_url, sha256, blob, retrieved_at))
    conn.commit()
    return int(cur.lastrowid)


def insert_metric(conn, source_id: int, m: dict, accepted: bool = False) -> int:
    """Insert one long-format metric row. ``m`` has model/condition/benchmark/
    value/units/row_idx/col_idx."""
    cur = conn.execute(
        "INSERT INTO metrics(source_id,model,condition,benchmark,value,units,row_idx,col_idx,accepted)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (source_id, m["model"], m.get("condition", ""), m["benchmark"], m["value"],
         m.get("units", ""), m.get("row_idx"), m.get("col_idx"), accepted))
    conn.commit()
    return int(cur.lastrowid)


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
