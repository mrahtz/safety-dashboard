"""PDF text-layer extractor (P2).

Deterministic source of truth for PDFs (section 2.2): ``pdfplumber`` gives words
and table cells with bounding boxes ``(x0, top, x1, bottom)`` per page; the crop
renderer (``crop.py``, PyMuPDF) rasterizes and boxes that region. Emits the
identical IR as the HTML path, so everything downstream is source-agnostic.

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


def _table_candidates(local, page_index, table_index, table, footnotes, crops_dir, run_id) -> list[ir.Candidate]:
    data = table.extract()
    header = [(c or "").replace("\n", " ").strip() for c in data[0]]
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
    return out


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
        return tuple(_table_candidates(local, page_index, table_index, tables[table_index],
                                       _page_footnotes(page_text), crops_dir, run_id))


def extract_all(source: str, crops_dir: pathlib.Path, run_id: str, max_cells: int = 60) -> tuple[ir.Candidate, ...]:
    """Scan every page's tables for numeric cells (capped). Pages with no text
    layer are skipped as a normal outcome, not an error (section 9)."""
    paths.ensure()
    local = fetch.local_copy(source, _cache())
    out: list[ir.Candidate] = []
    with pdfplumber.open(local) as pdf:
        for pi, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if not text.strip():
                continue
            footnotes = _page_footnotes(text)
            for ti, table in enumerate(page.find_tables()):
                out.extend(_table_candidates(local, pi, ti, table, footnotes, crops_dir, run_id))
                if len(out) >= max_cells:
                    return tuple(out[:max_cells])
    return tuple(out)
