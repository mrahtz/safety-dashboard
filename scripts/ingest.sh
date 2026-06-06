#!/usr/bin/env bash
# Freeze, extract, persist and VLM-verify every system card in the corpus.
set -o errexit
cd "$(dirname "$0")/.."
PYTHONPATH=src python3 -c "from llm_metrics import db, pipeline; pipeline.run(db.connect())"
