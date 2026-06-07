"""Screenshot every data table from the gpt-oss model card, transcribe with
Claude, and write a side-by-side HTML report: screenshot | extracted numbers."""

import base64
import csv
import io
import pathlib
import re
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import playwright.sync_api as pw
from llm_metrics.extract_html import _TABLES_JS, _render_section, VIEWPORT, SCALE
from llm_metrics.vlm_table import transcribe_raw, parse_csv, _strip_fences

URL = "https://deploymentsafety.openai.com/gpt-oss"
OUT_DIR = ROOT / "var" / "gpt_oss_report"
REPORT = OUT_DIR / "report.html"

# Use the headless shell binary that's already installed in this container.
_HEADLESS_SHELL = "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell"


def list_tables_local(url: str, crops_dir: pathlib.Path, run_id: str,
                      min_numeric: int = 3) -> list[dict]:
    """Like extract_html.list_tables but launches with the explicit binary path."""
    crops_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_HEADLESS_SHELL)
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=SCALE,
                                ignore_https_errors=True)
        page.goto(url, wait_until="networkidle", timeout=60000)
        for info in page.evaluate(_TABLES_JS, min_numeric):
            img = crops_dir / f"{run_id}_{info['section_key']}_section.png"
            if _render_section(page, info["selector"], img):
                out.append({"section_key": info["section_key"],
                            "section_title": info["title"],
                            "image": str(img), "page": None})
        browser.close()
    return out


def img_data_uri(path: pathlib.Path) -> str:
    b64 = base64.standard_b64encode(path.read_bytes()).decode()
    return f"data:image/png;base64,{b64}"


_is_numeric = re.compile(r"^[<>~≤≥]?\s*[-+($]?\$?\.?\d").match


def _block_to_table_html(block_text: str) -> tuple[str, int]:
    """Render one CSV block as an HTML <table>. Returns (html, numeric_cell_count)."""
    rows = [r for r in csv.reader(io.StringIO(block_text)) if any(c.strip() for c in r)]
    if not rows:
        return "", 0

    n_numeric = sum(
        1 for r in rows[1:] for i, v in enumerate(r)
        if i > 0 and _is_numeric(v.strip())
    )

    def cell(tag: str, content: str, numeric: bool = False) -> str:
        cls = " class='num'" if numeric else ""
        return f"<{tag}{cls}>{content}</{tag}>"

    html = "<table class='data'><thead><tr>"
    for h in rows[0]:
        html += cell("th", h)
    html += "</tr></thead><tbody>"
    for r in rows[1:]:
        html += "<tr>"
        for i, v in enumerate(r):
            html += cell("td", v, numeric=(i > 0 and bool(_is_numeric(v.strip()))))
        html += "</tr>"
    html += "</tbody></table>"
    return html, n_numeric


def csv_to_grid_html(raw_csv: str) -> tuple[str, int]:
    """Render the raw CSV as HTML, handling sub-tables separated by blank lines.
    Returns (html_string, total_numeric_cell_count)."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", _strip_fences(raw_csv)) if b.strip()]
    if not blocks:
        return "<p class='empty'>No data extracted.</p>", 0

    parts, total = [], 0
    for block in blocks:
        h, n = _block_to_table_html(block)
        if h:
            parts.append(h)
            total += n

    return "\n".join(parts) if parts else "<p class='empty'>No data extracted.</p>", total


def build_html(tables: list[dict], csvs: list[str]) -> str:
    sections = []
    for t, raw_csv in zip(tables, csvs):
        title = t["section_title"] or t["section_key"]
        img_uri = img_data_uri(pathlib.Path(t["image"]))

        data_html, n_numeric = csv_to_grid_html(raw_csv)

        sections.append(textwrap.dedent(f"""
        <section>
          <h2>{title}</h2>
          <div class='pair'>
            <div class='screenshot'>
              <p class='label'>Screenshot</p>
              <img src='{img_uri}' alt='table screenshot'>
            </div>
            <div class='extracted'>
              <p class='label'>Extracted numbers ({n_numeric} numeric cells)</p>
              {data_html}
            </div>
          </div>
          <details>
            <summary>Raw CSV from Claude</summary>
            <pre>{raw_csv}</pre>
          </details>
        </section>
        """))

    body = "\n".join(sections)
    return textwrap.dedent(f"""
    <!DOCTYPE html>
    <html lang='en'>
    <head>
      <meta charset='utf-8'>
      <title>gpt-oss model card — extracted tables</title>
      <style>
        body {{ font-family: system-ui, sans-serif; max-width: 1600px; margin: 0 auto; padding: 1rem; }}
        h1 {{ font-size: 1.4rem; }}
        h2 {{ font-size: 1.1rem; border-bottom: 1px solid #ccc; padding-bottom: .3rem; }}
        .pair {{ display: flex; gap: 2rem; align-items: flex-start; margin: 1rem 0; }}
        .pair > div {{ flex: 1 1 0; min-width: 0; }}
        .screenshot img {{ max-width: 100%; border: 1px solid #ddd; }}
        .label {{ font-size: .8rem; color: #666; margin: 0 0 .3rem; }}
        .extracted {{ overflow-x: auto; }}
        table.data {{ border-collapse: collapse; font-size: .8rem; white-space: nowrap; }}
        table.data th, table.data td {{ border: 1px solid #ddd; padding: .2rem .45rem; }}
        table.data th {{ background: #f0f0f0; text-align: left; }}
        table.data td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
        .empty {{ color: #888; font-style: italic; }}
        details {{ margin-top: .5rem; }}
        pre {{ background: #f8f8f8; padding: .5rem; overflow-x: auto; font-size: .8rem; }}
        section {{ margin-bottom: 3rem; }}
      </style>
    </head>
    <body>
      <h1>gpt-oss model card — tables extracted with Claude ({URL})</h1>
      {body}
    </body>
    </html>
    """).strip()


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Re-use cached screenshots if they exist; re-screenshot otherwise.
    cached = sorted(OUT_DIR.glob("gpt_oss_t*_section.png"))
    if cached:
        print(f"Using {len(cached)} cached screenshots")
        tables = [{"section_key": p.stem.removeprefix("gpt_oss_").removesuffix("_section"),
                   "section_title": "", "image": str(p), "page": None}
                  for p in cached]
    else:
        print(f"Screenshotting tables from {URL} …")
        tables = list_tables_local(URL, OUT_DIR, "gpt_oss")
        print(f"Found {len(tables)} data tables")

    csvs = []
    for t in tables:
        img_path = pathlib.Path(t["image"])
        csv_cache = img_path.with_suffix(".csv")
        if csv_cache.exists():
            print(f"  {t['section_key']}: using cached CSV")
            raw = csv_cache.read_text()
        else:
            print(f"  Transcribing {t['section_key']} ({img_path.name}) …")
            raw = transcribe_raw(img_path)
            csv_cache.write_text(raw)
            parsed = parse_csv(raw)
            print(f"    → {len(parsed)} numeric cells")
        csvs.append(raw)

    html = build_html(tables, csvs)
    REPORT.write_text(html, encoding="utf-8")
    print(f"\nReport written → {REPORT}")


if __name__ == "__main__":
    main()
