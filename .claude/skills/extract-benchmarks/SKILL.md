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
model,condition,subset,benchmark,category,value,units,fig_num,row_idx,col_idx,page_num
```

Work in a scratch dir (e.g. `var/extract/<source-slug>/`) — keep the source,
the page images, and the CSV together so the verify pass can re-look.

## 0. Credentials — environment variables

All upload scripts read the Supabase write credentials **straight from the
environment** — there is no `var/supabase.env` file anymore. Two env vars are
required:
- `SUPABASE_URL` — the project URL, e.g. `https://rapkltwpfvzleejytgmq.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY` — a `service_role` JWT or `sb_secret_…` key (from
  the Supabase dashboard → Settings → API keys)

In the remote Claude Code environment both are already present, so nothing to do.
Check they're set:

```bash
test -n "$SUPABASE_URL" && test -n "$SUPABASE_SERVICE_ROLE_KEY" && echo "set" || echo "missing"
```

If either is **missing**, ask the user for the values and export them for the
session (do not log or echo the key value, and do not write it to a file):

```bash
export SUPABASE_URL=<value from user>
export SUPABASE_SERVICE_ROLE_KEY=<value from user>
```

Once both are set, proceed.

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

**`page_num` is where the *data* is drawn, never where the caption sits.** A row's
`page_num` is the image file index of the page that **renders that row's own
cell/bar/point** — not the page that carries the figure's caption or number. These
differ when a figure spans a page break: a single figure (one `fig_num`) can have
its panels split across two page images (e.g. one sub-chart on page 25, another on
page 26), with the "Figure N" caption printed on only one of them. That is **not**
an error — assign each row the page its own bar is drawn on, so a figure's rows may
legitimately carry **different `page_num` values under the same `fig_num`**. Do
**not** snap every row of a figure to the caption's page; the review page lines each
number up against the page image it was read from, so a Long-form-virology bar drawn
on page 25 must stay `page_num=25` even though "[Figure 2.2.4.1.A]" is captioned on
page 26. (Real example that bit us: all of `fig-2.2.4.1.A`'s rows got snapped to the
caption page, mis-aligning the page-25 bars against the page-26 image.)

Read the **whole** source before you decide you're done — tables and graphs are
often in an appendix.

## 3. First pass — extract to CSV

Walk the source top to bottom and emit one CSV row per (model, condition,
subset, benchmark) data point. Column rules:

