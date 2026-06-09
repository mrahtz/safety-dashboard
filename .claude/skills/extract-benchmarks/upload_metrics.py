"""Insert an extracted-benchmark CSV into the Supabase `metrics` table.

Used by the extract-benchmarks skill after the extract -> double-check loop has
produced a clean CSV. Rows land with `accepted = false`; a reviewer signs tables
off in review.html (recorded in the `reviews` table), which is what drives the
dashboard's "trusted only" view. Each row is tagged with the `source_url` it came
from so a run is identifiable and re-runnable.

CSV columns (exact): model,condition,benchmark,value,units,fig_num,row_idx,col_idx
  - fig_num is the source table/figure number (always set) -> stored as section_key;
  - a table row also sets row_idx/col_idx; a graph row leaves them empty.

Auth/retry: service-role key from var/supabase.env, the `apikey` header (PostgREST
401s without it), and a small backoff on transient 5xx/429. stdlib only.

Usage:
    python3 upload_metrics.py <result.csv> "<source-url-or-name>"
"""

import csv
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ENV_PATH = pathlib.Path("var/supabase.env")
# CSV column -> metrics column. fig_num maps to section_key (the grouping key the
# dashboard/review pages use); the rest map straight through.
_TEXT = ("model", "condition", "benchmark", "value", "units")
_INT = ("row_idx", "col_idx")


def _env() -> dict[str, str]:
    if not ENV_PATH.exists():
        raise SystemExit(
            f"Missing {ENV_PATH}. Create it with:\n"
            f"  mkdir -p var\n"
            f"  echo 'SUPABASE_URL=https://<ref>.supabase.co' >> {ENV_PATH}\n"
            f"  echo 'SUPABASE_SERVICE_ROLE=<service_role_key>' >> {ENV_PATH}\n"
            f"(service_role key from Supabase dashboard → Settings → API keys)"
        )
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
            raise RuntimeError(f"{method} {url} -> {e.code}: {e.read()[:200]!r}") from e
        except urllib.error.URLError:
            if attempt < tries - 1:
                time.sleep(2 ** attempt); continue
            raise
    raise RuntimeError("unreachable")


def _row(record: dict, source: str) -> dict:
    """One CSV record -> one `metrics` row. Empty cells become NULL; row_idx/col_idx
    are typed as ints; fig_num becomes the text section_key; accepted starts false."""
    out = {"source_url": source, "accepted": False}
    for col in _TEXT:
        raw = (record.get(col) or "").strip()
        out[col] = raw or None
    for col in _INT:
        raw = (record.get(col) or "").strip()
        out[col] = int(raw) if raw else None
    fig = (record.get("fig_num") or "").strip()
    out["section_key"] = fig or None
    return out


def upload(csv_path: pathlib.Path, source: str) -> int:
    env = _env()
    with csv_path.open(newline="") as f:
        rows = [_row(rec, source) for rec in csv.DictReader(f)]
    if not rows:
        print(f"{csv_path}: no rows to upload")
        return 0
    url = f"{env['SUPABASE_URL']}/rest/v1/metrics"
    headers = {"Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
               "apikey": env["SUPABASE_SERVICE_ROLE"],
               "Content-Type": "application/json", "Prefer": "return=minimal"}
    # Chunk so a big card doesn't make one oversized request.
    for i in range(0, len(rows), 200):
        _req("POST", url, headers, json.dumps(rows[i:i + 200]).encode())
    print(f"inserted {len(rows)} rows into metrics (accepted=false, source_url={source!r})")
    return len(rows)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    upload(pathlib.Path(argv[1]), argv[2])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
