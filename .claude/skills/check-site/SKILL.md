---
name: check-site
description: Smoke-check the live LLM safety-metrics dashboard (the GitHub Pages site at amid.fish/safety-dashboard) with a headless browser. Use when the user reports "the site is broken / erroring / blank", after deploying frontend or Supabase changes, or to verify a fix reached production. Loads each page and reports console errors, uncaught exceptions, failed network requests (e.g. Supabase 404s), and visible "load failed" / empty states.
---

# Checking the main site

The three dashboard pages (`index.html`, `sources.html`, `review.html`) are static
and read their data at runtime from Supabase (PostgREST + Storage) using the public
anon key. So "the site errors" almost always means a **runtime** problem — a renamed/
missing table, an RLS change, a bad anon key, or a stale Pages deploy — not a build
failure. This skill drives a real browser against production and surfaces exactly
what's failing.

## Run it

```bash
python3 .claude/skills/check-site/check_site.py            # prod: http://amid.fish/safety-dashboard/
python3 .claude/skills/check-site/check_site.py <BASE_URL> # any deploy/preview, or a file:// dir
```

Exit code is non-zero if any issue is found. Full-page screenshots of every page are
written to `/tmp/site-check/` — read them (they're PNGs) to see the actual render.

### After a deploy: wait for it to go live, then check

```bash
.claude/skills/check-site/await_deploy.sh [VERSION]   # VERSION defaults to the ?v= in web/index.html
```

Watches the **uncached** `index.html` for the cache-bust token it references
(`common.js?v=NNN`) — that flips the moment the new build lands (Cloudflare passes
HTML through but edge-caches the JS), then auto-runs `check_site.py`. It is
**bounded** — on timeout it prints a diagnostic (most often the deploy *failed*, not
"slow": the `github-pages` environment blocked the branch — see CLAUDE.md) instead of
spinning forever. Prefer this over an ad-hoc `curl | grep` poll loop. Remember to bump
the `?v=` token in all three pages when `common.js` changes (see CLAUDE.md).

## What it catches

- **Visible error / empty state** — the page printed `Load failed: …`, `No cells`, or
  `No data` instead of the matrix/table.
- **Failed requests** — any 4xx/5xx (favicon ignored). The usual smoking gun is a
  Supabase REST 404 like `…/rest/v1/<table>?…` → the frontend and DB schema disagree.
- **Console errors** and **uncaught exceptions** — JS blew up.

## Interpreting results

- `Could not find the table 'public.<x>'` (PGRST205) → the deployed `web/common.js`
  queries a table the DB no longer has. Either the schema changed or the Pages deploy
  is stale. Check the deployed file: `curl -sSL http://amid.fish/safety-dashboard/common.js`.
- A **404 on `/rest/v1/reviews`** is expected and tolerated — the reviews table is
  optional; `loadReviews()` swallows it. Not a real failure.
- Empty **crop thumbnails** are expected when rows were loaded "figures only" (no
  Storage upload); `crop_url` is blank by design until a full CI publish runs.

## Gotchas

- This sandboxed runner does TLS interception, so without help every HTTPS fetch fails
  with `ERR_CERT_AUTHORITY_INVALID` and masks the real error. The script already
  launches with `--ignore-certificate-errors` + `ignore_https_errors` to see through it.
- Pages deploys (`.github/workflows/pages.yml`) only publish `web/`; data lives in
  Supabase. If the HTML/JS is right but there's no data, run/inspect the `refresh`
  workflow or the Supabase tables instead.
- The browser binary is auto-located under `/opt/pw-browsers/`; if Playwright isn't
  installed in the environment, `pip install playwright` first.
