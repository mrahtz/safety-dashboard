# CLAUDE.md — working notes for this repo

Guidance for an AI assistant (or human) working on safety-dashboard. Read this
first; it captures the architecture, the fast dev loop, and the sharp edges.

## What this is

Ingests LLM benchmark/safety numbers from 12 system/model cards (`corpus.py`)
into SQLite, publishes to Supabase, and serves three static dashboards from
`web/`. See `README.md` for the full picture.

**Current data path (shipped):** freeze → screenshot each data table → a VLM
(`claude-sonnet-4-6`, `vlm_table.py`) transcribes each table image to CSV →
numbers stored as `accepted` → `publish.py` pushes to Supabase. Provenance is
**table-level** (the table screenshot + the stored CSV), not per-cell boxes.

## Repo map

- `src/llm_metrics/`
  - `pipeline.py` — orchestration (VLM path). `python -m llm_metrics.pipeline [ids…]`.
  - `vlm_table.py` — table screenshot → CSV (the reader). `transcribe_raw`/`parse_csv`.
  - `extract_html.py` / `extract_pdf.py` — `list_tables()` (screenshots, VLM path)
    **and** the older per-cell structural `extract*()` (used by `app.py`).
  - `crop.py` — PDF rasterize: `render_pdf_section` (table image), `render_pdf_crop` (per-cell).
  - `db.py` / `schema.py` — SQLite; **schema is a FROZEN CHECK-constrained contract**
    (statuses: pending/accepted/rejected/verified/needs_review). Extra per-table
    metadata is merged into `context_json`, never new columns.
  - `ir.py` — FROZEN dataclasses (`Candidate`/`SourceRef`/`Context`); `test_ir` pins fields.
  - `publish.py` — uploads images+CSVs to Storage (deduped, retried) + upserts rows;
    deletes rows beyond the freshly-written set (no orphans).
  - `freeze.py`,`fetch.py`,`corpus.py`,`paths.py` — sources/IO.
  - `app.py` + `serve.sh`, `export_static.py` + `export.sh` — legacy local Flask UI
    and offline export; both use the **structural** reader (+`ocr.py`,`normalize.py`,`serde.py`).
- `web/` — `index.html` (matrix), `sources.html`, `review.html`, `common.js`.
- `scripts/` — `ingest.sh`, `publish.sh`, `dump_tables.py`, `probe_supabase.py`, `serve.sh`, `export.sh`.
- `.github/workflows/` — `refresh` (full), `dev-ingest` (fast, no publish), `probe`, `pages`.
- `supabase/reviews.sql` — the one piece of Supabase DDL (reviews table + RLS).

## Iterate FAST (don't re-run the full pipeline)

The full `refresh` is ~8 min. For iteration:
- **Transcription/parse changes** → run `dev-ingest` on ONE card (Actions →
  dev-ingest → `sources: gpt-5-5`). It dumps each table's CSV to the log, no
  publish. Or locally: `PYTHONPATH=src python3 -m llm_metrics.pipeline gpt-5-5 &&
  python3 scripts/dump_tables.py` (needs `CLAUDE_API_KEY`).
- **Frontend changes** → just edit `web/` and push; `pages.yml` redeploys in ~1 min
  against the data already in Supabase. No pipeline.
- **Supabase key sanity** → run the `probe` workflow (dummy data, ~20s).

Never do a full `refresh` just to test one thing.

## Sharp edges (learned the hard way)

- **CI is ephemeral** — `var/` is gitignored and rebuilt every run; nothing
  persists between runs (no caching yet).
- **Supabase key**: must be a `service_role` JWT or `sb_secret_…` key. Storage
  needs the `apikey` header (not just `Authorization`) or it 401s "Invalid
  Compact JWS". A `sbp_…` personal token does NOT work.
- **Publish replaces everything**: each cell's table image recurs across many
  rows — dedupe uploads by path (done) or you'll do thousands of PUTs and 504.
