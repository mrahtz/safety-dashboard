#!/usr/bin/env python3
"""Fix missing condition labels in the metrics table.

2a) Qwen3 thinking/non-thinking (source 14)
    The Qwen3 paper alternates thinking/non-thinking results in consecutive
    tables. Confirmed from tbl-22 (which carries explicit condition labels):
      tbl-13, tbl-15, tbl-17, tbl-19, tbl-21 → "thinking"
      tbl-14, tbl-16, tbl-18, tbl-20         → "non-thinking"
    Only applies to rows whose model name starts with "Qwen3-" and whose
    current condition is null/empty.

2b) BBQ sub-metrics (source 11, section tbl-3.5.2.A)
    Each model has two rows with condition "standard thinking" but different
    values — two distinct sub-metrics from the same table. These are printed
    for manual investigation; not patched automatically unless --fix-bbq is
    passed (after the correct sub-metric labels are confirmed).

Usage:
    python3 scripts/fix_conditions.py            # dry-run (default, safe)
    python3 scripts/fix_conditions.py --execute  # apply Qwen3 patches
    python3 scripts/fix_conditions.py --fix-bbq VALUE_A=LABEL_A VALUE_B=LABEL_B --execute
"""

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict


# tbl-11/12: thinking/non-thinking split for Qwen3-235B-A22B (confirmed: AIME 2024 = 85.7 vs 40.1)
# tbl-13/14 through tbl-19/20: thinking/non-thinking splits for smaller Qwen3 models
# (confirmed from tbl-22 which carries explicit condition labels matching tbl-13 thinking values)
# tbl-3 through tbl-8 are "comparison with baselines" tables with different evaluation setups
# — they report genuinely different values from tbl-11+ so are left unlabeled intentionally.
THINKING_SECTIONS     = {"tbl-11", "tbl-13", "tbl-15", "tbl-17", "tbl-19", "tbl-21"}
NON_THINKING_SECTIONS = {"tbl-12", "tbl-14", "tbl-16", "tbl-18", "tbl-20"}

QWEN3_SOURCE_ID = 14
BBQ_SOURCE_ID   = 11


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


def sb_get_all(base_url, key, table, select, filters=""):
    rows = []
    step = 1000
    offset = 0
    while True:
        path = (f"{base_url}/rest/v1/{table}?select={select}"
                + (f"&{filters}" if filters else "")
                + f"&order=id&offset={offset}&limit={step}")
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


def sb_patch(base_url, key, row_id, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/rest/v1/metrics?id=eq.{row_id}",
        data=data,
        method="PATCH",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    with urllib.request.urlopen(req) as r:
        r.read()


def fix_qwen3(base_url, key, execute):
    print("=== 2a: Qwen3 thinking / non-thinking (source 14) ===\n")
    rows = sb_get_all(base_url, key, "metrics",
                      "id,model,benchmark,condition,value,section_key",
                      f"source_id=eq.{QWEN3_SOURCE_ID}")

    patches = []
    skipped = []
    for r in rows:
        model = (r["model"] or "").strip()
        if not model.startswith("Qwen3-"):
            continue
        cond = (r["condition"] or "").strip()
        if cond:
            continue  # already labelled
        sec = (r["section_key"] or "").strip()
        # Extract base table key (strip sub-letter suffix like tbl-13a → tbl-13)
        import re
        m = re.match(r"(tbl-\d+)", sec)
        base_sec = m.group(1) if m else sec
        if base_sec in THINKING_SECTIONS:
            new_cond = "thinking"
        elif base_sec in NON_THINKING_SECTIONS:
            new_cond = "non-thinking"
        else:
            skipped.append(r)
            continue
        patches.append((r, new_cond))

    if skipped:
        print(f"  WARNING: {len(skipped)} Qwen3 rows with null condition have unrecognised "
              f"section_keys — skipping:")
        for r in skipped:
            print(f"    id={r['id']}  {r['model']} | {r['benchmark']} | sec={r['section_key']}")
        print()

    if not patches:
        print("  No Qwen3 rows need patching.\n")
        return

    # Print summary grouped by model → section → proposed condition
    by_model = defaultdict(list)
    for r, new_cond in patches:
        by_model[r["model"]].append((r, new_cond))

    for model, items in sorted(by_model.items()):
        secs = sorted(set(f"{r['section_key']}→{c}" for r, c in items))
        print(f"  {model}: {len(items)} rows  ({', '.join(secs[:4])}{'…' if len(secs)>4 else ''})")

    print()
    print(f"{'Would patch' if not execute else 'Patching'} {len(patches)} rows.\n")

    if not execute:
        print("  Re-run with --execute to apply.\n")
        return

    for r, new_cond in patches:
        print(f"  PATCH id={r['id']}  {r['model']} | {r['benchmark']} | "
              f"sec={r['section_key']}  →  condition={new_cond!r}")
        sb_patch(base_url, key, r["id"], {"condition": new_cond})
    print(f"\nPatched {len(patches)} rows.")


def inspect_bbq(base_url, key):
    print("=== 2b: BBQ sub-metrics (source 11, tbl-3.5.2.A) ===\n")
    rows = sb_get_all(base_url, key, "metrics",
                      "id,model,benchmark,condition,value,section_key",
                      f"source_id=eq.{BBQ_SOURCE_ID}&benchmark=eq.BBQ")

    # Find duplicate conditions within the same (model, section_key)
    by_model_sec = defaultdict(list)
    for r in rows:
        by_model_sec[(r["model"], r["section_key"])].append(r)

    issues = {k: v for k, v in by_model_sec.items() if len(v) > 1}
    if not issues:
        print("  No BBQ sub-metric conflicts found.\n")
        return

    print("  These rows share the same model + section but have different values.")
    print("  They need distinct condition labels — check the source PDF to name them.\n")
    for (model, sec), items in sorted(issues.items()):
        print(f"  {model}  (section {sec})")
        for r in sorted(items, key=lambda x: float(x["value"])):
            print(f"    id={r['id']}  condition={r['condition']!r}  value={r['value']}")
    print()
    print("  To fix: re-run with --fix-bbq after identifying the sub-metric names.\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true",
                        help="Apply patches (default: dry-run)")
    parser.add_argument("--fix-bbq", action="store_true",
                        help="Also patch BBQ rows (requires confirming labels first)")
    args = parser.parse_args()

    base_url, key = get_env()

    fix_qwen3(base_url, key, args.execute)
    inspect_bbq(base_url, key)


if __name__ == "__main__":
    main()
