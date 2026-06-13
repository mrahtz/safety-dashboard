# safety-dashboard — LLM safety-metrics dashboard

Serves three static dashboards (from `web/`) that explore and review benchmark
and safety numbers extracted from published model/system cards. The data lives
in Supabase; every number is tied back to the source card and table it came from.

**Live site:** http://amid.fish/safety-dashboard/

## How it works

- **Pages** (`web/`) — `index.html` (model × benchmark matrix), `sources.html`
  (numbers grouped by source), and `review.html` (reviewer sign-off). All three
  are fully static and read from Supabase with a public read-only anon key;
  shared helpers live in `web/common.js`.
- **Data** — two Postgres tables, `sources` and `metrics` (DDL in
  `supabase/metrics.sql`). Each `metrics` row lands with `accepted = false`; a
  reviewer flips it to `true` in `review.html` and the row then shows on the
  dashboard. No staging or pipeline.
- **Ingestion** — human-driven via the `extract-benchmarks` skill
  (`.claude/skills/extract-benchmarks/`): point it at a card PDF (or a web page
  to print to PDF), it extracts every benchmark result, normalizes names, uploads
  per-page images to Supabase Storage, and inserts rows to `metrics`.
- **Deploy** — `.github/workflows/pages.yml` publishes `web/` to GitHub Pages on
  every push to `main`.

## More detail

See [`CLAUDE.md`](./CLAUDE.md) for the full working notes: the repo map, the data
path, deploy facts, auth/RLS setup, and the keys/secrets reference. It is the
authoritative description of how this repo is built and operated.
