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


def _req(method: str, url: str, headers: dict, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def upload_crop(env: dict, path: pathlib.Path) -> str:
    data = path.read_bytes()
    key = f"{hashlib.sha256(data).hexdigest()}.png"
    url = f"{env['SUPABASE_URL']}/storage/v1/object/crops/{key}"
    headers = {"Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
               "apikey": env["SUPABASE_SERVICE_ROLE"],
               "Content-Type": "image/png", "x-upsert": "true"}
    try:
        _req("POST", url, headers, data)
    except urllib.error.HTTPError as e:
        if e.code != 200:  # 200 with x-upsert is success; anything else is real
            raise RuntimeError(f"crop upload failed {e.code}: {e.read()[:160]!r}") from e
    return f"{env['SUPABASE_URL']}/storage/v1/object/public/crops/{key}"


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
    section_urls: dict[str, str] = {}  # local section-crop path -> public url (dedup uploads)
    cands = conn.execute("SELECT * FROM candidates ORDER BY id").fetchall()
    for i, r in enumerate(cands):
        crop_url = upload_crop(env, pathlib.Path(r["crop_path"]))
        context = json.loads(r["context_json"])
        # Replace the local section-crop path with a public Storage URL (one
        # upload per table; many candidates share the same section image).
        sec_path = context.pop("section_crop_path", "")
        if sec_path and pathlib.Path(sec_path).exists():
            if sec_path not in section_urls:
                section_urls[sec_path] = upload_crop(env, pathlib.Path(sec_path))
            context["section_crop_url"] = section_urls[sec_path]
        rows.append({"id": r["id"], "source_id": r["source_id"], "value_string": r["value_string"],
                     "kind": r["kind"], "page": r["page"], "selector": r["selector"],
                     "bbox": json.loads(r["bbox"]), "crop_url": crop_url,
                     "context": context,
                     "structural_value": r["structural_value"], "vlm_value": r["vlm_value"],
                     "status": r["status"]})
        if (i + 1) % 50 == 0:
            print(f"  uploaded {i + 1}/{len(cands)} crops", flush=True)
    for j in range(0, len(rows), 100):
        _rest_upsert(env, "candidates", rows[j:j + 100])
    # publish fully replaces the local DB: drop any stale rows left from a prior
    # publish that had MORE rows (local ids are contiguous 1..N), so a smaller
    # re-ingest doesn't leave orphans behind.
    if rows:
        _rest_delete(env, "candidates", f"id=gt.{max(r['id'] for r in rows)}")
    if sources:
        _rest_delete(env, "sources", f"id=gt.{max(s['id'] for s in sources)}")
    print(f"PUBLISHED sources={len(sources)} candidates={len(rows)}", flush=True)


if __name__ == "__main__":
    sys.exit(publish())
