"""Insert an extracted-benchmark CSV into the Supabase `metrics` table.

Used by the extract-benchmarks skill after the extract -> double-check loop has
produced a clean CSV. First upserts a row in `sources` (by origin_url), then
inserts one `metrics` row per CSV row, linked via source_id. Rows land with
`accepted = false`; a reviewer flips sections to true in review.html.

CSV columns (exact): model,condition,benchmark,value,units,fig_num,row_idx,col_idx,page_num
  - fig_num    -> stored as section_key (groups a table's cells for the review page)
  - row_idx/col_idx: table rows only; graph rows leave them empty
  - page_num:  1-based PDF page the table/figure appears on; always set

Auth/retry: service-role key from var/supabase.env, the `apikey` header (PostgREST
401s without it), and a small backoff on transient 5xx/429. stdlib only.

Usage:
    python3 upload_metrics.py <result.csv> <source-url> <num_pages_total>
"""

import csv
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ENV_PATH = pathlib.Path("var/supabase.env")
_TEXT = ("model", "condition", "benchmark", "value", "units")
_INT = ("row_idx", "col_idx", "page_num")


def _env() -> dict[str, str]:
    if not ENV_PATH.exists():
        raise SystemExit(
            f"Missing {ENV_PATH}. Create it with:\n"
            f"  mkdir -p var\n"
            f"  echo 'SUPABASE_URL=https://<ref>.supabase.co' >> {ENV_PATH}\n"
            f"  echo 'SUPABASE_SERVICE_ROLE=<service_role_key>' >> {ENV_PATH}\n"
            f"(service_role key from Supabase dashboard → Settings → API keys)"
        )
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip()
    return out


def _req(method: str, url: str, headers: dict[str, str], data: bytes | None = None, tries: int = 4) -> bytes:
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return bytes(r.read())
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


def upsert_source(env: dict[str, str], origin_url: str, num_pages_total: int) -> int:
    """Upsert a sources row (by origin_url) and return its id."""
    url = f"{env['SUPABASE_URL']}/rest/v1/sources?on_conflict=origin_url"
    headers = {
        "Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
        "apikey": env["SUPABASE_SERVICE_ROLE"],
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    body = json.dumps([{"origin_url": origin_url, "num_pages_total": num_pages_total}]).encode()
    result = json.loads(_req("POST", url, headers, body))
    return int(result[0]["id"])


def _metric_row(record: dict[str, str], source_id: int) -> dict[str, object]:
    out: dict[str, object] = {"source_id": source_id, "accepted": False}
    for col in _TEXT:
        raw = (record.get(col) or "").strip()
        out[col] = raw or None
    for col in _INT:
        raw = (record.get(col) or "").strip()
        out[col] = int(raw) if raw else None
    fig = (record.get("fig_num") or "").strip()
    out["section_key"] = fig or None
    return out


def upload(csv_path: pathlib.Path, source_url: str, num_pages_total: int) -> int:
    env = _env()
    source_id = upsert_source(env, source_url, num_pages_total)
    with csv_path.open(newline="") as f:
        rows = [_metric_row(rec, source_id) for rec in csv.DictReader(f)]
    if not rows:
        print(f"{csv_path}: no rows to upload")
        return 0
    url = f"{env['SUPABASE_URL']}/rest/v1/metrics"
    headers = {
        "Authorization": f"Bearer {env['SUPABASE_SERVICE_ROLE']}",
        "apikey": env["SUPABASE_SERVICE_ROLE"],
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    for i in range(0, len(rows), 200):
        _req("POST", url, headers, json.dumps(rows[i:i + 200]).encode())
    print(f"source_id={source_id} — inserted {len(rows)} rows into metrics (accepted=false)")
    return len(rows)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    upload(pathlib.Path(argv[1]), argv[2], int(argv[3]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
