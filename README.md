# safety-dashboard — LLM-metrics ingestion with screenshot provenance

A pipeline that extracts LLM benchmark numbers from published system cards into
a database, plus a UI to review them. The one load-bearing property: **every
stored number is provable** — each is bound to a bounding box in the source's
own coordinate system, so its screenshot is a tight crop of exactly that spot,
by construction rather than by luck.

Design in one line: a deterministic **structural extractor** is the source of
truth (it gives real coordinates); a **VLM** is only an independent cross-check
of the number, never the extractor. See the project brief for the full rationale.

## Status: Phase 0 (frozen contracts + eval fixtures)

This branch delivers **Phase 0 only** — the prerequisites that must be reviewed
and committed before any extractor is written. The build proceeds as a sequence
of human-reviewed prototypes (P1…P7); no prototype starts before the prior one
has sign-off. See [`RUNBOOK.md`](RUNBOOK.md) for the review/demo path.

What's here:

| Path | What it is | Frozen? |
|------|------------|---------|
| `src/llm_metrics/ir.py` | The intermediate representation both extractors emit (brief §5.1) | **yes** |
| `src/llm_metrics/schema.py` | SQLite schema: `sources`, `candidates`, `attempts` + enum CHECKs (brief §5.2) | **yes** |
| `fixtures/eval_fixtures.json` | ~26 numbers hand-transcribed from the two test sources, with location + context | data |
| `src/llm_metrics/fixtures.py` | Typed, validating loader for the fixtures | — |
| `tests/` | Contract + fixture self-tests (`pytest`) | — |

The two contracts are **single-owner** (brief §5, §8): propose changes to the
orchestrator, don't edit them unilaterally.

## Quickstart

```bash
pip install pytest      # the only Phase 0 dependency beyond the stdlib
pytest -q               # 21 tests
```

## What's deliberately NOT here yet

Extractors, the crop renderer, the source freezer, the VLM verifier, and the UI
all arrive with their prototypes (P1+). Scanned/image-only PDFs are out of scope
(no text layer — brief §3.3). The Gemini capabilities table on PDF page 5 is an
embedded image with no text layer and is likewise out of scope; see the
coverage note in the fixtures file.
