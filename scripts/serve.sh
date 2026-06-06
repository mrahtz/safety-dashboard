#!/usr/bin/env bash
# Run the review/dashboard web UI on http://localhost:8000.
set -o errexit
cd "$(dirname "$0")/.."
PYTHONPATH=src python3 -c "from llm_metrics import app; app.app.run(host='0.0.0.0', port=${1:-8000}, threaded=True)"