| column | meaning |
| --- | --- |
| `model` | canonical model name from `models.txt` (normalize). |
| `condition` | how the **model** was run — the setting applied to the model itself: tool use ("no tools", "with browsing"), reasoning effort ("high"), thinking ("thinking"/"non-thinking"), safeguards/mitigations, attempt budget ("200 attempts"), and the **sampling/scoring protocol** the number was computed under (shot count "5-shot", "pass@1", "Avg@4", "maj@64", metric name like "Acc."/"EM"), and training variant ("SFT+RL", checkpoint). Empty if the source gives none. |
| `subset` | which **slice of the benchmark** the number is for — a property of the eval, not the model: language or language family ("Portuguese", "Indo-European"), difficulty ("Hard"), topic/harm category ("Criminal / Basic", "Ethics & Morality"), context length ("128k", "RULER 32K"), modality ("audio+visual"), named split/subset ("public split", "validation"), task sub-category ("Code", "Sequence Design"), aggregate ("overall", "Average"), or head-to-head baseline ("vs Gemini 1.5 Flash 002"). Empty if the eval has no sub-slice. |
| `benchmark` | canonical eval name from `benchmarks.txt` (normalize). |
| `category` | the broad family of the **benchmark itself**, from the fixed list in `categories.txt` (Knowledge & Reasoning · Math · Code & Agentic · Multimodal · Multilingual & IF · Cyber · Safety & Refusal · CBRN & Bio · Alignment & Honesty · Other). A property of the **benchmark**, not the row — every row for a given benchmark gets the **same** category. Pick the closest bucket; use `Other` only when nothing fits. The dashboard groups and filters columns by this, so keep it consistent with how the same benchmark was categorized before (check `benchmark_category_map.csv`). |
| `value` | the number **exactly as printed** (keep the source's precision/sign; e.g. `91.4`, `0.82`, `1247`). Don't round or rescale. |
| `units` | `%`, `accuracy`, `Elo`, `pass@1`, `s`, … — whatever the source states. Empty if unitless. |
| `fig_num` | **always set** (tables *and* graphs): the table/figure the row came from, **namespaced by kind** — `tbl-<n>` for a table, `fig-<n>` for a graph. Use the source's printed number (Table 3 → `tbl-3`, Figure 2 → `fig-2`); if the source doesn't number them, count from `1` in reading order, tables and figures each in their own sequence (`tbl-1`, `tbl-2`, …; `fig-1`, `fig-2`, …). This value becomes the row's `section_key`, so it must be unique per source table/figure (see below). |
| `row_idx` | **tables only**: 0-based row of the cell within its table (so the table can be reconstructed). Empty for graph rows. |
| `col_idx` | **tables only**: 0-based column of the cell within its table. Empty for graph rows. |
| `page_num` | **always set**: the 1-based **image file index** the row's own cell/bar/point is **drawn on** (`page-01.png` → `1`, `page-002.png` → `2`) — the file number, *not* the page number printed in the PDF, and *not* the page the figure's caption sits on. The review page aligns each row to its page image by this value. A figure that spans a page break keeps **one `fig_num`** but its rows take **different `page_num`s** — page each row by where its bar is drawn, not by the caption (see §2). |

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
  **CRITICAL:** Bar/point labels are frequently rendered as **vector graphics**
  and will not appear in `pdftotext` output — do **not** use text extraction to
  decide whether a chart has printed values. Always **visually inspect the page
  image**. If labels look small, crop and enlarge the chart area with Python
  Pillow before concluding no values are printed:
  ```python
  from PIL import Image
  img = Image.open('var/extract/$SLUG/page-NN.png')
  w, h = img.size
  crop = img.crop((left, top, right, bottom))  # bounding box of the chart
  zoom = crop.resize((crop.width * 5, crop.height * 5), Image.LANCZOS)
  zoom.save('/tmp/chart_zoom.png')
  ```
  Read the zoomed image. Only skip a graph after visually confirming no printed
  values exist.
- **Company-internal benchmarks.** If an eval is a proprietary benchmark only
  that one company runs (not a public/shared eval), make that explicit by
  prefixing the owner, e.g. `OpenAI Illicit`, `OpenAI Harassment`, `Anthropic
  internal eval`. Store it that way in the CSV *and* in `benchmarks.txt`.
- **Split model-run setting from benchmark slice.** When one label bundles both,
  put each part in the right column — don't dump the whole thing in `condition`.
  A header like "Portuguese (non-thinking)" or "Arabic, high" → `subset` =
  `Portuguese`/`Arabic` (the eval slice), `condition` = `non-thinking`/`high`
  (how the model ran). Rule of thumb: if it describes *the model's setup* it's
  `condition`; if it describes *which part of the eval* the number covers it's
  `subset`. Sampling/scoring (shots, pass@k, Avg@k, metric name) is `condition`.
- **Normalize as you go.** Map each model/benchmark to its canonical form via the
  txt files; append confidently-canonical new names (§0).
- **Categorize each benchmark.** Assign one `category` per the fixed list in
  `categories.txt`. First check `benchmark_category_map.csv` — if the benchmark is
  already there, reuse that category verbatim so the column stays in one group.
  For a new benchmark, pick the closest bucket (the keyword hints in
  `categories.txt` help); `Other` only as a last resort.

Write the CSV (quote any field containing a comma; standard CSV quoting).

## 4. Second pass — verify, fix, repeat until clean

Do **not** trust the first pass. Loop:

1. **Double-check every number.** Go cell-by-cell / point-by-point back to the
   page image and confirm the `value` (and its `units`, `condition`, `subset`,
   `fig_num`, `page_num`, and for tables its `row_idx`/`col_idx`) matches. Fix
   any mismatch. For `page_num`, confirm the row's **own bar/cell is visibly drawn
   on that page image** — don't trust that a figure lives on one page. For any
   `fig_num` whose rows span a page break (panels on two page images), verify each
   row is paged to *its* panel's image, not snapped to the caption's page.
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

**Give the source a display name.** The dashboard/sources/review pages title each
source card from the `SOURCE_LABELS` map in `web/common.js`, keyed by `origin_url`.
A URL not in the map falls back to `prettyFromUrl()` (last path segment, dashes →
spaces), which renders arxiv links as bare numbers (`2506.13585`) and mangles
casing (`Claude Opus 4 5 System Card`, `gpt 5 system card`). So **add an entry for
`$SOURCE_URL`** keyed by the exact URL you uploaded. Match the existing style:
proper model name + lowercase document type — `"GPT-5 system card"`,
`"Grok 4 model card"`, `"DeepSeek-V3 technical report"` (use the arxiv paper's real
title to identify the model). Editing `common.js` means bumping the `?v=`
cache-bust token in `index.html`, `sources.html`, `review.html`, `db-state.html`
(see CLAUDE.md "Frontend changes").

## Done

Report: source ingested, # tables and # graphs read, # rows extracted, how many
verify passes it took and what they fixed, any names you appended to the canon
files, and the upload result (row count inserted into `metrics`). Don't open a
PR or push unless asked.
