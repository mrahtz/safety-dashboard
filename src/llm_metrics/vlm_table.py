"""VLM table transcriber (the reader, not a verifier).

Section 2.3's vision model, repurposed as the *primary* reader: instead of a
structural parser, we hand the model the exact image a human reviewer sees -- the
whole table screenshot -- and ask it to transcribe the grid to CSV. Every
non-empty numeric cell becomes a candidate whose provenance is that table
screenshot (table-level, not per-cell bounding box).

stdlib urllib only (the Anthropic SDK's httpx transport fails in this proxied
environment; urllib works), mirroring vlm.py.
"""

import base64
import csv
import io
import os
import json
import pathlib
import re
import urllib.error
import urllib.request


class VlmUnavailable(RuntimeError):
    """The model could not be reached (auth/billing/network)."""


# A capable reader matters here (this is the source of truth now), so default to
# Sonnet rather than Haiku. Override with LLM_METRICS_VLM_MODEL.
MODEL = os.environ.get("LLM_METRICS_VLM_MODEL", "claude-sonnet-4-6")
_ENDPOINT = "https://api.anthropic.com/v1/messages"
_PROMPT = (
    "You are transcribing ONE table from an AI model/system card screenshot. "
    "Output the table as CSV and NOTHING else -- no prose, no code fences.\n"
    "Rules:\n"
    "1. The first CSV row is the column headers exactly as printed (these are "
    "usually model names). Keep the first cell for the row-label column; it may "
    "be blank.\n"
    "2. Each following row begins with the row label exactly as printed (the "
    "benchmark / metric / category), then one value per column in order.\n"
    "3. Copy every value exactly as printed -- keep %, +/- signs, and decimals. "
    "Use an empty field for blank cells.\n"
    "4. Do not add, drop, merge, reorder, or compute anything. One CSV column "
    "per table column, one CSV row per table row.\n"
    "5. Sub-tables: if a new set of column headers appears partway through the "
    "table body (a row of text labels, not values), treat it as a sub-table "
    "boundary. Emit a blank line, then output that row as a fresh header row, "
    "then continue with the rows below it.\n"
    "6. Strip footnote markers (*, †, ‡, §, ¶, and superscript digits/letters) "
    "from row labels and column headers. Keep values exactly as printed."
)

# Long-format reader (the production schema): one CSV line per numeric cell,
# splitting the model, condition, benchmark, value, and units into columns and
# recording the cell's grid position. parse_long() turns it into metric dicts.
_LONG_PROMPT = (
    "You are transcribing the data table(s) in this AI model/system card image "
    "into a normalized long format. Output CSV and NOTHING else -- no prose, no "
    "code fences.\n"
    "The first line must be exactly this header:\n"
    "model,condition,benchmark,value,units,row,col\n"
    "Then emit ONE line per NUMERIC data cell. For each cell:\n"
    "- model: the model the column belongs to (its column header, usually a model "
    "name); carry a spanned model header across every column it covers.\n"
    "- condition: the setting for that column or row -- e.g. low, medium, high, "
    "with tools, no tools, browsing. EMPTY if there is no such axis.\n"
    "- benchmark: the row label (the benchmark / metric / category).\n"
    "- value: the number as printed but WITHOUT its unit symbol and WITHOUT "
    "thousands separators (write 2439 not 2,439; write 95.8 not 95.8%). Keep a "
    "leading +/- sign and decimals.\n"
    "- units: the unit of the value -- '%' for a percentage, '$' for dollars, "
    "'Elo', etc.; EMPTY for a plain score or ratio.\n"
    "- row: 0-based row index in the data body, top to bottom (continue across "
    "sub-tables; skip header rows).\n"
    "- col: 0-based index among the VALUE columns only; the leftmost value column "
    "is 0. Do NOT count the label column.\n"
    "Quote any field that contains a comma. Strip footnote markers from labels. "
    "One line per numeric cell; do not add, drop, or compute anything."
)


