"""Publish the local SQLite results to a Supabase project (external DB + Storage).

Reads ``var/metrics.sqlite`` + the crop PNGs and:
- uploads each crop to the public ``crops`` bucket, keyed by its sha256 (content
  addressed -> immutable + deduplicated provenance), and
- upserts sources/candidates into Postgres via PostgREST, rewriting the local
  ``crop_path`` to the public Storage ``crop_url``.

Uses the service-role key for writes (kept in var/supabase.env, gitignored).
stdlib only, so it runs without the extractor dependencies installed.
"""

import hashlib
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


_CONTENT_TYPE = {".png": "image/png", ".csv": "text/csv"}


def upload_blob(env: dict, path: pathlib.Path) -> str:
    """Content-address a file into the public crops bucket; return its URL.
    Handles PNG crops/section screenshots and CSV table transcriptions."""
    data = path.read_bytes()
    ext = path.suffix.lower() if path.suffix.lower() in _CONTENT_TYPE else ".png"
    key = f"{hashlib.sha256(data).hexdigest()}{ext}"
    url = f"{env['SUPABASE_URL']}/storage/v1/object/crops/{key}"
    headers = {"Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
               "apikey": env["SUPABASE_SERVICE_ROLE"],
               "Content-Type": _CONTENT_TYPE[ext], "x-upsert": "true"}
    try:
        _req("POST", url, headers, data)
    except urllib.error.HTTPError as e:
        if e.code != 200:  # 200 with x-upsert is success; anything else is real
            raise RuntimeError(f"upload failed {e.code}: {e.read()[:160]!r}") from e
    return f"{env['SUPABASE_URL']}/storage/v1/object/public/crops/{key}"


# Back-compat alias (older callers/tests).
upload_crop = upload_blob


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
    sources = [dict(r) for r in conn.execute("SELECT * FROM sources")]
    _rest_upsert(env, "sources", [{k: s[k] for k in
                 ("id", "kind", "origin_url", "sha256", "retrieved_at", "blob_path")} for s in sources])
    print(f"upserted {len(sources)} sources", flush=True)
    rows = []
    uploaded: dict[str, str] = {}      # local path -> public url; dedup so each image uploads once

    def up(path: str) -> str:
        if path not in uploaded:
            uploaded[path] = upload_blob(env, pathlib.Path(path))
        return uploaded[path]

    mets = conn.execute("SELECT * FROM metrics ORDER BY id").fetchall()
    for i, r in enumerate(mets):
        # Every cell of a table shares ONE whole-table screenshot, so the same
        # image recurs across many rows -- dedup by path (the up() cache) or we'd
        # PUT it hundreds of times (and trip a 504).
        crop_url = up(r["crop_path"]) if r["crop_path"] else ""
        rows.append({"id": r["id"], "source_id": r["source_id"],
                     "model": r["model"], "condition": r["condition"],
                     "benchmark": r["benchmark"], "value": r["value"], "units": r["units"],
                     "row_idx": r["row_idx"], "col_idx": r["col_idx"], "crop_url": crop_url,
                     "section_key": r["section_key"], "section_title": r["section_title"],
                     "status": r["status"]})
        if (i + 1) % 50 == 0:
            print(f"  uploaded {i + 1}/{len(mets)} crops", flush=True)
    for j in range(0, len(rows), 100):
        _rest_upsert(env, "metrics", rows[j:j + 100])
    # publish fully replaces the local DB: drop any stale rows left from a prior
    # publish that had MORE rows (local ids are contiguous 1..N), so a smaller
    # re-ingest doesn't leave orphans behind.
    if rows:
        _rest_delete(env, "metrics", f"id=gt.{max(r['id'] for r in rows)}")
    if sources:
        _rest_delete(env, "sources", f"id=gt.{max(s['id'] for s in sources)}")
    print(f"PUBLISHED sources={len(sources)} metrics={len(rows)}", flush=True)


if __name__ == "__main__":
    sys.exit(publish())
