"""PDF text-layer extractor (P2).

Deterministic source of truth for PDFs (section 2.2): ``pdfplumber`` gives words
and table cells with bounding boxes ``(x0, top, x1, bottom)`` per page; the crop
renderer (``crop.py``, PyMuPDF) rasterizes and boxes that region. Emits the
identical IR as the HTML path, so everything downstream is source-agnostic.

Alongside each numeric cell we also render one **section screenshot** per table
(the whole table region, no highlight) and tag every cell with the table it came
from, so the per-source view can group numbers under the table they live in.
This section metadata travels next to the IR (not inside it -- the IR is a frozen
contract), as a plain dict the persistence layer merges into ``context``.

Out of scope (section 3.3): PDFs with no text layer. We detect that and fail
loudly rather than emit garbage.
"""

import pathlib
import re

import pdfplumber

from llm_metrics import crop, fetch, ir, paths

# Footnote lines on a page, e.g. "1The ordering of evaluations..." / "2 For tone".
_FOOTNOTE_LINE = re.compile(r"^\d{1,2}\s?[A-Z(]")


def _cache() -> pathlib.Path:
    return paths.ROOT / "cache"


def _page_footnotes(text: str) -> tuple[str, ...]:
    return tuple(ln.strip() for ln in text.splitlines() if _FOOTNOTE_LINE.match(ln.strip()))


def _title_above(page, table_bbox: tuple[float, float, float, float]) -> str:
    """Best-effort caption: the line of text just above the table's top edge."""
    x0, top, x1, _ = table_bbox
    try:
        words = page.extract_words()
    except Exception:
        return ""
    above = [w for w in words if w["bottom"] <= top + 1 and (top - w["bottom"]) < 38
             and w["x1"] > x0 - 40 and w["x0"] < x1 + 40]
    if not above:
        return ""
    line_top = max(w["top"] for w in above)               # the closest line above
    line = [w for w in above if abs(w["top"] - line_top) < 4]
    text = " ".join(w["text"] for w in sorted(line, key=lambda w: w["x0"])).strip()
    return text[:110]


def _table_candidates(local, page, page_index, table_index, table, footnotes,
                      crops_dir, run_id) -> tuple[list[ir.Candidate], dict]:
    data = table.extract()
    header = [(c or "").replace("\n", " ").strip() for c in data[0]]
    section_key = f"p{page_index}_t{table_index}"
    title = _title_above(page, table.bbox) or f"Page {page_index + 1}, table {table_index + 1}"
    section_crop = crops_dir / f"{run_id}_{section_key}_section.png"
    out: list[ir.Candidate] = []
    for ri, row in enumerate(table.rows):
        if ri == 0:
            continue
        row_label = (data[ri][0] or "").replace("\n", " ").strip()
        for ci, cbox in enumerate(row.cells):
            text = (data[ri][ci] or "").replace("\n", " ").strip()
            if ci == 0 or cbox is None or not re.match(r"^[-+($]?\$?\d", text):
                continue
            crop_path = crops_dir / f"{run_id}_p{page_index}_t{table_index}_r{ri}_c{ci}.png"
            crop.render_pdf_crop(local, page_index, tuple(cbox), crop_path)
            out.append(ir.Candidate(
                value_string=text,
                source_ref=ir.SourceRef(kind="pdf", page=page_index, selector=None, bbox=tuple(cbox)),
                crop_path=crop_path,
                context=ir.Context(column_header=header[ci] if ci < len(header) else "",
                                   row_label=row_label, caption="", footnotes=footnotes)))
    section = {"section_key": section_key, "section_title": title,
               "section_crop_path": str(section_crop)}
    if out:  # only render the section image if the table yielded numbers
        crop.render_pdf_section(local, page_index, tuple(table.bbox), section_crop)
    return out, section


def extract(source: str, page_index: int, table_index: int,
            crops_dir: pathlib.Path, run_id: str) -> tuple[ir.Candidate, ...]:
    paths.ensure()
    local = fetch.local_copy(source, _cache())
    with pdfplumber.open(local) as pdf:
        if page_index >= len(pdf.pages):
            raise IndexError(f"page {page_index} out of range ({len(pdf.pages)} pages)")
        page = pdf.pages[page_index]
        page_text = page.extract_text() or ""
        if not page_text.strip():
            raise ValueError(f"page {page_index} has no text layer (out of scope, section 3.3)")
        tables = page.find_tables()
        if table_index >= len(tables):
            raise ValueError(f"no table #{table_index} on page {page_index} ({len(tables)} found)")
        cands, _ = _table_candidates(local, page, page_index, table_index, tables[table_index],
                                     _page_footnotes(page_text), crops_dir, run_id)
        return tuple(cands)


def extract_all_with_sections(source: str, crops_dir: pathlib.Path, run_id: str,
                              max_cells: int = 60) -> list[tuple[ir.Candidate, dict]]:
    """Scan every page's tables for numeric cells (capped), returning each
    candidate paired with its table's section metadata. Pages with no text layer
    are skipped as a normal outcome, not an error (section 9)."""
    paths.ensure()
    local = fetch.local_copy(source, _cache())
    out: list[tuple[ir.Candidate, dict]] = []
    with pdfplumber.open(local) as pdf:
        for pi, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if not text.strip():
                continue
            footnotes = _page_footnotes(text)
            for ti, table in enumerate(page.find_tables()):
                cands, section = _table_candidates(local, page, pi, ti, table, footnotes,
                                                   crops_dir, run_id)
                for c in cands:
                    out.append((c, section))
                    if len(out) >= max_cells:
                        return out[:max_cells]
    return out


def extract_all(source: str, crops_dir: pathlib.Path, run_id: str,
                max_cells: int = 60) -> tuple[ir.Candidate, ...]:
    return tuple(c for c, _ in extract_all_with_sections(source, crops_dir, run_id, max_cells))
