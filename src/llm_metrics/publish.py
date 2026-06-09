"""Publish the local SQLite results to Supabase.

Reads ``var/metrics.sqlite`` and upserts sources/metrics into Postgres via
PostgREST. Sources include the full HTML/PDF blob.

Uses the service-role key for writes (kept in var/supabase.env, gitignored).
stdlib only, so it runs without the extractor dependencies installed.
"""

import base64
import json
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.request

ENV_PATH = pathlib.Path("var/supabase.env")
DB_PATH = pathlib.Path("var/metrics.sqlite")


def _env() -> dict[str, str]:
    out = {}
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip()
    return out


def _req(method: str, url: str, headers: dict, data: bytes | None = None, tries: int = 4) -> bytes:
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** attempt); continue   # transient gateway/rate errors
            raise
        except urllib.error.URLError:
            if attempt < tries - 1:
                time.sleep(2 ** attempt); continue
            raise
    raise RuntimeError("unreachable")


def _rest_upsert(env: dict, table: str, rows: list[dict]) -> None:
    url = f"{env['SUPABASE_URL']}/rest/v1/{table}?on_conflict=id"
    headers = {"Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
               "apikey": env["SUPABASE_SERVICE_ROLE"], "Content-Type": "application/json",
               "Prefer": "resolution=merge-duplicates,return=minimal"}
    _req("POST", url, headers, json.dumps(rows).encode())


def _rest_delete(env: dict, table: str, query: str) -> None:
    url = f"{env['SUPABASE_URL']}/rest/v1/{table}?{query}"
    headers = {"Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
               "apikey": env["SUPABASE_SERVICE_ROLE"], "Prefer": "return=minimal"}
    _req("DELETE", url, headers)


def publish() -> None:
    env = _env()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    sources = []
    for r in conn.execute("SELECT * FROM sources"):
        blob_b64 = base64.b64encode(r["blob"]).decode("ascii")
        sources.append({"id": r["id"], "kind": r["kind"], "origin_url": r["origin_url"],
                       "sha256": r["sha256"], "blob": blob_b64, "retrieved_at": r["retrieved_at"]})
    _rest_upsert(env, "sources", sources)
    print(f"upserted {len(sources)} sources", flush=True)

    rows = []
    mets = conn.execute("SELECT * FROM metrics ORDER BY id").fetchall()
    for r in enumerate(mets):
        rows.append({"id": r[1]["id"], "source_id": r[1]["source_id"],
                     "model": r[1]["model"], "condition": r[1]["condition"],
                     "benchmark": r[1]["benchmark"], "value": r[1]["value"], "units": r[1]["units"],
                     "row_idx": r[1]["row_idx"], "col_idx": r[1]["col_idx"],
                     "section_key": r[1]["section_key"], "accepted": r[1]["accepted"]})
    for j in range(0, len(rows), 100):
        _rest_upsert(env, "metrics", rows[j:j + 100])
    if rows:
        _rest_delete(env, "metrics", f"id=gt.{max(r['id'] for r in rows)}")
    if sources:
        _rest_delete(env, "sources", f"id=gt.{max(s['id'] for s in sources)}")
    print(f"PUBLISHED sources={len(sources)} metrics={len(rows)}", flush=True)


if __name__ == "__main__":
    sys.exit(publish())
