# safety-dashboard — LLM-metrics ingestion with screenshot provenance

Extracts LLM benchmark/safety numbers from published system/model cards into a
database and serves three static dashboards to explore and review them. Every
number is tied to **the screenshot of the table it came from**, so each value is
traceable back to its source.

## How it works (current pipeline: VLM transcription)

```
corpus → freeze → screenshot each data table → VLM transcribes table → numbers → publish → static pages
```

For each card (`corpus.py`):
1. **Freeze** (`freeze.py`) — fetch the bytes, hash to sha256, snapshot to `var/blobs/`.
2. **Screenshot every data table** — Playwright for the OpenAI HTML pages
   (`extract_html.list_tables`), `pdfplumber`/PyMuPDF for the Gemini PDFs
   (`extract_pdf.list_tables`). One image per table (≥3 numeric cells).
3. **Transcribe** (`vlm_table.py`) — each table image is sent to Claude
   (`claude-sonnet-4-6`) and returned as CSV. Every numeric cell becomes a
   candidate `(column_header, row_label, value)`; the raw CSV (the table's
   layout) is saved too.
4. **Persist** (`db.py`, schema in `schema.py`) — candidates land in SQLite with
   status `accepted` (machine-read, not yet human-reviewed). Provenance =
   the whole-table screenshot + the stored CSV.
5. **Publish** (`publish.py`) — uploads the table screenshots + table CSVs to
   Supabase Storage (content-addressed, deduped) and upserts rows via PostgREST.

**Provenance is table-level**: a number links to the table image the model read
and the CSV it produced, not to a per-cell bounding box.

> **Accuracy caveat (measured).** The VLM is accurate on most tables but
> *silently misreads some complex ones*. A cell-level audit against the HTML DOM
> found ~95% exact but **46 wrong cells concentrated in a few tables** (e.g.
> gpt-oss "Table 3" — 37/38 wrong, column misalignment). For HTML we have exact
> values for free in the DOM, so the recommended next step is to take HTML values
> structurally and keep the VLM for PDFs (see "Two readers" below). Run the audit
> with `/tmp`-style scripts or the presence/cell checks in the git history.

## The three pages (`web/`, served by GitHub Pages)

1. **`index.html` — model × benchmark matrix.** Rows = models (`column_header`),
   columns = benchmarks (`row_label`), qualified by source+table so distinct
   metric tables don't collapse. Junk axes/values are filtered client-side. Click
   a cell to see every source value + the table screenshot.
2. **`sources.html` — numbers by source.** Pick a card; each table is re-rendered
   **in its original layout** from the stored CSV, with the source screenshot one
   click away.
3. **`review.html` — review one table at a time.** Sign in (Supabase Auth magic
   link) and accept/reject a whole table. Decisions are keyed by a stable
   `table_key = origin_url || '::' || section_key` in a separate `reviews` table,
   so re-ingest never wipes them; the sign-off badge overlays the other pages.

Table identification uses `section_key` (e.g., `p0_t0` for page 0 table 0) to
persist across re-ingests. Review decisions are stored in a separate `reviews` table
keyed by `table_key = origin_url || '::' || section_key`.

## Corpus

12 cards from two families:
- **OpenAI Deployment Safety Hub** (HTML): gpt-5.5, gpt-5.2, gpt-5.1, gpt-5, o3, sora-2, gpt-oss.
- **Google DeepMind model cards** (PDF): Gemini 3 Pro, 3.1 Pro, 2.5 Pro, 2.5 Flash, 2.0 Flash.

## Run it

```bash
pip install playwright pdfplumber pymupdf Pillow markupsafe   # ingest deps (no OCR needed)
python -m playwright install chromium
export CLAUDE_API_KEY=sk-ant-...                              # the table reader

./scripts/ingest.sh                       # whole corpus -> var/metrics.sqlite
python3 -m llm_metrics.pipeline gpt-5-5   # OR just one/two cards (fast)
python3 scripts/dump_tables.py            # print the transcribed CSVs to eyeball
./scripts/publish.sh                      # push to Supabase (needs var/supabase.env)
pytest -q                                 # contract + persistence self-tests
```

`var/supabase.env` (gitignored) needs `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE`
(a `service_role` JWT or a new `sb_secret_…` key — both work; a `sbp_…` personal
token does **not**).

## CI / deployment

- **`.github/workflows/refresh.yml`** — full re-ingest + publish (weekly + manual).
- **`.github/workflows/dev.yml`** (dev-ingest) — fast loop: ingest one/two cards, dump
  transcriptions to the log, **no publish**. Use this to iterate.
- **`.github/workflows/probe.yml`** — checks the Supabase key works (Storage +
  PostgREST) with dummy data, no ingest.
- **`.github/workflows/pages.yml`** — deploys `web/` to GitHub Pages.

Secrets required: `CLAUDE_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE`.

### Review setup (one-time)
- Run `supabase/reviews.sql` in the Supabase SQL editor (creates `reviews` + RLS:
  anon read, authenticated write).
- Supabase → Authentication: enable Email/magic-link and add the deployed
  `…/review.html` URL to the redirect allowlist.

