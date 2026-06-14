# CLAUDE.md — working notes for this repo

Guidance for an AI assistant (or human) working on safety-dashboard.

## What this is

Serves three static dashboards from `web/` backed by Supabase. Benchmark data
enters via the `extract-benchmarks` skill (human-driven, not automated).

**Data path:** use `/extract-benchmarks` skill with a model/system card **PDF**
— **PDF files only, web pages are not supported** → extracts every benchmark
result, stores per-page PNG images in Supabase Storage → upserts a `sources`
row then inserts `metrics` rows with `accepted = false` → reviewer accepts each
table in `review.html` (flips `accepted = true` directly on the rows) → trusted
rows show on the dashboard. Two tables, one boolean, no staging.

## Repo map

- `web/` — `index.html` (matrix), `sources.html`, `review.html`, `db-state.html`, `common.js`.
  - `review-mobile-test.html` — standalone mobile/iPad layout mockup (not linked
    from nav; reach by direct URL). The side-by-side card layout (pinch-zoom page
    image left, scrollable extracted tables right) also ships in `review.html`.
    Each PDF page image is pinch-zoom/pan via the **panzoom** lib. Learnings:
    - pinch-zoom is a *top-level visual-viewport* gesture, so it can't be scoped
      to one region with CSS or an `<iframe>` (an iframe isolates scroll/pan, not
      zoom) — it must be intercepted in JS and applied as a transform to the element.
    - **Do NOT put `will-change: transform` on the zoomed `<img>`.** It pins the
      image to a GPU layer rasterized once at its small on-screen size (~the panel
      width), so panzoom's `transform: scale()` just stretches that low-res bitmap
      → **blurry zoom on iPad/WebKit, regardless of source image DPI** (Chromium
      hides this by re-rasterizing; WebKit does not). Without `will-change` the
      browser re-rasterizes from the full-res source as you zoom. Corollary:
      raising the page-image render DPI does nothing for zoom sharpness until this
      is fixed — the extra pixels are discarded before the transform. (We render
      pages at 300 DPI, see the extract-benchmarks skill; that only pays off once
      the zoom path actually samples the source.)
- `supabase/` — Supabase DDL:
  - `metrics.sql` — canonical schema for `sources` + `metrics` tables. `condition`
    = how the *model* was run (reasoning effort, tools, thinking, safeguards,
    attempts, sampling/scoring like Pass@1/5-shot, training variant); `subset` =
    which *slice of the benchmark* was measured (language, language family,
    difficulty, topic/harm category, named split, context length, …). The two were
    split out of a single `condition` column on 2026-06-14 — see
    `condition_subset_mapping.csv` (the applied classification) and
    `condition_premigration_backup.json` (id → original `condition`, for revert).
    The `extract-benchmarks` skill populates `subset` on new extractions (its CSV
    has a `subset` column); `upload_metrics.py` tolerates older CSVs without it.
  - `promote.sql` — reviewer UPDATE policy on `metrics`.
- `.github/workflows/pages.yml` — deploys `web/` to GitHub Pages on push to `main`.
- `.claude/skills/extract-benchmarks/` — the ingestion skill (standalone, no pipeline deps).
- `.claude/skills/check-site/` — headless smoke-check of the live site.

## Review page — hard requirements (do not regress)

`review.html` exists so a reviewer can **check each extracted number against the
actual PDF page it came from**. These are load-bearing invariants — a green
deploy that violates any of them is a regression even if nothing errors. A
known-good snapshot is the commit that added this section on `main`
(2026-06-13), tagged locally as **`review-known-good-2026-06-13`**; diff against
it if a change feels off. (The tag was created in a web session whose git proxy
blocks `refs/tags/*` pushes, so it may not be on the remote — publish it with
`git push origin review-known-good-2026-06-13` from a local clone.)

1. **Page and numbers visible at the same time.** The page image and its
   extracted numbers must be on screen together — no tab-switch, modal, or
   separate view that shows one without the other. Side-by-side comparison is
   the whole point.
2. **Two columns: page left, numbers right.** Left column = the PDF page
   image(s); right column = the extracted tables/figures for that page. Don't
   collapse to a single stacked column.
3. **Works at iPad mini portrait (~768px wide).** The layout is built for a
   tablet/mobile viewport, not just desktop. Verify at iPad mini size, not only
   at a wide window — a change that only looks right on desktop is not done.
4. **Page images are pinch-zoomable.** Each PDF page image is pinch-zoom/pan via
   **panzoom**, scoped to its own panel. Do **not** add `will-change: transform`
   to the zoomed `<img>` (blurs zoom on iPad/WebKit — see the `web/` repo-map
   note above for the full reasoning).
5. **Every PDF page shows, including pages with no tables/figures.** Drive the
   page list from `sources.num_pages_total` (walk `1..N`), not just the pages
   that have `metrics` rows. Pages with no extracted content still render their
   page image with an empty results panel, so nothing is silently hidden.
6. **Sign-off works and persists.** The point of the page is review: the reviewer
   must be able to accept (and un-accept) each table/figure, and the change must
   persist to `metrics.accepted` in Supabase — accepted rows then show on the
   dashboard. The viewing layout exists to serve this action; if accept doesn't
   stick, the page is broken regardless of how it looks.
