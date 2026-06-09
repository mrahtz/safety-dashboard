---
name: extract-benchmarks
description: Ingest a model/system card or web page and extract every benchmark result from its tables and number-labeled graphs into a normalized CSV, then verify it and stage it to the Supabase `pending` table. Use when the user gives you a card/paper/page (URL, PDF, or local file/image) and wants its eval numbers pulled out, normalized to canonical benchmark/model names, and uploaded. Reads tables and graphs directly (the agent does the reading — no VLM pipeline), normalizes names against benchmarks.txt / models.txt, and runs an extract → double-check loop until clean.
---

# Extracting benchmark results from a card or web page

You are the reader. Given one source (a model/system card, a paper, or a web
page — as a URL, a PDF, or a local file/image), pull **every** benchmark number
out of its **tables** and its **number-labeled graphs**, normalize the names,
verify the result, and stage it to Supabase. The output is a CSV with these
exact columns:

```
model,condition,benchmark,value,units,fig_num,row_idx,col_idx
```

Work in a scratch dir (e.g. `var/extract/<source-slug>/`) — keep the source,
the page/figure images, and the CSV together so the verify pass can re-look.

## 0. Canonical name files (read these first, keep them growing)

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

## 1. Get the source in front of you

Whatever the input, turn it into something you can actually read closely:

- **Web page (HTML).** The DOM has exact values — prefer them over a screenshot.
  Fetch the page (WebFetch, or `curl -sL <url> -o page.html`) and read the raw
  table markup. Also grab a rendered screenshot if the layout/graphs matter.
- **PDF.** Rasterize the pages to PNGs and read the images (graphs only exist as
  pixels): `pdftoppm -png -r 150 card.pdf page` → `page-01.png`, … Read each
  page image. (`src/llm_metrics/crop.py` has `render_pdf_page` if you want to
  rasterize a page programmatically.)
- **Local file / image.** Read it directly.

Read the **whole** source before you decide you're done — tables and graphs are
often in an appendix.

## 2. First pass — extract to CSV

Walk the source top to bottom and emit one CSV row per (model, condition,
benchmark) data point. Column rules:

| column | meaning |
| --- | --- |
| `model` | canonical model name from `models.txt` (normalize). |
| `condition` | the eval setting that distinguishes otherwise-identical rows: tool use ("no tools", "with browsing"), reasoning effort ("high"), shot count, split, temperature, pass@k, etc. Empty if the source gives none. |
| `benchmark` | canonical eval name from `benchmarks.txt` (normalize). |
| `value` | the number **exactly as printed** (keep the source's precision/sign; e.g. `91.4`, `0.82`, `1247`). Don't round or rescale. |
| `units` | `%`, `accuracy`, `Elo`, `pass@1`, `s`, … — whatever the source states. Empty if unitless. |
| `fig_num` | **always set** (tables *and* graphs): the table/figure number the row came from. Use the source's printed number (Table 3 → `3`, Figure 2 → `2`); if the source doesn't number them, count from `1` in reading order — tables and figures each in their own sequence. |
| `row_idx` | **tables only**: 0-based row of the cell within its table (so the table can be reconstructed). Empty for graph rows. |
| `col_idx` | **tables only**: 0-based column of the cell within its table. Empty for graph rows. |

So **every** row carries a `fig_num` identifying its source table/figure; a
**table** row additionally sets `row_idx`/`col_idx`, a **graph** row leaves them
empty. Whether `row_idx`/`col_idx` are populated is what distinguishes a table
row (`fig_num=1` → Table 1) from a graph row (`fig_num=1` → Figure 1). Count
tables and figures separately.

Extraction rules:

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

## 3. Second pass — verify, fix, repeat until clean

Do **not** trust the first pass. Loop:

1. **Double-check every number.** Go cell-by-cell / point-by-point back to the
   source image or DOM and confirm the `value` (and its `units`, `condition`,
   `fig_num`, and for tables its `row_idx`/`col_idx`) matches. Fix any mismatch.
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

## 4. Stage to Supabase (`pending` table)

The verified CSV is uploaded to a dedicated Supabase staging table named
`pending` (separate from the live `metrics`/`candidates` tables).

**One-time:** create the table if it doesn't exist yet. It's DDL, so it goes
through the Management API with `curl` (see CLAUDE.md "Keys & secrets" — a
`sbp_…` token, *not* the service key, and `curl` because Cloudflare blocks
urllib):

```bash
# needs a short-lived sbp_… personal token + the project ref (rapkltwpfvzleejytgmq)
curl -sS https://api.supabase.com/v1/projects/$PROJECT_REF/database/query \
  -H "Authorization: Bearer $SBP_TOKEN" -H "Content-Type: application/json" \
  --data @<(jq -Rs '{query: .}' < .claude/skills/extract-benchmarks/pending_table.sql)
```

**Every run:** push the CSV rows (PostgREST insert, service-role key from
`var/supabase.env` — same auth pattern as `publish.py`):

```bash
python3 .claude/skills/extract-benchmarks/upload_pending.py \
  var/extract/<slug>/result.csv  "<source-url-or-name>"
```

The script reads `var/supabase.env` (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE`),
sends the `apikey` header (Storage/PostgREST 401s without it), and retries
transient 5xx/429 — same conventions as `publish.py`. It tags every row with the
`source` you pass so a run is identifiable and re-runnable.

**Also store the card for the review iframe.** The review page embeds the
original on the left from a `cards` table (see `supabase/promote.sql`), because
the live source usually refuses framing and Supabase Storage neuters served HTML
(`content-security-policy: default-src 'none'; sandbox`). So for an HTML source,
save the fetched page with a `<base href="<origin>/">` injected after `<head>`
(so its root-relative assets still resolve) and upsert it:

```bash
python3 - "$SOURCE_URL" var/extract/<slug>/page.html <<'PY'
import json, sys, urllib.parse, pathlib
url, html_path = sys.argv[1], sys.argv[2]
origin = "{0.scheme}://{0.netloc}/".format(urllib.parse.urlparse(url))
html = pathlib.Path(html_path).read_text(encoding="utf-8").replace(
    "<head>", '<head><base href="%s">' % origin, 1)
pathlib.Path("/tmp/card.json").write_text(json.dumps({"source": url, "html": html}))
PY
curl -sS -X POST "$SUPABASE_URL/rest/v1/cards" \
  -H "Authorization: Bearer $SERVICE" -H "apikey: $SERVICE" \
  -H "Content-Type: application/json" -H "Prefer: resolution=merge-duplicates,return=minimal" \
  --data-binary @/tmp/card.json
```

(For a PDF source, store/point at the PDF instead — PDFs frame fine.)

## Done

Report: source ingested, # tables and # graphs read, # rows extracted, how many
verify passes it took and what they fixed, any names you appended to the canon
files, and the upload result (row count inserted into `pending`). Don't open a
PR or push unless asked.
