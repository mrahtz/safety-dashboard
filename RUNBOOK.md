# Runbook

## What this is

An end-to-end pipeline that pulls LLM safety/benchmark numbers out of published
system cards and into a reviewable dashboard, where **every number is bound to a
bounding box** in its source and shown next to a tight, highlighted crop of
exactly that spot. Structural extraction is the source of truth; an independent
VLM read cross-checks it; an OCR-presence check guards against wrong-region
bounding boxes.

## Two-minute demo (offline)

The delivered `safety-dashboard-offline.zip` is self-contained:

1. Unzip it.
2. Open `index.html` in any browser — no server, no network.
3. KPIs show 473 numbers across 12 system cards. Use the **filter** buttons
   (verified+accepted / all / needs_review), the **search** box, click a column
   header to **sort**, and **hover any crop** to enlarge it and see the red box
   framing the exact number.

Spot-check provenance: pick any row, hover its crop, and confirm the red box
frames the number in the `raw` column. The `0.868*` row (GPT-5.5, "hate") is a
good one — the footnote mark is captured and the crop proves where it came from.

## Run it yourself

```bash
pip install flask pymupdf pdfplumber playwright pytesseract markupsafe
python -m playwright install chromium && apt-get install -y tesseract-ocr
./scripts/ingest.sh     # freeze + extract + verify all 12 cards -> var/metrics.sqlite
./scripts/export.sh     # regenerate var/export/index.html (the offline dashboard)
./scripts/serve.sh      # live UI at http://localhost:8000 : Dashboard / Review / Extract
pytest -q               # 36 contract + pipeline tests
```

`scripts/ingest.sh` runs the whole corpus in one process. To reproduce the
build exactly (one short, isolated process per card — robust on constrained
hosts), loop `python -m llm_metrics.pipeline <model_id>` over the ids in
`corpus.py`.

## What each prototype delivers

- **P1 HTML / P2 PDF** — paste a URL/PDF on the *Extract* page; get each numeric
  cell beside its highlighted crop, its context, and the OCR-presence check.
- **P3** — `freeze.py` snapshots each source by sha256; `db.py` persists
  candidates; the *Review* page accepts/rejects and logs to `attempts`.
- **P4** — `vlm.py` + `verify.py`: an independent VLM reads one crop and the
  result is compared to the structural value after normalization, plus the
  OCR-presence check. Agreement -> `verified`.
- **P5** — the *Dashboard* / static export renders only trustworthy rows by
  default, each cell linking to its crop.
- **P6a** — `normalize.py` strips %/marks and parses a typed float.

## Important caveat on verification status (read this)

The VLM cross-check (P4) is implemented and unit-tested, and it ran
successfully on Gemini 3 Pro (5/5 `verified`) — but the shared Anthropic API
key's **credit balance was exhausted partway through the build**. So the full
corpus was verified with the VLM disabled, falling back to the
**structural + OCR-presence** signal:

- `accepted` = a scalar value that is provably present in the OCR text of its
  own crop (structural extraction self-confirmed). 408 of 473.
- `needs_review` = no single scalar (fractions, multi-value cells) or the
  OCR-presence check did not find it. 65 of 473.
- `verified` (structural *and* independent VLM agree + OCR-present) requires API
  credit; re-run `./scripts/ingest.sh` with a funded `CLAUDE_API_KEY` to upgrade
  `accepted` rows to `verified`.

Nothing is shown as more trustworthy than it is, and every number — whatever its
status — carries its provenance crop.

## Other known limits

- HTML crops render from the **live page** (faithful layout); raw bytes are
  frozen by sha256 for provenance. Full HTML asset-bundle freezing is out of
  scope this milestone.
- Image-only PDF tables (no text layer) are out of scope (§3.3).
- This managed environment firewalls all egress except TLS/443, so a public
  **Cloudflare tunnel cannot be established** (the edge needs port 7844). The
  dashboard is therefore delivered as the offline export; run it locally with
  `scripts/serve.sh`.
