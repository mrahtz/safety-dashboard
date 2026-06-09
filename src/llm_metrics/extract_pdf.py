"""PDF page-render lister (VLM-transcription path).

DeepMind model cards use borderless tables pdfplumber's detection misses, so
instead of detecting tables we rasterize every page to a whole-page image
(``crop.py``, PyMuPDF) and let the vision model read it. Emits the same
section-dict shape as the HTML path, so everything downstream is source-agnostic.
"""

import pathlib

import pdfplumber

from llm_metrics import crop, fetch, paths


def _cache() -> pathlib.Path:
    return paths.ROOT / "cache"


def _page_title(page) -> str:
    """First non-empty text line of the page, as a section heading."""
    for line in (page.extract_text() or "").splitlines():
        if line.strip():
            return line.strip()[:110]
    return ""


def list_pages(source: str, crops_dir: pathlib.Path, run_id: str) -> list[dict]:
    """Render EVERY page to a whole-page image for the page-at-a-time VLM path.

    Does no table detection -- the model reads each page (DeepMind cards use
    borderless tables pdfplumber misses). Returns the section-dict shape the
    orchestrator expects, one per page."""
    paths.ensure()
    local = fetch.local_copy(source, _cache())
    crops_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    with pdfplumber.open(local) as pdf:
        for pi, page in enumerate(pdf.pages):
            img = crops_dir / f"{run_id}_p{pi}_section.png"
            crop.render_pdf_page(local, pi, img)
            out.append({"section_key": f"p{pi}",
                        "section_title": _page_title(page) or f"Page {pi + 1}",
                        "image": str(img), "page": pi, "bbox": None})
    return out
