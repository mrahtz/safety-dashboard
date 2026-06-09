"""PDF page rasterizer (VLM-transcription path).

PyMuPDF (``fitz``) rasterizes a whole page at high DPI -- the per-page screenshot
the vision model transcribes in the page-at-a-time PDF path.
"""

import pathlib

import fitz

SCALE = 3          # ~216 DPI render


def render_pdf_page(pdf_path: pathlib.Path, page_index: int, out_path: pathlib.Path) -> None:
    """Rasterize a WHOLE page (no crop, no highlight) -- the per-page screenshot
    the VLM transcribes in the page-at-a-time PDF path."""
    doc = fitz.open(pdf_path)
    if not 0 <= page_index < doc.page_count:
        raise IndexError(f"page {page_index} out of range (doc has {doc.page_count})")
    pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"page render not written: {out_path}")  # broken invariant (section 9)
