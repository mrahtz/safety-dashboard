"""HTML structural extractor + crop renderer (P1).

Source of truth for HTML sources (section 2.2): deterministic, reads the DOM,
and for every numeric table cell returns its raw text, its on-screen rectangle
(via ``getBoundingClientRect``), an element selector, a tight highlighted crop,
and the surrounding context (column header, row label, caption, footnotes).

Runs as ``python -m llm_metrics.extract_html <source> <table_index> <crops_dir>
<out_json>`` so callers (Flask) can invoke it in a subprocess, keeping the sync
Playwright event loop out of the web server's worker threads. ``source`` may be
an http(s) URL or a local file path (a frozen copy, P3).
"""

import json
import pathlib
import sys

import playwright.sync_api as pw

from llm_metrics import ir, serde

VIEWPORT = {"width": 1280, "height": 2400}
SCALE = 2
PAD = 16  # px of surrounding context kept around each boxed cell

# Tags the numeric cells of one table and returns their text + context. We also
# collect page-level footnote lines (text starting with a footnote marker) so
# the caller can attach the ones whose marker appears in a given cell.
_EXTRACT_JS = r"""
(tableIndex) => {
  const tables = [...document.querySelectorAll('table')];
  const table = tables[tableIndex];
  if (!table) return {error: 'no table at index ' + tableIndex, n_tables: tables.length};
  const caption = (table.querySelector('caption')?.innerText || '').trim();
  const rows = [...table.querySelectorAll('tr')];
  const headerRow = rows.find(r => r.querySelector('th')) || rows[0];
  const headerCells = headerRow ? [...headerRow.children].map(c => c.innerText.trim()) : [];
  const footnotes = [...document.querySelectorAll('p,li,small,span,div,td')]
    .map(e => e.innerText.trim()).filter(t => /^[*†‡§¶]\s*\S/.test(t) && t.length < 400);
  const cells = []; let uid = 0;
  for (const row of rows) {
    if (row === headerRow) continue;
    const children = [...row.children];
    const rowLabel = (children[0]?.innerText || '').trim();
    children.forEach((cell, ci) => {
      const text = cell.innerText.trim();
      if (ci === 0 || !/^[-+($]?\d/.test(text)) return;     // skip row-label + non-numeric
      const id = 'llmcell-' + (uid++);
      cell.setAttribute('data-llm-id', id);
      cells.push({value_string: text, selector: '[data-llm-id="' + id + '"]',
                  column_header: headerCells[ci] || '', row_label: rowLabel, caption});
    });
  }
  return {caption, footnotes, n_cells: cells.length, cells};
}
"""

# Same idea as _EXTRACT_JS but across every table on the page (capped), so one
# browser session yields a card's full numeric content.
_EXTRACT_ALL_JS = r"""
(maxCells) => {
  const tables = [...document.querySelectorAll('table')];
  const cells = []; let uid = 0;
  tables.forEach((table, ti) => {
    const caption = (table.querySelector('caption')?.innerText || '').trim();
    const rows = [...table.querySelectorAll('tr')];
    const headerRow = rows.find(r => r.querySelector('th')) || rows[0];
    const headerCells = headerRow ? [...headerRow.children].map(c => c.innerText.trim()) : [];
    for (const row of rows) {
      if (row === headerRow) continue;
      const children = [...row.children];
      const rowLabel = (children[0]?.innerText || '').trim();
      children.forEach((cell, ci) => {
        const text = cell.innerText.trim();
        if (ci === 0 || !/^[-+($]?\d/.test(text) || cells.length >= maxCells) return;
        const id = 'llmcell-' + (uid++); cell.setAttribute('data-llm-id', id);
        cells.push({value_string: text, selector: '[data-llm-id="' + id + '"]',
                    column_header: headerCells[ci] || '', row_label: rowLabel, caption});
      });
    }
  });
  const footnotes = [...document.querySelectorAll('p,li,small,span,div,td')]
    .map(e => e.innerText.trim()).filter(t => /^[*†‡§¶]\s*\S/.test(t) && t.length < 400);
  return {n_tables: tables.length, n_cells: cells.length, cells, footnotes};
}
"""

_HIGHLIGHT_JS = """(r) => {
  const d = document.createElement('div'); d.id = '__llm_hl';
  Object.assign(d.style, {position:'fixed', left:r.x+'px', top:r.y+'px',
    width:r.w+'px', height:r.h+'px', border:'3px solid #e60023',
    boxShadow:'0 0 0 3px rgba(230,0,35,.25)', zIndex:2147483647, pointerEvents:'none'});
  document.body.appendChild(d);
}"""


