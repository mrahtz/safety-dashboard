#!/usr/bin/env bash
# Publish var/metrics.sqlite + crops to Supabase (reads var/supabase.env).
set -o errexit
cd "$(dirname "$0")/.."
PYTHONPATH=src python3 -m llm_metrics.publish
