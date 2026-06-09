"""Database schema (long-format metrics).

One row per numeric cell: (model, condition, benchmark) with its value + units and
grid position (row_idx, col_idx). ``status`` is CHECK-constrained and drives the
review workflow; ``kind`` on sources is html|pdf. Provenance is the table/page
screenshot (crop_path locally, rewritten to crop_url on publish).
"""

import sqlite3

SOURCE_KINDS: tuple[str, ...] = ("html", "pdf")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


DDL: str = f"""
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY,
    kind         TEXT    NOT NULL CHECK ({_in_list('kind', SOURCE_KINDS)}),
    origin_url   TEXT    NOT NULL,
    sha256       TEXT    NOT NULL UNIQUE,
    blob         BLOB    NOT NULL,
    retrieved_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES sources(id),
    model         TEXT    NOT NULL,
    condition     TEXT    NOT NULL DEFAULT '',
    benchmark     TEXT    NOT NULL,
    value         TEXT    NOT NULL,
    units         TEXT    NOT NULL DEFAULT '',
    row_idx       INTEGER,
    col_idx       INTEGER,
    section_key   TEXT,
    accepted      BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS reviews (
    table_key   TEXT PRIMARY KEY,
    status      TEXT NOT NULL CHECK (status IN ('accepted', 'rejected', 'needs_review')),
    note        TEXT,
    reviewer    TEXT,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema on ``conn``. Idempotent (uses IF NOT EXISTS)."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(DDL)
    conn.commit()
