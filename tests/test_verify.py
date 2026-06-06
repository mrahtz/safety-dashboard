"""Tests for the verification decision and the OCR-presence self-check.

Includes the P4 definition-of-done case: a deliberately wrong bounding box (a
crop showing a different number) must be caught by the OCR-presence check.
"""

import pathlib

import PIL.Image
import PIL.ImageDraw
import pytest

from llm_metrics import ocr, verify


def test_decide_agreement_verifies():
    assert verify._decide(0.979, 0.979, ocr_ok=True) == "verified"


def test_decide_disagreement_reviews():
    assert verify._decide(0.979, 0.5, ocr_ok=True) == "needs_review"


def test_decide_failed_ocr_reviews_even_on_agreement():
    assert verify._decide(0.979, 0.979, ocr_ok=False) == "needs_review"


def test_decide_non_scalar_reviews():
    assert verify._decide(None, None, ocr_ok=True) == "needs_review"


def _crop_with(text: str, path: pathlib.Path) -> pathlib.Path:
    img = PIL.Image.new("RGB", (200, 90), "white")
    d = PIL.ImageDraw.Draw(img)
    d.rectangle((8, 8, 192, 82), outline=(230, 0, 35), width=3)
    d.text((40, 35), text, fill="black")
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def test_ocr_presence_true_for_matching_crop(tmp_path):
    crop = _crop_with("0.500", tmp_path / "right.png")
    assert ocr.value_present_in_crop("0.500", crop) is True


def test_wrong_bbox_caught_by_ocr_presence(tmp_path):
    # The crop shows 0.500 but the parser claims the cell is 0.999 (bbox points
    # at the wrong region). The OCR-presence check must reject it.
    crop = _crop_with("0.500", tmp_path / "wrong.png")
    assert ocr.value_present_in_crop("0.999", crop) is False
