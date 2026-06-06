"""PDF crop-and-highlight renderer (P2).

PyMuPDF (``fitz``) rasterizes a page at high DPI; we draw the highlight box at
the pdfplumber bounding box (scaled into pixels) and crop tight to it. This is
the PDF arm of the same crop contract the HTML path satisfies, so downstream is
source-agnostic.
"""

import pathlib

import fitz
import PIL.Image
import PIL.ImageDraw

SCALE = 3          # ~216 DPI render
PAD_PX = 26        # context kept around the boxed region


def render_pdf_crop(pdf_path: pathlib.Path, page_index: int,
                    bbox: tuple[float, float, float, float], out_path: pathlib.Path) -> None:
    doc = fitz.open(pdf_path)
    if not 0 <= page_index < doc.page_count:
        raise IndexError(f"page {page_index} out of range (doc has {doc.page_count})")
    page = doc[page_index]
    x0, top, x1, bottom = bbox
    pw, ph = page.rect.width, page.rect.height
    if x0 < -1 or top < -1 or x1 > pw + 1 or bottom > ph + 1 or x1 <= x0 or bottom <= top:
        raise ValueError(f"bbox {bbox} outside page bounds {pw}x{ph}")  # broken invariant (section 9)
    pix = page.get_pixmap(matrix=fitz.Matrix(SCALE, SCALE))
    img = PIL.Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    sx0, st, sx1, sb = (v * SCALE for v in bbox)
    PIL.ImageDraw.Draw(img).rectangle((sx0, st, sx1, sb), outline=(230, 0, 35), width=3)
    crop = img.crop((max(0, sx0 - PAD_PX), max(0, st - PAD_PX),
                     min(img.width, sx1 + PAD_PX), min(img.height, sb + PAD_PX)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out_path)
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"crop not written: {out_path}")  # broken invariant (section 9)
