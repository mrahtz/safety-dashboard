"""OCR-presence self-check (section 4.4, "third cheap check").

Assert that a value string actually appears in the OCR text of its own crop.
This catches the nasty case where the parser read the right cell text but the
bounding box points at the wrong region (e.g. off by one row) -- which a VLM
read alone might not expose. A failed check routes the candidate to
``needs_review`` (it never auto-rejects), so this is allowed to be conservative.

OCR on a tight, often colored, low-contrast crop drops decimal points and
leading zeros, so we match on the *significant digit run* (leading zeros
stripped) rather than an exact string, and we OCR a few preprocessed variants to
improve recall. A wrong-region bbox shows different digits and still fails.
"""

import pathlib
import re

import PIL.Image
import PIL.ImageOps
import pytesseract

from llm_metrics import normalize


def _variants(img: PIL.Image.Image) -> tuple[PIL.Image.Image, ...]:
    g = PIL.ImageOps.grayscale(img)
    w, h = g.size
    inner = g.crop((int(w * 0.18), int(h * 0.18), int(w * 0.82), int(h * 0.82)))
    return (g, inner)


def ocr_text(crop_path: pathlib.Path) -> str:
    if not crop_path.exists():
        raise FileNotFoundError(f"crop missing for OCR check: {crop_path}")
    img = PIL.Image.open(crop_path)
    # Two passes: the full crop and an inner crop (drops neighbour-row noise).
    # --psm 6 (uniform block) reads tight, colored cells far better than default.
    return "\n".join(pytesseract.image_to_string(v, config="--psm 6") for v in _variants(img))


def _significant_digits(s: str) -> str:
    return re.sub(r"\D", "", s).lstrip("0")


def value_present_in_crop(value_string: str, crop_path: pathlib.Path) -> bool:
    target = _significant_digits(normalize.strip_decorations(value_string))
    if not target:
        return True  # nothing numeric to check (e.g. a PASS/FAIL or all-zero cell)
    return target in _significant_digits(ocr_text(crop_path))
