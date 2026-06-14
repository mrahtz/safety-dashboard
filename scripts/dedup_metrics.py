#!/usr/bin/env python3
"""Remove exact-duplicate metrics rows.

A "duplicate" is two or more rows that share the same
(source_id, model, benchmark, condition, value) but differ only in section_key
(i.e. the same number was extracted from both a figure and a table in the same PDF).

The row with the lowest `id` is kept; the rest are deleted.

Usage:
    python3 scripts/dedup_metrics.py            # dry-run (default, safe)
    python3 scripts/dedup_metrics.py --execute  # actually delete
"""

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict


def get_env():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        env_file = os.path.join(os.path.dirname(__file__), "..", "var", "supabase.env")
        try:
            with open(env_file) as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        if k.strip() == "SUPABASE_URL":
                            url = v.strip().rstrip("/")
                        elif k.strip() in ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_ROLE"):
                            key = v.strip()
        except FileNotFoundError:
            pass
    if not url or not key:
        sys.exit("ERROR: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY env vars")
    return url, key


def sb_get_all(base_url, key, table, select):
    rows = []
    step = 1000
    offset = 0
    while True:
        path = f"{base_url}/rest/v1/{table}?select={select}&order=id&offset={offset}&limit={step}"
        req = urllib.request.Request(path, headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
        })
        with urllib.request.urlopen(req) as r:
            chunk = json.loads(r.read())
        rows.extend(chunk)
        if len(chunk) < step:
            break
        offset += step
    return rows


def sb_delete(base_url, key, row_id):
    path = f"{base_url}/rest/v1/metrics?id=eq.{row_id}"
    req = urllib.request.Request(
        path,
        method="DELETE",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Prefer": "return=minimal",
        },
    )
    with urllib.request.urlopen(req) as r:
        r.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true",
                        help="Actually delete rows (default: dry-run only)")
    args = parser.parse_args()

    base_url, key = get_env()

    print("Fetching all metrics rows…")
    rows = sb_get_all(base_url, key, "metrics",
                      "id,source_id,model,benchmark,condition,value,section_key")
    print(f"  {len(rows)} rows fetched.")

    # Group by dedup key: (source_id, model, benchmark, condition, value)
    groups = defaultdict(list)
    for r in rows:
        k = (
            r["source_id"],
            (r["model"] or "").strip(),
            (r["benchmark"] or "").strip(),
            (r["condition"] or "").strip(),
            (r["value"] or "").strip(),
        )
        groups[k].append(r)

    to_delete = []
    for k, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: r["id"])
        keep = members[0]
        extras = members[1:]
        to_delete.extend(extras)
        sections = [r["section_key"] for r in members]
        src_id, model, bench, cond, val = k
        print(
            f"  DUP  src={src_id}  {model!r} | {bench!r} | cond={cond!r} | val={val!r}"
        )
        print(f"       keep id={keep['id']} ({keep['section_key']})")
        for ex in extras:
            print(f"       drop id={ex['id']}  ({ex['section_key']})")

    print()
    print(f"{'Would delete' if not args.execute else 'Deleting'} {len(to_delete)} duplicate row(s).")

    if not to_delete:
        print("Nothing to do.")
        return

    if not args.execute:
        print("\nRe-run with --execute to apply.")
        return

    for r in to_delete:
        print(f"  Deleting id={r['id']}…", end=" ")
        sb_delete(base_url, key, r["id"])
        print("done.")

    print(f"\nDeleted {len(to_delete)} rows.")


if __name__ == "__main__":
    main()
