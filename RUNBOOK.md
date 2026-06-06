# Phase 0 runbook — frozen contracts + eval fixtures

Per the review-gate workflow (brief §7.0), a prototype is *done* only when it
passes its own tests, ships this runbook, and a human signs off. Phase 0 is
"reviewed once, before P1": confirm the contracts, and confirm the fixtures
match what's actually in the two cards.

## Two-minute demo path

```bash
pip install pytest
pytest -q                 # expect: 21 passed
```

Then eyeball the three artifacts under review:

1. **IR contract** — `src/llm_metrics/ir.py`. Confirm `Context`, `SourceRef`,
   `Candidate` match brief §5.1 verbatim. `test_ir.py` pins the field names.
2. **DB schema** — `src/llm_metrics/schema.py`. Confirm the three tables and the
   `status` / `attempts.kind` / `kind` enums match brief §5.2. `test_schema.py`
   proves an out-of-domain `status` is *rejected* by the DB (fails loudly, §9).
3. **Eval fixtures** — `fixtures/eval_fixtures.json`. This is the regression set
   for every later prototype. Spot-check a few rows against the cards (below).

## How the fixtures were built (and how to re-verify)

Numbers were hand-transcribed from the two §6 test sources, captured 2026-06-06.
The exact bytes transcribed from are pinned by sha256 in the fixtures file
(reproducible/diffable once the P3 freezer exists):

- **GPT-5.5** — HTML, Deployment Safety Hub: `https://deploymentsafety.openai.com/gpt-5-5`
  (benchmark tables are real `<table>` elements in the static DOM).
- **Gemini 3 Pro** — PDF model card:
  `https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf`

To re-verify a couple of rows by hand:

```bash
# GPT-5.5 table 0 (refusal/safety scores) — e.g. hate/gpt-5.5 = "0.868*"
curl -sL https://deploymentsafety.openai.com/gpt-5-5 -o /tmp/hub.html
python3 - <<'PY'
import html.parser, pathlib
# (parse <table> elements; table index 0 is the designated HTML acceptance table)
PY

# Gemini page 8 (0-indexed 7) safety eval table — e.g. Text to Text Safety = "-10.4%"
curl -sL "https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf" -o /tmp/g.pdf
python3 -c "import pdfplumber; print(pdfplumber.open('/tmp/g.pdf').pages[7].extract_tables())"
```

## Why these specific numbers (the stress cases to check)

The fixtures deliberately include the hard cases the brief calls out, so later
prototypes are forced to handle them:

- **Footnote glued onto a number**: `0.868*` → normalized `0.868` (§4.2, §4.4).
- **Mixed `%` inside/outside parentheses**: `51.8% (57.2%, 3818)` (§4.4).
- **Parenthetical deltas**: `32.32% (+1.35%)`, and every Gemini row is a delta
  vs. Gemini 2.5 Pro (`-10.4%`, `+0.2% (non-egregious)`) (§6 stress notes).
- **Trailing-zero precision**: `0.810` must compare equal to `0.81`.
- **Prose numbers, not tables**: Gemini `1M` context / `64K` output (§4.3).
- **Fractions with no single float**: Gemini `11/12`, `0/13` (normalized `null`).

## Known gaps / coverage notes

- Fixtures are **live captures** (pre-freeze). Source freezing is P3; until then
  a re-fetch may return a different sha256 — for the GPT-5.5 hub that is expected
  (system cards get silently revised, §4.1) and is itself a signal to record.
- **Gemini capabilities benchmark table (PDF page 5) is an embedded image** with
  no text layer → out of scope (§3.3). Gemini fixtures therefore come from the
  text-layer tables (pages 8 and 10) and prose (page 2). This caps the clean
  Gemini set at ~13 rather than a full 15 — flagged here for the reviewer.

## Sign-off gate

Do **not** start P1 (HTML extraction) until a human confirms: the two contracts
are correct and frozen, and the fixtures match the cards.