7. **Reviewer-only auth gates the writes.** Accepting requires the Supabase
   magic-link login (signups disabled; only allowed reviewer emails — see the
   Auth section). Anonymous visitors can read but must not be able to flip
   `accepted`; the RLS `metrics_update` policy enforces this.
8. **The numbers shown beside a page are the ones extracted from that page.**
   Right-column tables/figures must be grouped by their own `page_num` and lined
   up next to the matching left-column page image, with pages in `1..N` order —
   never numbers from page X sitting beside the image of page Y.

When editing `review.html`, re-confirm all of these before pushing (the
`check-site` skill's authenticated review check covers login + the render +
two-column layout; eyeball the iPad-mini viewport, pinch-zoom, and an actual
accept round-trip manually).

## Adding data

Run the `/extract-benchmarks` skill. It will:
1. Check for `var/supabase.env`; if missing, ask for credentials and write it.
2. Download the PDF, rasterize every page to a PNG with `pdftoppm`, and read every page image.
3. Extract every benchmark number from the page images into a normalized CSV.
4. Verify the CSV in a double-check loop.
5. Upload: upsert the `sources` row, insert all `metrics` rows (`accepted=false`), upload page PNGs to the `page-images` Storage bucket.

Then sign tables off in `review.html` (which shows each PDF page image beside its extracted tables/figures); trusted rows appear on the dashboard.

**GOTCHA — never drop-and-reinsert `metrics` rows to correct a source.** The
reviewer's sign-off lives in `metrics.accepted` on the existing rows. Deleting a
source's rows and re-inserting from a fixed CSV (or just re-running
`upload_metrics.py`, which only *inserts*) creates fresh rows with
`accepted=false` — silently **wiping every accept a reviewer already made**.
`sources` upserts by `origin_url`, but `metrics` does not. To fix already-uploaded
values, **`PATCH` the specific cells in place** (match on `source_id` +
`section_key` [+ `row_idx`/`col_idx`], `value`/`condition`/etc.) via PostgREST so
`accepted` is preserved. Only delete+reinsert when the section's row structure
itself changed and you've confirmed with the reviewer that re-review is acceptable.

## Frontend changes

Edit `web/` and push to `main`; `pages.yml` redeploys in ~1 min. No pipeline.

**Cache-busting:** `amid.fish` is behind Cloudflare, which caches `*.js` for
hours. When you change `common.js`, bump the `?v=` token in `index.html`,
`sources.html`, `review.html`, and `db-state.html` (use a date like `20260609`,
+letter for a second change that day). HTML is not cached (`DYNAMIC`).

**Always check the site after any deploy** — run
`.claude/skills/check-site/await_deploy.sh` which waits for the build and then
runs a headless browser check. A green Actions run is not proof the site works.

`await_deploy.sh` decides the deploy is "live" by watching `index.html` for the
`common.js?v=` token, so it only fires for deploys that bump that token. To verify
a standalone page that doesn't touch `common.js` (e.g. `review-mobile-test.html`),
poll that page's own HTML for a unique marker instead — HTML is uncached
(`DYNAMIC`), so `curl … | grep <marker>` flips the moment the new build lands.

**Marker must fit on ONE line.** `grep` matches line-by-line, so a marker that
wraps across a newline in the served HTML never matches — the poll will "time
out" forever even though the deploy succeeded in ~20s. Pick a short marker you can
confirm sits on a single line (`grep -c <marker>` on the local file should return
1), or use `grep -z`. A poll timeout almost always means a bad marker, not a
failed deploy — check the Actions run / `curl` the page before assuming infra.

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
| **Supabase `service_role`** | all writes — PostgREST inserts (`upload_metrics.py`). Must be a `service_role` JWT or `sb_secret_…` key. | `var/supabase.env` (gitignored; the skill writes it on first use). In the remote Claude Code environment also available as the `SUPABASE_SERVICE_ROLE_KEY` env var. |
| **Supabase `anon`** | public read-only key shipped in `web/common.js`. | hardcoded in `web/common.js` (safe to publish). |
| **Supabase URL** | `https://rapkltwpfvzleejytgmq.supabase.co`. | `var/supabase.env`; `web/common.js`. In the remote Claude Code environment also available as the `SUPABASE_URL` env var. |
| **Supabase personal token** (`sbp_…`) | Management API only — run DDL/migrations via `curl`. | In the remote Claude Code environment this is available as `SUPABASE_ACCESS_TOKEN` env var. Otherwise generate at supabase.com/dashboard/account/tokens. |

In the remote Claude Code environment, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
and `SUPABASE_ACCESS_TOKEN` are present as env vars, so a fresh container needs no
`var/supabase.env` written by hand. The `check-site` skill reads its Supabase creds
**only** from `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` in the environment.
(Note the env var is `SUPABASE_SERVICE_ROLE_KEY`; `var/supabase.env` and the upload
scripts use the name `SUPABASE_SERVICE_ROLE` — same value, different name.)

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

**Always finish on `main`.** When you're done with a task, merge your work to
`main` and push it — that's what triggers the Pages deploy. Don't leave the
change stranded on a feature branch: a branch that only lives on `origin` never
deploys (`pages.yml` runs on push to `main` only). If you developed on a feature
branch, merge it into `main` and `git push origin main` before calling the task
done, then verify the deploy with `check-site` (see "Frontend changes").
