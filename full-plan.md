# Plan: Metrics Data QA + Source-Attribution UI

## Context

The Supabase `metrics` table has three data quality problems found during QA:

1. **True duplicates**: same (source_id, model, benchmark, condition, value) was extracted from
   both a figure and a table in the same PDF, creating redundant rows with different `section_key`
   values. These show as duplicate columns in the dashboard.

2. **Missing condition labels**: Qwen3 paper (source 14) reports thinking-mode and non-thinking-mode
   scores in alternating tables (tbl-13/15/17/19 = thinking; tbl-14/16/18/20 = non-thinking), but
   both were extracted with `condition = null`. Result: two wildly different AIME 2024 values (e.g.
   76.0 vs 31.0) sitting in separate unlabelled columns for the same model. BBQ also has two rows
   per model from the same section with the same condition label but different values (two different
   sub-metrics conflated).

3. **Dashboard layout**: `benchId = [url, section_key, bench, cond].join("::")` means every
   source × section × benchmark × condition is a separate column. After dedup + condition fixes,
   the right design is one column per (benchmark, condition) — any cross-source conflicts sit in
   the same cell and the existing modal already shows each value with its source.

---

## Step 1 — `scripts/dedup_metrics.py` (reusable)

**Logic:**
- Group all metrics rows by `(source_id, model, benchmark, condition, value)`.
- For any group with >1 row, keep the lowest `id` and delete the rest.
- `--dry-run` mode (default): prints every would-be deletion.
- `--execute` mode: fires `DELETE /rest/v1/metrics?id=eq.{id}` for each extra row.
- Uses `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` env vars (or reads `var/supabase.env`).

**Expected deletions (from QA):**
- 6 benchmarks for models in source 10 (same value in fig-1 AND tbl-6):
  AIME 2024, Codeforces, GPQA Diamond, MATH, MMLU-Pro, SWE-bench Verified —
  affecting Claude 3.5 Sonnet (2024-10-22), DeepSeek-V2.5, DeepSeek-V3,
  GPT-4o (2024-05-13), Llama 3.1 405B.
- Magistral Medium AIME 2024 pass@1 and maj@64 (source 8, two sections).
- QwQ-32B AIME 2024 (tbl-13 + tbl-15 in source 14).
- Claude Mythos Preview + Claude Opus 4.6 + Claude Sonnet 4.6:
  Child Safety Multi-turn, Suicide/Self-harm Multi-turn, CharXiv Reasoning, USAMO 2026
  (each in both a fig and a table in source 3).

---

## Step 2 — `scripts/fix_conditions.py`

Two independent fixes:

### 2a — Qwen3 thinking/non-thinking (source 14)

Section key pattern (confirmed from rows that already carry condition labels in tbl-22):
- `tbl-13, tbl-15, tbl-17, tbl-19, tbl-21` → condition `thinking`
- `tbl-14, tbl-16, tbl-18, tbl-20`         → condition `non-thinking`

Script queries `source_id=14, condition=null` and patches `condition` by section_key prefix.
Applies across all benchmarks from source 14, not just AIME 2024.

`--dry-run` (default): prints proposed patches. `--execute`: PATCHes via PostgREST.

### 2b — BBQ sub-metrics (source 11, section tbl-3.5.2.A)

Two rows per model with identical condition "standard thinking" but different values
(e.g. Haiku 4.5: 1.37 and 0.54). These are two distinct sub-metrics in the same table;
need to verify the paper to name them before patching.

Script **prints** these rows in dry-run but does **not** auto-patch.
Add `--fix-bbq` flag after labels are confirmed manually.

---

## Step 3 — `web/index.html` UI change

Change `benchId` (line 88) from:
```js
const benchId = [url, c.section_key || "", bench, cond].join("::");
```
to:
```js
const benchId = [bench, cond].join("::");
```

Change `group` from `sourceLabel(url)` to first word of benchmark name:
```js
const group = bench.split(" ")[0];
```

**Effect:**
- One column per (benchmark, condition) instead of per (source × section × benchmark × condition).
- Cross-source conflicts land in one cell; the existing `±N` marker fires; the existing
  `openCell()` modal already shows each value with its `sourceLabel(...)` — no modal changes needed.
- Column count drops dramatically (e.g. AIME 2024: from ~5 columns to 2).

No `common.js` change → no `?v=` bump needed (index.html is DYNAMIC/uncached).

---

## Order of operations

1. `scripts/fix_conditions.py --execute` (set condition labels before dedup so the dedup key is clean)
2. `scripts/dedup_metrics.py --execute` (duplicates now share the same composite key)
3. Push `web/index.html` to `main` → auto-deploys in ~1 min.

## Verification

- Run each script `--dry-run` first, review output, then `--execute`.
- After dedup: re-query Supabase and confirm no `(source_id, model, benchmark, condition, value)`
  group has >1 row.
- After condition fix: spot-check Qwen3-32B AIME 2024 — should be exactly two rows:
  `condition=thinking value≈81.4` and `condition=non-thinking value≈31.0`.
- After UI deploy: open dashboard, filter benchmarks to "AIME" — should see 2 columns
  (AIME 2024 · (none) and AIME 2024 · Avg@64). Click Claude 4 Opus in the null column —
  modal should show two rows (75.7 and 76.0) each with their source label.
