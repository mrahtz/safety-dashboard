---
name: extract-benchmarks
description: Ingest a model/system card **PDF** and extract every benchmark result from its tables and number-labeled graphs into a normalized CSV, then verify it and upload it to the Supabase `metrics` table (accepted=false, pending reviewer sign-off). Use when the user gives you a card PDF and wants its eval numbers pulled out, normalized to canonical benchmark/model names, and uploaded. **PDF files only — web pages are not supported.** Reads tables and graphs directly (the agent does the reading — no VLM pipeline), normalizes names against benchmarks.txt / models.txt, and runs an extract → double-check loop until clean.
---

# Extracting benchmark results from a card PDF

You are the reader. Given one source **as a PDF** (a model/system card or a
paper), pull **every** benchmark number out of its **tables** and its
**number-labeled graphs**, normalize the names, verify the result, and stage it
to Supabase. The output is a CSV with these exact columns:

> **PDF files only.** This skill ingests PDF sources exclusively. Web pages are
> not supported — do **not** attempt to print an HTML page to PDF. The review
> page pairs each PDF page image with the tables/figures extracted from it; every
> row must have a real `page_num`.
>
> **No URL given? Find the source PDF yourself — don't stop and ask.** If the
> user names a card (e.g. "the Mythos system card") without giving a URL or file,
> web-search for the official PDF yourself (see §2) and proceed. Only come back to
> the user if you genuinely can't locate an authoritative PDF, or you've found
> several plausible candidates and need them to disambiguate which one to ingest.

```
model,condition,benchmark,value,units,fig_num,row_idx,col_idx,page_num
```

Work in a scratch dir (e.g. `var/extract/<source-slug>/`) — keep the source,
the page images, and the CSV together so the verify pass can re-look.

## 0. Credentials — set up `var/supabase.env` if missing

Before doing anything else, check whether `var/supabase.env` exists (it lives at
the repo root, is gitignored, and holds the Supabase write credentials).

```bash
test -f var/supabase.env && echo "exists" || echo "missing"
```

If it is **missing**, ask the user for:
- `SUPABASE_URL` — the project URL, e.g. `https://rapkltwpfvzleejytgmq.supabase.co`
- `SUPABASE_SERVICE_ROLE` — a `service_role` JWT or `sb_secret_…` key (from the
  Supabase dashboard → Settings → API keys)

Then write the file:

```bash
mkdir -p var
cat > var/supabase.env <<EOF
SUPABASE_URL=<value from user>
SUPABASE_SERVICE_ROLE=<value from user>
EOF
```

The file is gitignored — it will never be committed. Do not log or echo the key
value. Once the file exists, proceed.

## 1. Canonical name files (read these first, keep them growing)

Two files in this skill dir are the source of truth for names:

- `benchmarks.txt` — canonical **eval** names.
- `models.txt` — canonical **model** names.

Format (both files): one canonical name per line; `#` starts a comment; an
optional `|`-separated tail lists aliases that map to that canonical name, e.g.
`GPQA Diamond | GPQA-D | GPQA (diamond)`. Read both fully before extracting.

These are **read + auto-append**: when you meet a name that is clearly canonical
but missing, append it (and any alias you just resolved). Only append names you
are confident are the real canonical form — never invent canon to make a row
fit. If you are unsure, leave the row's name as-written and flag it in the
verify pass instead of polluting the canon files.

## 2. Get the PDF (find the URL yourself if needed), then rasterize to page images