def _as_url(source: str) -> str:
    if source.startswith(("http://", "https://", "file://")):
        return source
    return pathlib.Path(source).resolve().as_uri()


def _footnotes_for(value: str, page_footnotes: list[str]) -> tuple[str, ...]:
    marks = {ch for ch in value if ch in "*†‡§¶"}
    seen: dict[str, None] = {}  # dedup, preserve order
    for f in page_footnotes:
        if f and f[0] in marks:
            seen.setdefault(f, None)
    return tuple(seen)


def _render_crop(page: pw.Page, selector: str, out_path: pathlib.Path) -> tuple[float, float, float, float]:
    """Draw a tight box on the cell, screenshot a padded crop, return doc bbox."""
    page.query_selector(selector).scroll_into_view_if_needed()
    rect = page.eval_on_selector(selector, "e => { const r = e.getBoundingClientRect();"
                                 " return {x:r.x, y:r.y, w:r.width, h:r.height}; }")
    if rect["w"] <= 0 or rect["h"] <= 0:
        raise ValueError(f"degenerate rect for {selector}: {rect}")  # broken invariant (section 9)
    page.evaluate(_HIGHLIGHT_JS, rect)
    vw = page.viewport_size
    x, y = max(0.0, rect["x"] - PAD), max(0.0, rect["y"] - PAD)
    clip = {"x": x, "y": y,
            "width": min(rect["w"] + 2 * PAD, vw["width"] - x),
            "height": min(rect["h"] + 2 * PAD, vw["height"] - y)}
    page.screenshot(path=str(out_path), clip=clip)
    page.evaluate("() => document.getElementById('__llm_hl')?.remove()")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise RuntimeError(f"crop not written: {out_path}")  # broken invariant (section 9)
    sx, sy = page.evaluate("() => [window.scrollX, window.scrollY]")
    return (rect["x"] + sx, rect["y"] + sy, rect["x"] + sx + rect["w"], rect["y"] + sy + rect["h"])


def _cells_to_candidates(page, cells, footnotes, crops_dir, run_id) -> tuple[ir.Candidate, ...]:
    out: list[ir.Candidate] = []
    for i, cell in enumerate(cells):
        crop = crops_dir / f"{run_id}_c{i}.png"
        bbox = _render_crop(page, cell["selector"], crop)
        out.append(ir.Candidate(
            value_string=cell["value_string"],
            source_ref=ir.SourceRef(kind="html", page=None, selector=cell["selector"], bbox=bbox),
            crop_path=crop,
            context=ir.Context(column_header=cell["column_header"], row_label=cell["row_label"],
                               caption=cell["caption"],
                               footnotes=_footnotes_for(cell["value_string"], footnotes)),
        ))
    return tuple(out)


def _open(p):
    browser = p.chromium.launch()
    # The managed environment proxies TLS with a CA Chromium doesn't trust; ignore
    # cert errors so we can reach the (already-trusted) source hosts.
    return browser, browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE, ignore_https_errors=True)


def extract(source: str, table_index: int, crops_dir: pathlib.Path, run_id: str,
            max_cells: int = 60) -> tuple[ir.Candidate, ...]:
    """Extract one table (``table_index >= 0``) or every table (``-1``, capped)."""
    crops_dir.mkdir(parents=True, exist_ok=True)
    with pw.sync_playwright() as p:
        browser, page = _open(p)
        page.goto(_as_url(source), wait_until="networkidle", timeout=60000)
        if table_index < 0:
            data = page.evaluate(_EXTRACT_ALL_JS, max_cells)
        else:
            data = page.evaluate(_EXTRACT_JS, table_index)
            if "error" in data:
                raise ValueError(f"{data['error']} (page has {data['n_tables']} tables)")
        out = _cells_to_candidates(page, data["cells"], data["footnotes"], crops_dir, run_id)
        browser.close()
    return out


def main() -> None:
    source, table_index, crops_dir, out_json = sys.argv[1], int(sys.argv[2]), pathlib.Path(sys.argv[3]), pathlib.Path(sys.argv[4])
    cands = extract(source, table_index, crops_dir, run_id=pathlib.Path(out_json).stem)
    out_json.write_text(json.dumps([serde.candidate_to_dict(c) for c in cands], indent=2))
    print(f"extracted {len(cands)} candidates -> {out_json}")


if __name__ == "__main__":
    main()
