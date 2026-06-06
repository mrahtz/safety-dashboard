# safety-dashboard — LLM-metrics ingestion with screenshot provenance

A pipeline that extracts LLM benchmark/safety numbers from published system cards
into a database, and a dashboard to review them. The one load-bearing property:
**every stored number is provable.** Each number is bound to a *bounding box* in
the source's own coordinate system, so its screenshot is a tight crop of exactly
that spot — provenance by construction, not by luck.

Design in one line: a deterministic **structural extractor** is the source of
truth (it gives real coordinates); an independent **VLM** only cross-checks the
number it reads (it never produces the record). A number is `verified` only when
both agree *and* the value is found in the OCR text of its own crop.

## What works end to end

| Stage | What it does | Module(s) |
|------|---------------|-----------|
| Contracts | Frozen IR + DB schema everything codes against | `ir.py`, `schema.py` |
| P1 HTML | Playwright reads DOM cells + `getBoundingClientRect`, renders tight highlighted crops | `extract_html.py` |
| P2 PDF | `pdfplumber` cells + PyMuPDF rasterize/crop — identical IR | `extract_pdf.py`, `crop.py` |
| P3 freeze+DB | Content-addressed freezer + SQLite persistence + accept/reject UI | `freeze.py`, `db.py`, `app.py` |
| P4 VLM verify | Independent VLM read + normalize-compare + OCR-presence check | `vlm.py`, `verify.py`, `ocr.py` |
| P5 dashboard | Render-from-DB dashboard; status filter so only verified rows show | `app.py`, `export_static.py` |
| P6a normalize | Strip %/marks, parse typed float, record precision | `normalize.py` |

Orchestration (`pipeline.py`) freezes → extracts → persists → verifies each card
in `corpus.py`. Failures degrade gracefully: a source it can't parse is skipped,
a number it can't auto-verify becomes `needs_review` — it never writes a guessed
record (brief §9).

## Corpus

12 real system/model cards from two families discovered at build time:
- **OpenAI Deployment Safety Hub** (HTML): gpt-5.5, gpt-5.2, gpt-5.1, gpt-5, o3, sora-2, gpt-oss.
- **Google DeepMind model cards** (PDF): Gemini 3 Pro, 3.1 Pro, 2.5 Pro, 2.5 Flash, 2.0 Flash.

## Run it

```bash
pip install flask pymupdf pdfplumber playwright pytesseract markupsafe
python -m playwright install chromium
apt-get install -y tesseract-ocr
export CLAUDE_API_KEY=sk-ant-...        # for the VLM verifier (P4)

./scripts/ingest.sh        # freeze + extract + verify the whole corpus -> var/metrics.sqlite
./scripts/serve.sh         # interactive UI at http://localhost:8000  (Dashboard / Review / Extract)
./scripts/export.sh        # self-contained offline dashboard -> var/export/index.html
pytest -q                  # contract + pipeline self-tests
```

The **offline export** (`var/export/`) is a single `index.html` plus a `crops/`
folder, fully self-contained: open it in any browser with no server and no
network. Filter by status, search, sort, and hover any crop to enlarge it.

## Tests

`pytest` covers the frozen contracts, normalization, persistence/idempotency, and
the verification protocol — including the P4 case that a **deliberately wrong
bounding box is caught by the OCR-presence check**.

## Live deployment (GitHub Pages + Supabase)

The dashboard can run as a static page backed by an external DB instead of a
baked export:

- **Postgres (Supabase)** holds `sources`/`candidates`; **Storage** holds the
  crops (public bucket, keyed by sha256 — immutable, deduplicated provenance).
- **RLS**: anonymous users get read-only access; status changes require an
  authenticated reviewer. The frontend ships only the public **anon** key.
- `scripts/publish.sh` (`llm_metrics/publish.py`) pushes the local SQLite +
  crops up: it uploads each crop to Storage and upserts the rows via PostgREST,
  rewriting `crop_path` → public `crop_url`. Needs `var/supabase.env` (gitignored)
  with `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE`.
- `web/index.html` is the static frontend: it reads candidates from PostgREST
  and crops from Storage entirely client-side. Open it directly, or serve it on
  GitHub Pages (point Pages at the repo / `web/`). The ingest runs in CI or
  locally and publishes; Pages just serves the static file.

The split: **ingest is a backend batch job** (Actions/local, holds the secrets);
**serving is static** (Pages, anon read-only). Re-run `scripts/ingest.sh` then
`scripts/publish.sh` to refresh.

## Known limits

- HTML crops render from the **live page** (faithful layout); the raw bytes are
  still frozen by sha256 for provenance/diffing. Freezing a full HTML asset
  bundle is out of scope for this milestone.
- Image-only PDF tables (no text layer) are out of scope (brief §3.3) — e.g. the
  Gemini capabilities table on page 5, which is a figure.
- This managed environment firewalls all egress except TLS/443, so a public
  Cloudflare tunnel (edge port 7844) cannot be established here; the dashboard is
  delivered as the offline export above and runs locally via `scripts/serve.sh`.