**If the user didn't hand you a URL or a local file, find the PDF yourself.**
Web-search for the official source — e.g. `<card name> system card PDF` — and
prefer the canonical publisher domain (the lab/vendor's own site or CDN) over
mirrors and re-hosts. Many cards live behind a stable doc page that 307-redirects
to the real CDN PDF (e.g. `anthropic.com/document/...` →
`www-cdn.anthropic.com/...pdf`); verify a candidate with
`curl -sIL <url>` (expect `content-type: application/pdf`) and `pdfinfo` (title +
page count) before committing to it. Use the **stable doc/landing page** as the
`origin_url` you upload, not the hashed CDN path, which can rotate between
revisions. If several distinct PDFs plausibly match (e.g. different page counts /
revisions), ask the user which to ingest rather than guessing.

Once you have it, rasterize every page to a PNG and read the images (graphs only
exist as pixels). `pdftoppm` ships with **poppler-utils** — if it's missing (e.g.
a fresh container), install it first (`apt-get update && apt-get install -y
poppler-utils`, or `brew install poppler`):

```bash
curl -sL <url> -o var/extract/$SLUG/card.pdf
pdftoppm -png -r 300 var/extract/$SLUG/card.pdf var/extract/$SLUG/page
# → page-01.png, page-02.png, …
```

Read each page image. The **image file index** is the `page_num` for every
table/figure on that page: `page-01.png` → `1`, `page-02.png` → `2`. Use this
file index — **not** the page number printed in the PDF's footer or table of
contents. Front matter (title page, TOC, roman-numeral pages) shifts the
printed numbers out of sync with the files, and the review page pairs each
table/figure with the page image whose file index matches `page_num`.

Read the **whole** source before you decide you're done — tables and graphs are
often in an appendix.

## 3. First pass — extract to CSV

Walk the source top to bottom and emit one CSV row per (model, condition,
benchmark) data point. Column rules:

| column | meaning |
| --- | --- |
| `model` | canonical model name from `models.txt` (normalize). |
| `condition` | the eval setting that distinguishes otherwise-identical rows: tool use ("no tools", "with browsing"), reasoning effort ("high"), shot count, split, temperature, pass@k, etc. Empty if the source gives none. |
| `benchmark` | canonical eval name from `benchmarks.txt` (normalize). |
| `value` | the number **exactly as printed** (keep the source's precision/sign; e.g. `91.4`, `0.82`, `1247`). Don't round or rescale. |
| `units` | `%`, `accuracy`, `Elo`, `pass@1`, `s`, … — whatever the source states. Empty if unitless. |
| `fig_num` | **always set** (tables *and* graphs): the table/figure the row came from, **namespaced by kind** — `tbl-<n>` for a table, `fig-<n>` for a graph. Use the source's printed number (Table 3 → `tbl-3`, Figure 2 → `fig-2`); if the source doesn't number them, count from `1` in reading order, tables and figures each in their own sequence (`tbl-1`, `tbl-2`, …; `fig-1`, `fig-2`, …). This value becomes the row's `section_key`, so it must be unique per source table/figure (see below). |
| `row_idx` | **tables only**: 0-based row of the cell within its table (so the table can be reconstructed). Empty for graph rows. |
| `col_idx` | **tables only**: 0-based column of the cell within its table. Empty for graph rows. |
| `page_num` | **always set**: the 1-based **image file index** the table/figure appears on (`page-01.png` → `1`, `page-002.png` → `2`) — the file number, *not* the page number printed in the PDF. The review page aligns each table/figure to its page image by this value. |

So **every** row carries a `fig_num` identifying its source table/figure; a
**table** row additionally sets `row_idx`/`col_idx`, a **graph** row leaves them
empty. `upload_metrics.py` stores `fig_num` verbatim as the review page's
`section_key` (the key it groups a table/figure's cells under), and the review
page tells a table from a graph by whether any cell in the group has a `row_idx`.
Because of that, **Table 1 and Figure 1 must not both be `1`** — they would merge
into a single broken group — which is exactly why tables are `tbl-<n>` and graphs
are `fig-<n>`. Count tables and figures separately.

Extraction rules:

- **Only benchmark/eval results.** Extract numbers that are eval/benchmark
  results. **Skip** tables that aren't evals: model architecture and parameter
  counts, checkpoint/context sizes, pricing, dataset/token counts,
  hyperparameters, etc. (e.g. a "Model parameter counts" table is *not* a
  benchmark — don't ingest it).
- **Tables.** Transcribe every numeric cell. Preserve layout via `row_idx`/
  `col_idx` (header row is row 0; the leftmost label column is col 0 — index the
  *value* cells by their true grid position so the table reconstructs).
- **Graphs — numbers only.** Include a graph data point **only if the value is
  printed on the chart** (a label on the bar/point/line). **Never read a value
  off the axis/scale** — if a bar has no printed number, skip it.
- **Company-internal benchmarks.** If an eval is a proprietary benchmark only
  that one company runs (not a public/shared eval), make that explicit by
  prefixing the owner, e.g. `OpenAI Illicit`, `OpenAI Harassment`, `Anthropic
  internal eval`. Store it that way in the CSV *and* in `benchmarks.txt`.
- **Normalize as you go.** Map each model/benchmark to its canonical form via the
  txt files; append confidently-canonical new names (§0).

Write the CSV (quote any field containing a comma; standard CSV quoting).

## 4. Second pass — verify, fix, repeat until clean

Do **not** trust the first pass. Loop:

1. **Double-check every number.** Go cell-by-cell / point-by-point back to the
   page image and confirm the `value` (and its `units`, `condition`,
   `fig_num`, `page_num`, and for tables its `row_idx`/`col_idx`) matches. Fix
   any mismatch.
2. **Double-check every name** (`benchmark` and `model`). For each, confirm:
   - it **matches what the paper actually calls it** (right eval, right model —
     not a look-alike);
   - it is **the canonical name** (per the txt files), not a variant/alias;
   - it **makes sense semantically** (e.g. a code score on a code benchmark; a
     "% refusals" value isn't tagged as an accuracy eval; a model didn't get
     silently merged with a different size/variant).
   Fix anything off; update `benchmarks.txt` / `models.txt` if you resolved a new
   canonical name or alias.
3. After applying fixes, **run the checks again** from the top. Keep looping
   until a full pass finds **no** issues. Only then is the CSV done.

Briefly note in your reply what you changed between passes (e.g. "pass 2 fixed 3
transposed digits and renamed `GPQA` → `GPQA Diamond`").

## 5. Upload to Supabase (`sources` + `metrics` tables, page images to Storage)

The two live tables are `sources` (one row per card) and `metrics` (one row per
data point). The upload script upserts into `sources` first (idempotent by
`origin_url`), gets back the `source_id`, then inserts all `metrics` rows with
`accepted = false`. A reviewer flips sections to `accepted = true` in
`review.html`; the dashboard's "trusted only" view is driven by that boolean.

The `sources` + `metrics` schema lives in `supabase/metrics.sql`; if the tables
don't exist yet, run that file once via the Management API (`sbp_…` token +
`curl`, see CLAUDE.md "Keys & secrets").

**Set up variables:**
```bash
SLUG=<slug>          # short identifier, e.g. gemini-2-5-pro
SOURCE_URL=<url>     # the canonical source URL
```

**Count pages:**
```bash
NUM_PAGES=$(ls var/extract/$SLUG/page-*.png | wc -l)
```

**Create Storage bucket (idempotent — safe to re-run):**
```bash
python3 .claude/skills/extract-benchmarks/create_bucket.py
```

**Upload metrics** (prints `source_id=<N>` — note the number):
```bash
python3 .claude/skills/extract-benchmarks/upload_metrics.py \
  var/extract/$SLUG/result.csv "$SOURCE_URL" $NUM_PAGES
```

**Upload page images** (substitute `<N>` with the source_id printed above):
```bash
python3 .claude/skills/extract-benchmarks/upload_pages.py $SLUG <N>
```

## Done

Report: source ingested, # tables and # graphs read, # rows extracted, how many
verify passes it took and what they fixed, any names you appended to the canon
files, and the upload result (row count inserted into `metrics`). Don't open a
PR or push unless asked.