- **Re-ingest is nondeterministic** (the VLM) — counts/values shift run to run.
- **VLM accuracy**: silently misreads some complex tables (audit: gpt-oss
  "Table 3" 37/38 wrong). For HTML the DOM has exact values — see README "Two
  readers"; moving HTML values to structural is the open recommendation.
- **Always audit by spot check**: regenerate the matrix CSV and/or diff VLM
  values vs the DOM before trusting the data. Two tables is not an audit.

## Conventions

- Don't change `ir.py` / `schema.py` field shapes (frozen; tests enforce). Put new
  per-table data in `context_json`.
- Keep comment/naming density consistent with surrounding code.
- `pytest -q` must stay green (30 tests: ir, db, schema, normalize, fixtures).

## Deploy facts

- Live site: GitHub Pages (custom domain in the repo's Pages settings), e.g.
  `…/index.html`, `…/sources.html`, `…/review.html`.
- Supabase project ref `rapkltwpfvzleejytgmq`; `web/common.js` ships the public
  **anon** key (read-only). Service key lives only in CI secrets.

## Keys & secrets

Five credentials make the pipeline + site go. CI reads them from **GitHub Actions
secrets** (`refresh`/`dev-ingest`/`probe` workflows); `publish.py` reads the
Supabase pair from a gitignored `var/supabase.env` that the workflow writes from
those secrets. Manage CI secrets at
[GitHub → Settings → Secrets → Actions](https://github.com/mrahtz/safety-dashboard/settings/secrets/actions).

| Key | Used for | Where it lives | Get / rotate / generate |
| --- | --- | --- | --- |
| **Anthropic API key** | the VLM reader (`vlm_table.py`, model `claude-sonnet-4-6`). Env var `CLAUDE_API_KEY` (falls back to `ANTHROPIC_API_KEY`). | CI secret `CLAUDE_API_KEY`; locally export it in your shell. | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) (make a separate dev key here too) |
| **Supabase `service_role`** | all writes — PostgREST upserts + Storage crop uploads (`publish.py`). Must be a `service_role` JWT or `sb_secret_…` key. | CI secret `SUPABASE_SERVICE_ROLE`; locally `var/supabase.env`. | [API keys page](https://supabase.com/dashboard/project/rapkltwpfvzleejytgmq/settings/api-keys) ([legacy JWT tab](https://supabase.com/dashboard/project/rapkltwpfvzleejytgmq/settings/api)) |
| **Supabase `anon`** | public read-only key the static site ships to read PostgREST + Storage. | hardcoded in `web/common.js` (safe to publish). | [same API keys page](https://supabase.com/dashboard/project/rapkltwpfvzleejytgmq/settings/api-keys); rotating it means updating `web/common.js` |
| **Supabase URL** | base URL `https://rapkltwpfvzleejytgmq.supabase.co`. Config, not a secret. | CI secret `SUPABASE_URL`; `var/supabase.env`; `web/common.js`. | [project settings](https://supabase.com/dashboard/project/rapkltwpfvzleejytgmq/settings/general) (project ref `rapkltwpfvzleejytgmq`) |
| **Supabase personal token** (`sbp_…`) | the **Management API** only (`api.supabase.com` — run SQL/DDL, e.g. schema migrations). Ad-hoc admin/dev, not used by CI. | not stored; generate per-use, short expiry. | [supabase.com/dashboard/account/tokens](https://supabase.com/dashboard/account/tokens) |

Sharp edges (also see above): a `sbp_…` token does **not** work as a PostgREST/
Storage `apikey` — it only drives the Management API; conversely a
`service_role`/`sb_secret_…` key can't run DDL. Storage calls need the `apikey`
header, not just `Authorization`. The Management API blocks the `python-urllib`
User-Agent (Cloudflare 1010) — POST DDL with `curl` instead.

## Branch / git

The repo's primary branch holds all work. Commit working changes; keep `pytest`
green; only push branches you've been told to. Don't open PRs unless asked.
