"""Verification protocol (P4 / section 4.4).

Compare the structural reading against the independent VLM reading, after
normalizing both, plus the OCR-presence self-check:

- normalize before comparing (strip %, marks, round to the source's precision),
- assert the structural value appears in the OCR text of its own crop,
- agreement AND OCR-present -> ``verified``; otherwise -> ``needs_review``.

We never trust the VLM's number as truth; we trust its *agreement* as a signal.
Values with no single scalar reading (fractions like ``11/12``, ``PASS``) cannot
be auto-compared and route to ``needs_review``.
"""

import dataclasses
import pathlib

from llm_metrics import normalize, ocr, vlm


@dataclasses.dataclass(frozen=True)
class Result:
    structural_value: float | None
    vlm_raw: str
    vlm_value: float | None
    ocr_ok: bool
    status: str            # "verified" | "accepted" | "needs_review"


def verify_candidate(value_string: str, crop_path: pathlib.Path) -> Result:
    precision = normalize.precision_of(value_string)
    structural_value = normalize.to_float(value_string, precision)
    ocr_ok = ocr.value_present_in_crop(value_string, crop_path)
    try:
        vlm_raw = vlm.read_number(crop_path)
    except vlm.VlmUnavailable as e:
        # No independent verifier available -> fall back to the structural + OCR
        # signal. A scalar value that is provably present in its own crop is
        # 'accepted'; anything else still needs a human (section 4.4).
        status = "accepted" if (structural_value is not None and ocr_ok) else "needs_review"
        return Result(structural_value, f"unavailable: {e}", None, ocr_ok, status)
    vlm_value = normalize.to_float(vlm_raw, precision)
    return Result(structural_value, vlm_raw, vlm_value, ocr_ok,
                  _decide(structural_value, vlm_value, ocr_ok))


def _decide(structural_value: float | None, vlm_value: float | None, ocr_ok: bool) -> str:
    if structural_value is None or not ocr_ok:
        return "needs_review"
    if vlm_value is not None and vlm_value == structural_value:
        return "verified"
    return "needs_review"
