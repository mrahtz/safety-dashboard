"""Invoke the structural extractors and return IR candidates.

The HTML extractor runs in a subprocess (Playwright sync API must not share a
thread with Flask's event loop); the PDF extractor (P2) is pure Python and runs
in-process. Both return the identical IR shape, so callers are source-agnostic.
"""

import pathlib
import subprocess
import sys
import tempfile
import uuid

from llm_metrics import ir, paths, serde


def _new_run_id() -> str:
    return uuid.uuid4().hex[:10]


def _run_html_raw(source: str, table_index: int) -> list[dict]:
    """Invoke the extractor subprocess and return its augmented item dicts
    (each is a serialized candidate plus an optional ``section`` block)."""
    paths.ensure()
    run_id = _new_run_id()
    out_json = pathlib.Path(tempfile.gettempdir()) / f"{run_id}.json"
    src_root = pathlib.Path(__file__).resolve().parents[1]  # the src/ dir
    # Inherit the full environment (Playwright needs PLAYWRIGHT_BROWSERS_PATH etc.)
    # and prepend our src/ to PYTHONPATH so the subprocess imports llm_metrics.
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-m", "llm_metrics.extract_html", source, str(table_index), str(paths.CROPS), str(out_json)],
        capture_output=True, text=True, env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"HTML extraction failed:\n{proc.stderr[-2000:]}")
    import json
    return json.loads(out_json.read_text())


def run_html(source: str, table_index: int) -> tuple[ir.Candidate, ...]:
    return tuple(serde.candidate_from_dict(d) for d in _run_html_raw(source, table_index))


def run_html_sections(source: str) -> list[tuple[ir.Candidate, dict]]:
    """All-tables extraction, each candidate paired with its table's section."""
    return [(serde.candidate_from_dict(d), d.get("section") or {})
            for d in _run_html_raw(source, -1)]


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
