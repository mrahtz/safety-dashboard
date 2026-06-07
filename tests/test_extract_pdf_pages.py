"""The page-at-a-time PDF lister renders EVERY page (no table detection, no
prose pre-filter) and returns one section dict per page."""

import pathlib

import fitz

from llm_metrics import extract_pdf


def _make_pdf(path: pathlib.Path) -> None:
    doc = fitz.open()
    doc.new_page().insert_text(
        (72, 72), "Evaluation\nBenchmark ModelA ModelB\nAIME 90.1 85.2\nGPQA 70.0 65.5")
    doc.new_page().insert_text((72, 72), "This page is prose only, with no tables.")
    doc.save(str(path))
    doc.close()


def test_list_pages_renders_every_page(tmp_path):
    pdf = tmp_path / "card.pdf"
    _make_pdf(pdf)
    pages = extract_pdf.list_pages(str(pdf), tmp_path / "crops", "test")

    # One section per page, in order, including the prose page (no pre-filter).
    assert [p["section_key"] for p in pages] == ["p0", "p1"]
    assert [p["page"] for p in pages] == [0, 1]
    for p in pages:
        img = pathlib.Path(p["image"])
        assert img.exists() and img.stat().st_size > 0
    # Title is the page's first text line.
    assert pages[0]["section_title"].startswith("Evaluation")
