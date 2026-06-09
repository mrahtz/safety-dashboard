"""HTML table-screenshot lister (VLM-transcription path).

Finds the data tables on a page (>= a few numeric cells), tags each, and renders
one whole-``<table>`` screenshot per table for the vision model to transcribe.
Provenance is the whole-table image, not per-cell boxes.

Runs as ``python -m llm_metrics.extract_html <source> tables <crops_dir>
<out_json>`` so the orchestrator can invoke it in a subprocess, keeping the sync
Playwright event loop off the caller's thread. ``source`` may be an http(s) URL
or a local file path (a frozen copy).
"""

import json
import pathlib
import sys

import playwright.sync_api as pw

VIEWPORT = {"width": 1280, "height": 2400}
SCALE = 2

# Find the data tables on the page (>= min numeric cells), tag each, and return
# its stable key + title. The VLM-transcription path screenshots each table and
# reads it whole rather than cell-by-cell.
_TABLES_JS = r"""
(minNumeric) => {
  const titleFor = (table) => {
    const cap = (table.querySelector('caption')?.innerText || '').trim();
    if (cap) return cap.slice(0, 140);
    let el = table;
    for (let i = 0; i < 6 && el; i++) {
      let p = el.previousElementSibling;
      while (p) {
        if (/^H[1-4]$/.test(p.tagName)) return p.innerText.trim().slice(0, 140);
        const h = p.querySelector && p.querySelector('h1,h2,h3,h4');
        if (h) return h.innerText.trim().slice(0, 140);
        p = p.previousElementSibling;
      }
      el = el.parentElement;
    }
    return '';
  };
  const out = [];
  [...document.querySelectorAll('table')].forEach((t, ti) => {
    let n = 0;
    t.querySelectorAll('td,th').forEach(c => { if (/^[-+($]?\d/.test(c.innerText.trim())) n++; });
    if (n >= minNumeric) {
      const skey = 't' + ti; t.setAttribute('data-llm-table', skey);
      out.push({section_key: skey, selector: '[data-llm-table="' + skey + '"]', title: titleFor(t)});
    }
  });
  return out;
}
"""


def _as_url(source: str) -> str:
    if source.startswith(("http://", "https://", "file://")):
        return source
    return pathlib.Path(source).resolve().as_uri()


def _render_section(page: pw.Page, selector: str, out_path: pathlib.Path) -> bool:
    """Screenshot a whole <table> element (no highlight). Returns success."""
    el = page.query_selector(selector)
    if el is None:
        return False
    try:
        el.scroll_into_view_if_needed()
        el.screenshot(path=str(out_path))
    except Exception:
        return False
    return out_path.exists() and out_path.stat().st_size > 0


def _open(p):
    browser = p.chromium.launch()
    # The managed environment proxies TLS with a CA Chromium doesn't trust; ignore
    # cert errors so we can reach the (already-trusted) source hosts.
    return browser, browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE, ignore_https_errors=True)


def list_tables(source: str, crops_dir: pathlib.Path, run_id: str,
                min_numeric: int = 3) -> list[dict]:
    """Screenshot each data table on the page; return its key/title/image path.

    This is the input to the VLM-transcription pipeline: one whole-table image
    per table, no per-cell crops."""
    crops_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    with pw.sync_playwright() as p:
        browser, page = _open(p)
        page.goto(_as_url(source), wait_until="networkidle", timeout=60000)
        for info in page.evaluate(_TABLES_JS, min_numeric):
            img = crops_dir / f"{run_id}_{info['section_key']}_section.png"
            if _render_section(page, info["selector"], img):
                out.append({"section_key": info["section_key"], "section_title": info["title"],
                            "image": str(img), "page": None})
        browser.close()
    return out


def main() -> None:
    source, mode, crops_dir, out_json = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4])
    run_id = pathlib.Path(out_json).stem
    if mode != "tables":
        raise SystemExit(f"unknown mode {mode!r} (only 'tables' is supported)")
    out_json.write_text(json.dumps(list_tables(source, crops_dir, run_id), indent=2))
    print(f"listed {len(json.loads(out_json.read_text()))} tables -> {out_json}")


if __name__ == "__main__":
    main()
