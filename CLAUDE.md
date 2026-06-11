# CLAUDE.md — working notes for this repo

Guidance for an AI assistant (or human) working on safety-dashboard.

## What this is

Serves three static dashboards from `web/` backed by Supabase. Benchmark data
enters via the `extract-benchmarks` skill (human-driven, not automated).

**Data path:** use `/extract-benchmarks` skill with a model/system card URL or
PDF → extracts every benchmark result → upserts a `sources` row then inserts
`metrics` rows with `accepted = false` → reviewer accepts each table in
`review.html` (flips `accepted = true` directly on the rows) → trusted rows
show on the dashboard. Two tables, one boolean, no staging.

## Repo map

- `web/` — `index.html` (matrix), `sources.html`, `review.html`, `common.js`.
- `supabase/` — Supabase DDL:
  - `metrics.sql` — canonical schema for `sources` + `metrics` tables.
  - `promote.sql` — reviewer UPDATE policy on `metrics`.
- `.github/workflows/pages.yml` — deploys `web/` to GitHub Pages on push to `main`.
- `.claude/skills/extract-benchmarks/` — the ingestion skill (standalone, no pipeline deps).
- `.claude/skills/check-site/` — headless smoke-check of the live site.

## Adding data

Run the `/extract-benchmarks` skill. It will:
1. Check for `var/supabase.env`; if missing, ask for credentials and write it.
2. Extract every benchmark number from the source into a normalized CSV.
3. Verify the CSV in a double-check loop.
4. Upload to the `metrics` table with `accepted = false`.

Then sign tables off in `review.html`; trusted rows appear on the dashboard.

## Frontend changes

Edit `web/` and push to `main`; `pages.yml` redeploys in ~1 min. No pipeline.

**Cache-busting:** `amid.fish` is behind Cloudflare, which caches `*.js` for
hours. When you change `common.js`, bump the `?v=` token in `index.html`,
`sources.html`, and `review.html` (use a date like `20260609`, +letter for a
second change that day). HTML is not cached (`DYNAMIC`).

**Always check the site after any deploy** — run
`.claude/skills/check-site/await_deploy.sh` which waits for the build and then
runs a headless browser check. A green Actions run is not proof the site works.

## Deploy facts

- Live site: **`http://amid.fish/safety-dashboard/`** (custom domain; `github.io`
  301s here).
- Supabase project ref `rapkltwpfvzleejytgmq`; `web/common.js` ships the public
  **anon** key (read-only).
- **Pages deploys from `main`** (`github-pages` environment allows `main`). A
  deploy from a disallowed branch is rejected in ~2s with zero steps — if a
  deploy no-ops, check the branch.

## Auth (review page)

`review.html` is reviewer-only via Supabase email magic-link login.

- Signups disabled (`disable_signup: true`). Single allowed user:
  `matthew.rahtz@gmail.com`. Add reviewers in Dashboard → Authentication → Users.
- RLS on `sources` allows anon + authenticated read (set in `supabase/metrics.sql`).
  Add new reviewer emails to the `metrics_update` policy in `supabase/promote.sql`.
- Magic-link redirect config: `site_url = http://amid.fish/safety-dashboard/review.html`,
  `uri_allow_list` includes `http://amid.fish/safety-dashboard/**` and
  `https://amid.fish/safety-dashboard/**`. Settable via Management API
  (`PATCH /v1/projects/<ref>/config/auth`, needs an `sbp_…` token) or
  Dashboard → Authentication → URL Configuration.

## Keys & secrets

| Key | Used for | Where it lives |
| --- | --- | --- |
| **Supabase `service_role`** | all writes — PostgREST inserts (`upload_metrics.py`). Must be a `service_role` JWT or `sb_secret_…` key. | `var/supabase.env` (gitignored; the skill writes it on first use). |
| **Supabase `anon`** | public read-only key shipped in `web/common.js`. | hardcoded in `web/common.js` (safe to publish). |
| **Supabase URL** | `https://rapkltwpfvzleejytgmq.supabase.co`. | `var/supabase.env`; `web/common.js`. |
| **Supabase personal token** (`sbp_…`) | Management API only — run DDL/migrations via `curl`. Ad-hoc, generate per-use. | not stored; generate at supabase.com/dashboard/account/tokens. |

Sharp edges: `sbp_…` tokens don't work as a PostgREST `apikey` (Management API
only). `service_role`/`sb_secret_…` keys can't run DDL. PostgREST needs the
`apikey` header, not just `Authorization`. Management API blocks `python-urllib`
UA (Cloudflare 1010) — POST DDL with `curl` instead.

DDL (CREATE/ALTER/DROP TABLE, CREATE POLICY, etc.) changes the schema and must
go through the Management API with the `sbp_…` token. DML (INSERT/UPDATE/DELETE/
SELECT) reads and writes rows and uses the `service_role` key via PostgREST.
The two are not interchangeable.

## Branch / git

Push to `main`. Don't open PRs unless asked.
