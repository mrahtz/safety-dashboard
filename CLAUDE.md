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
  - `vlm_table.py` — table screenshot → CSV (the reader). `transcribe_raw`/`parse_long`/`parse_csv`.
  - `extract_html.py` — `list_tables()`: screenshot each HTML data table (runs Playwright
    in a subprocess via `runner.run_html_tables`).
  - `extract_pdf.py` — `list_pages()`: rasterize every PDF page (page-at-a-time VLM path).
  - `crop.py` — PDF page rasterize: `render_pdf_page` (whole-page image).
  - `db.py` / `schema.py` — SQLite; **schema is a FROZEN CHECK-constrained contract**
    (statuses: pending/accepted/rejected/verified/needs_review). Extra per-table
    metadata is merged into `context_json`, never new columns.
  - `publish.py` — uploads images+CSVs to Storage (deduped, retried) + upserts rows;
    deletes rows beyond the freshly-written set (no orphans).
  - `freeze.py`,`fetch.py`,`corpus.py`,`paths.py` — sources/IO.
- `web/` — `index.html` (matrix), `sources.html`, `review.html`, `common.js`.
- `scripts/` — `ingest.sh`, `publish.sh`, `dump_tables.py`, `probe_supabase.py`.
- `.github/workflows/` — `refresh` (full), `dev-ingest` (fast, no publish), `probe`, `pages`.
- `supabase/` — Supabase DDL: `reviews.sql` (reviews table + RLS) and
  `promote.sql` (review-page "Accept → main" policies on metrics/pending + the
  `cards` table that backs the embeddable source iframe).

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
  "Table 3" 37/38 wrong). For HTML the DOM has exact values, so reading HTML
  values structurally (and keeping the VLM for PDFs) is the open recommendation.
- **Always audit by spot check**: regenerate the matrix CSV and/or diff VLM
  values vs the DOM before trusting the data. Two tables is not an audit.

## Conventions

- Don't change `schema.py` field shapes (frozen; tests enforce). Put new
  per-table data in `context_json`.
- Keep comment/naming density consistent with surrounding code.
- `pytest -q` must stay green (15 tests: db, schema, extract_pdf_pages, fixtures).

## Deploy facts

- Live site: GitHub Pages (custom domain in the repo's Pages settings), e.g.
  `…/index.html`, `…/sources.html`, `…/review.html`.
- Supabase project ref `rapkltwpfvzleejytgmq`; `web/common.js` ships the public
  **anon** key (read-only). Service key lives only in CI secrets.
- Live site is the custom domain **`http://amid.fish/safety-dashboard/`** (not the
  `github.io` URL — that 301s here).
- **Pages deploys from `main`.** `pages.yml` deploys `web/` on push to `main`,
  and the **`github-pages` environment now allows `main`** (it was added to the
  deployment-branch policy — confirmed by successful `main` deploys from 2026-06-08
  on). So the path to live is simply: land your `web/` change on `main` and the
  push triggers the deploy. (The env still also permits the legacy
  `claude/llm-metrics-ingestion-frdmg` ref.) Note a deploy from a *disallowed*
  branch is **rejected in ~2s with zero steps run** — that looks like a failure,
  not a block, so if a deploy mysteriously no-ops, check the branch first.
- **ALWAYS check the site after any deploy.** A green Actions run is NOT proof the
  site works — env policy, schema drift, and CDN cache have all bitten us. After
  every Pages deploy (or Supabase change), run
  `.claude/skills/check-site/await_deploy.sh` — it waits (bounded) for the new build
  to land, then runs `check_site.py` (headless browser; flags console errors, failed
  requests, visible load-error/empty states; screenshots to `/tmp/site-check/`). Don't
  report a deploy as done until this is green. A 404 on `/rest/v1/reviews` is expected
  and ignored.
- **Cloudflare edge cache + cache-busting.** `amid.fish` is proxied through the
  owner's **Cloudflare** (not part of Pages — Pages itself is Fastly). Cloudflare
  edge-caches `*.js` with a multi-hour TTL (`cf-cache-status: HIT`) but passes HTML
  through (`DYNAMIC`). So after a deploy the HTML is fresh but `common.js` is stale
  for hours. We defeat this **in-repo**: the three pages load `common.js?v=NNN`, and
  the versioned URL is a fresh cache key → served from origin immediately. **When you
  change `common.js`, bump the `?v=` token in `index.html`, `sources.html`, and
  `review.html`** (a date like `20260608`, +letter for a second change that day), or
  the change won't reach users. The `github.io` URL is not an escape hatch — Pages
  301-redirects it to the custom domain. (Alternatives if ever needed: purge
  Cloudflare via API, or drop the custom domain to serve straight from github.io.)

## Auth (review page)

`review.html` is reviewer-only: it reads the staged `pending` table behind a
Supabase **email magic-link** login (`signInWithOtp`, `shouldCreateUser:false`).
Who can get in is governed by **Supabase Auth project config**, not the code:

- **Signups are disabled** (`disable_signup: true`) and there is a single allowed
  user, `matthew.rahtz@gmail.com`. New emails can't self-register; add reviewers
  in Dashboard → Authentication → Users (and to the allowlist below).
- **Defense in depth:** the `pending` RLS read policy also gates on the email
  (`lower(auth.jwt()->>'email') = 'matthew.rahtz@gmail.com'`), so even an
  authenticated session from any other account reads nothing. Add emails to that
  policy (`supabase/`… or the skill's `pending_table.sql`) to grant access.
- **Magic-link redirect** must point at the live page or the link breaks (it
  defaulted to `http://localhost:3000`). Fixed via Auth config:
  `site_url = http://amid.fish/safety-dashboard/review.html` and
  `uri_allow_list = http://amid.fish/safety-dashboard/**,https://amid.fish/safety-dashboard/**,http://localhost:3000/**`.
  The app passes `emailRedirectTo = origin+pathname`; it's honoured only if it
  matches the allow-list, else Supabase falls back to `site_url`.
- These live in **Auth config**, settable via the Management API
  (`PATCH /v1/projects/<ref>/config/auth`, needs an `sbp_…` token) or
  Dashboard → Authentication → URL Configuration / Providers.

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
