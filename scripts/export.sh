#!/usr/bin/env bash
# Render the database to a self-contained offline dashboard under var/export/.
set -o errexit
cd "$(dirname "$0")/.."
PYTHONPATH=src python3 -m llm_metrics.export_static "${1:-var/export}"
