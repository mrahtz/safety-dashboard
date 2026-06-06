"""Database schema (FROZEN CONTRACT -- section 5.2).

Single-owner: schema changes are NOT parallelized (section 8). Propose changes
to the orchestrator.

The schema is expressed as SQLite DDL. Enum-valued columns are enforced with
CHECK constraints so an out-of-domain ``status`` or ``attempts.kind`` fails
loudly at write time (section 9) rather than silently corrupting the database.

Column notes that are not obvious from the names:

- ``candidates.bbox``        -- the IR ``SourceRef.bbox`` 4-tuple, stored as a
  JSON array string ``[x0, top, x1, bottom]``.
- ``candidates.context_json`` -- the IR ``Context`` serialized to JSON.
- ``candidates.structural_value`` / ``vlm_value`` -- the *normalized* readings
  filled in during verification (section 5.2). ``value_string`` keeps the
  original raw string from the IR.
"""

import sqlite3

# status ∈ {pending, accepted, rejected, verified, needs_review}
STATUSES: tuple[str, ...] = (
    "pending",
    "accepted",
    "rejected",
    "verified",
    "needs_review",
)

# attempts.kind ∈ {structural, vlm}; candidates.kind / sources.kind ∈ {html, pdf}
ATTEMPT_KINDS: tuple[str, ...] = ("structural", "vlm")
SOURCE_KINDS: tuple[str, ...] = ("html", "pdf")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


DDL: str = f"""
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY,
    kind         TEXT    NOT NULL CHECK ({_in_list('kind', SOURCE_KINDS)}),
    origin_url   TEXT    NOT NULL,
    sha256       TEXT    NOT NULL,
    retrieved_at TEXT    NOT NULL,
    blob_path    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id               INTEGER PRIMARY KEY,
    source_id        INTEGER NOT NULL REFERENCES sources(id),
    value_string     TEXT    NOT NULL,
    kind             TEXT    NOT NULL CHECK ({_in_list('kind', SOURCE_KINDS)}),
    page             INTEGER,
    selector         TEXT,
    bbox             TEXT    NOT NULL,
    crop_path        TEXT    NOT NULL,
    context_json     TEXT    NOT NULL,
    structural_value REAL,
    vlm_value        REAL,
    status           TEXT    NOT NULL DEFAULT 'pending'
                             CHECK ({_in_list('status', STATUSES)})
);

CREATE TABLE IF NOT EXISTS attempts (
    id            INTEGER PRIMARY KEY,
    candidate_id  INTEGER NOT NULL REFERENCES candidates(id),
    kind          TEXT    NOT NULL CHECK ({_in_list('kind', ATTEMPT_KINDS)}),
    instruction   TEXT    NOT NULL,
    result_string TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    """Create the schema on ``conn``. Idempotent (uses IF NOT EXISTS).

    Foreign keys are enforced per-connection in SQLite, so we enable them here
    to make broken candidate/source references fail loudly (section 9).
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(DDL)
    conn.commit()