def parse_long(text: str) -> list[dict]:
    """Long-format CSV -> [{model, condition, benchmark, value, units, row_idx,
    col_idx}] for the numeric cells."""
    out: list[dict] = []
    for r in csv.DictReader(io.StringIO(_strip_fences(text))):
        val = (r.get("value") or "").strip()
        if not re.match(r"^[<>~≤≥]?\s*[-+]?\$?\.?\d", val):  # must be a number
            continue
        try:
            row_idx = int(r["row"])
        except (TypeError, ValueError, KeyError):
            row_idx = None
        try:
            col_idx = int(r["col"])
        except (TypeError, ValueError, KeyError):
            col_idx = None
        out.append({"model": (r.get("model") or "").strip(),
                    "condition": (r.get("condition") or "").strip(),
                    "benchmark": (r.get("benchmark") or "").strip(),
                    "value": val, "units": (r.get("units") or "").strip(),
                    "row_idx": row_idx, "col_idx": col_idx})
    return out


def _api_key() -> str:
    key = os.environ.get("CLAUDE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise VlmUnavailable("no CLAUDE_API_KEY / ANTHROPIC_API_KEY in environment")
    return key


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    return t.strip()


def _parse_block(block_text: str, drop: set[str]) -> list[tuple[str, str, str]]:
    """Parse one CSV block (no blank lines) into numeric (col, row_label, value) tuples."""
    rows = [r for r in csv.reader(io.StringIO(block_text)) if any(c.strip() for c in r)]
    if len(rows) < 2:
        return []
    headers = [h.strip() for h in rows[0]]
    out: list[tuple[str, str, str]] = []
    for r in rows[1:]:
        row_label = (r[0] if r else "").strip()
        for i in range(1, len(r)):
            val = (r[i] or "").strip()
            col = headers[i].strip() if i < len(headers) else ""
            # Must look like a NUMBER (optionally led by < > ~ +/- ( $ .), not merely
            # contain a digit -- otherwise model-name cells like "gpt-5-thinking" or
            # "OpenAI o3" (from win/loss matchup tables) leak in as "values".
            if col.lower() not in drop and re.match(r"^[<>~≤≥]?\s*[-+($]?\$?\.?\d", val):
                out.append((col, row_label, val))
    return out


def parse_csv(text: str) -> list[tuple[str, str, str]]:
    """CSV text -> [(column_header, row_label, value_string)] for numeric cells.

    Handles multi-block output (sub-tables separated by blank lines)."""
    drop = {"description", "notes", "note"}
    out: list[tuple[str, str, str]] = []
    for block in re.split(r"\n\s*\n", _strip_fences(text)):
        out.extend(_parse_block(block.strip(), drop))
    return out


def transcribe_raw(image_path: pathlib.Path, model: str = MODEL, timeout: int = 120,
                   prompt: str = _PROMPT, max_tokens: int = 4000) -> str:
    """Return the model's raw CSV transcription of the table image (the table
    format we persist so the per-source view can re-render it faithfully).

    ``prompt``/``max_tokens`` are overridable so callers can request an
    alternative transcription schema (e.g. a normalized long format)."""
    if not image_path.exists():
        raise FileNotFoundError(f"table image missing for transcription: {image_path}")
    b64 = base64.standard_b64encode(image_path.read_bytes()).decode()
    body = json.dumps({
        "model": model, "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": prompt}]}],
    }).encode()
    req = urllib.request.Request(_ENDPOINT, data=body, headers={
        "x-api-key": _api_key(), "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code in (400, 401, 403):
            raise VlmUnavailable(f"HTTP {e.code}: {e.read().decode()[:200]}") from e
        raise
    # An empty content list is a valid response: the model output nothing (e.g. a
    # prose PDF page). Treat it as an empty transcription, not an IndexError.
    texts = [b.get("text", "") for b in (data.get("content") or []) if b.get("type") == "text"]
    return _strip_fences(texts[0] if texts else "")
