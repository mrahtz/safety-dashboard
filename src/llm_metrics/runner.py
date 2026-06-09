"""Invoke the HTML table-listing extractor in a subprocess.

Playwright's sync API must not share a thread with the caller, so the extractor
runs as its own process; it screenshots every data table on the page and returns
the section dicts (key / title / image / page) the orchestrator transcribes.
"""

import pathlib
import subprocess
import sys
import tempfile
import uuid

from llm_metrics import paths


def _new_run_id() -> str:
    return uuid.uuid4().hex[:10]


def run_html_tables(source: str) -> list[dict]:
    """Screenshot each data table (VLM-transcription path). Returns dicts with
    section_key / section_title / image / page."""
    import json
    paths.ensure()
    run_id = _new_run_id()
    out_json = pathlib.Path(tempfile.gettempdir()) / f"{run_id}.json"
    src_root = pathlib.Path(__file__).resolve().parents[1]
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "llm_metrics.extract_html", source, "tables", str(paths.CROPS), str(out_json)],
        capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"HTML table listing failed:\n{proc.stderr[-2000:]}")
    return json.loads(out_json.read_text())
