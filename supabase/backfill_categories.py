"""Backfill `metrics.category` from a benchmark->category map CSV.

Categories are a property of the *benchmark*, so this PATCHes in place, matching
on `benchmark` (every row for a given benchmark gets the same category). PATCHing
in place is deliberate: it preserves each row's `accepted` flag (the reviewer's
sign-off). Never delete+reinsert to set categories — that would wipe accepts.

The map lives in `supabase/benchmark_category_map.csv` (benchmark,category) and is
the applied classification, kept in the repo so the backfill is reproducible and
revertible. Re-running is idempotent (it just re-sets the same category).

Reads creds from the environment only (SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY),
same as upload_metrics.py. PostgREST DML, not DDL — the `category` column must
already exist (see supabase/metrics.sql). stdlib only.

Usage:
    python3 backfill_categories.py [benchmark_category_map.csv]
"""

import csv
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _env() -> dict[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = (os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
           or os.environ.get("SUPABASE_SERVICE_ROLE", "")).strip()
    if not url or not key:
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
    return {"url": url, "key": key}


def _req(method, url, headers, data=None, tries=4):
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.status, dict(r.headers), bytes(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(f"{method} {url} -> {e.code}: {e.read()[:200]!r}") from e
        except urllib.error.URLError:
            if attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("unreachable")


def _fetch_pairs(env: dict[str, str]) -> list[dict]:
    """Page through every metrics (benchmark, category) row."""
    out, step, frm = [], 1000, 0
    h = {"apikey": env["key"], "Authorization": f"Bearer {env['key']}"}
    while True:
        url = f"{env['url']}/rest/v1/metrics?select=benchmark,category"
        _, _, body = _req("GET", url, {**h, "Range": f"{frm}-{frm + step - 1}"})
        chunk = json.loads(body)
        out.extend(chunk)
        if len(chunk) < step:
            return out
        frm += step


def backfill(csv_path: pathlib.Path) -> int:
    env = _env()
    headers = {
        "Authorization": f"Bearer {env['key']}",
        "apikey": env["key"],
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    with csv_path.open(newline="") as f:
        pairs = [(r["benchmark"], r["category"]) for r in csv.DictReader(f)]
    for name, cat in pairs:
        q = urllib.parse.quote(name, safe="")
        url = f"{env['url']}/rest/v1/metrics?benchmark=eq.{q}"
        _req("PATCH", url, headers, json.dumps({"category": cat}).encode())  # raises on non-2xx
    print(f"PATCHed {len(pairs)} benchmarks")

    # Authoritative verification straight from the DB: nothing empty, every row's
    # category equals the map, and the map covers every benchmark in the table.
    want = dict(pairs)
    rows = _fetch_pairs(env)
    empty = sum(1 for r in rows if not (r.get("category") or ""))
    mism = {r["benchmark"] for r in rows if r.get("category") and want.get(r["benchmark"]) not in (None, r["category"])}
    uncovered = {r["benchmark"] for r in rows if r["benchmark"] not in want}
    print(f"verify: {len(rows)} rows, {empty} empty, {len(mism)} disagree with map, "
          f"{len(uncovered)} benchmarks not in map")
    if empty or mism or uncovered:
        for b in sorted(mism | uncovered):
            print("   needs attention:", b)
        raise SystemExit("backfill incomplete — see above")
    print("verify OK: every metrics row categorised per the map")
    return len(rows)


if __name__ == "__main__":
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else \
        pathlib.Path(__file__).with_name("benchmark_category_map.csv")
    backfill(path)
