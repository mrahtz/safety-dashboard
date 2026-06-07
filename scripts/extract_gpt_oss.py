"""Screenshot every data table from the gpt-oss model card, transcribe with
Claude into a normalized long format, and emit:

  * one ``*_long.csv`` per table  -- the single source of the numbers, one row
    per numeric cell (id, row, col, model, condition, benchmark, metric, value),
  * one ``*_grid.csv`` per table   -- the paper's 2-D layout, whose data cells
    hold an ``#id`` REFERENCE into the long CSV (no values duplicated),
  * ``gpt_oss_long.csv``            -- all tables concatenated, the standardized
    dataframe (table, model, condition, benchmark, metric, value),
  * ``report.html``                -- screenshot | grid (de-referenced) side by side.

One VLM call per table; the numbers live only in the long CSV.
"""

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
from llm_metrics.vlm_table import transcribe_raw, _strip_fences

URL = "https://deploymentsafety.openai.com/gpt-oss"
OUT_DIR = ROOT / "var" / "gpt_oss_report"
REPORT = OUT_DIR / "report.html"
COMBINED = OUT_DIR / "gpt_oss_long.csv"

# Use the headless shell binary that's already installed in this container.
_HEADLESS_SHELL = "/opt/pw-browsers/chromium_headless_shell-1194/chrome-linux/headless_shell"

# One numeric cell per output line, tagged with its grid position (the "xy") and
# its semantic axes, so we can rebuild both the long frame and the paper layout.
_LONG_PROMPT = (
    "You are extracting ONE table from an AI model/system card screenshot into a "
    "normalized long format. Output CSV and NOTHING else -- no prose, no fences.\n"
    "The first line must be exactly this header:\n"
    "row,col,model,condition,benchmark,metric,value\n"
    "Then emit ONE line per NUMERIC data cell in the table. For each cell:\n"
    "- value: the number exactly as printed (keep %, +/- signs and decimals; do "
    "not round or compute).\n"
    "- model: the model the column belongs to, from the column header; carry a "
    "spanned model header across every column it covers.\n"
    "- condition: the reasoning level / setting for that column (e.g. low, "
    "medium, high). Leave EMPTY if the table has no such axis.\n"
    "- benchmark: the row label (the benchmark / category / language).\n"
    "- metric: what the number measures -- from an explicit metric column, a "
    "corner label like 'Benchmark (Accuracy (%))', or the caption. If unclear "
    "use 'score'.\n"
    "- row: 0-based index of this cell's row in the data body, counting every "
    "data row top to bottom INCLUDING across sub-tables (skip header rows).\n"
    "- col: 0-based index among the VALUE columns only; the leftmost value "
    "column is 0. Do NOT count label columns.\n"
    "Strip footnote markers (*, daggers, superscripts) from model/condition/"
    "benchmark/metric, but keep value verbatim. Every numeric cell gets exactly "
    "one line."
)

_FIELDS = ["row", "col", "model", "condition", "benchmark", "metric", "value"]
_is_numeric = re.compile(r"^[<>~≤≥]?\s*[-+($]?\$?\.?\d").match


# ---------------------------------------------------------------------------
# Screenshots (unchanged path, explicit Chromium binary for this container)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Long records  <->  the two on-disk CSVs
# ---------------------------------------------------------------------------
def parse_records(raw: str) -> list[dict]:
    """VLM long-CSV output -> list of {row, col, model, condition, benchmark,
    metric, value}. Drops anything whose value isn't numeric."""
    reader = csv.DictReader(io.StringIO(_strip_fences(raw)))
    out: list[dict] = []
    for r in reader:
        if not r or not (r.get("value") or "").strip():
            continue
        val = r["value"].strip()
        if not _is_numeric(val):
            continue
        try:
            row, col = int(r["row"]), int(r["col"])
        except (TypeError, ValueError):
            continue
        out.append({"row": row, "col": col,
                    "model": (r.get("model") or "").strip(),
                    "condition": (r.get("condition") or "").strip(),
                    "benchmark": (r.get("benchmark") or "").strip(),
                    "metric": (r.get("metric") or "").strip(),
                    "value": val})
    return out


def write_long_csv(records: list[dict], path: pathlib.Path) -> None:
    """The single source of the numbers: id + position + axes + value."""
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", *_FIELDS])
        for i, rec in enumerate(records):
            w.writerow([i, *(rec[k] for k in _FIELDS)])


def read_long_csv(path: pathlib.Path) -> list[dict]:
    out = []
    for r in csv.DictReader(path.open()):
        out.append({"row": int(r["row"]), "col": int(r["col"]),
                    "model": r["model"], "condition": r["condition"],
                    "benchmark": r["benchmark"], "metric": r["metric"],
                    "value": r["value"]})
    return out


def build_grid(records: list[dict]) -> list[list[str]]:
    """Reconstruct the paper's 2-D layout with #id references in data cells.

    Column headers come from each column's (model, condition); row labels come
    from each row's benchmark, suffixed with [metric] when the metric varies
    down the table (e.g. accuracy vs hallucination rate, or Score vs Elo)."""
    if not records:
        return []
    n_rows = max(r["row"] for r in records) + 1
    n_cols = max(r["col"] for r in records) + 1

    # Per-column model/condition (first non-empty wins) and per-row benchmark/metric.
    col_model = [""] * n_cols
    col_cond = [""] * n_cols
    row_bench = [""] * n_rows
    row_metric = [""] * n_rows
    cell_id: dict[tuple[int, int], int] = {}
    for i, rec in enumerate(records):
        c, rw = rec["col"], rec["row"]
        col_model[c] = col_model[c] or rec["model"]
        col_cond[c] = col_cond[c] or rec["condition"]
        row_bench[rw] = row_bench[rw] or rec["benchmark"]
        row_metric[rw] = row_metric[rw] or rec["metric"]
        cell_id[(rw, c)] = i

    metric_varies = len({m for m in row_metric if m}) > 1
    has_cond = any(col_cond)

    grid: list[list[str]] = []
    grid.append(["", *col_model])                       # model header row
    if has_cond:
        grid.append(["", *col_cond])                    # condition header row
    for rw in range(n_rows):
        label = row_bench[rw]
        if metric_varies and row_metric[rw]:
            label = f"{label} [{row_metric[rw]}]"
        body = [f"#{cell_id[(rw, c)]}" if (rw, c) in cell_id else "" for c in range(n_cols)]
        grid.append([label, *body])
    return grid


def write_grid_csv(grid: list[list[str]], path: pathlib.Path) -> None:
    with path.open("w", newline="") as f:
        csv.writer(f).writerows(grid)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def grid_to_html(grid: list[list[str]], id2value: dict[int, str]) -> str:
    """Render the grid, de-referencing #id cells to their value from the long CSV."""
    if not grid:
        return "<p class='empty'>No data extracted.</p>"
    n_head = 1 + (1 if len(grid) > 1 and any(grid[1][1:]) and not any(
        c.startswith("#") for c in grid[1][1:]) else 0)

    def render_cell(tag, text):
        ref = text.startswith("#")
        if ref:
            text = id2value.get(int(text[1:]), "?")
        num = ref and bool(_is_numeric(text))
        cls = " class='num'" if num else ""
        return f"<{tag}{cls}>{text}</{tag}>"

    html = "<table class='data'><thead>"
    for hr in grid[:n_head]:
        html += "<tr>" + "".join(render_cell("th", c) for c in hr) + "</tr>"
    html += "</thead><tbody>"
    for r in grid[n_head:]:
        html += "<tr>" + render_cell("th", r[0]) + "".join(
            render_cell("td", c) for c in r[1:]) + "</tr>"
    html += "</tbody></table>"
    return html


def build_html(tables: list[dict], grids: list[list[list[str]]],
               id2values: list[dict[int, str]], counts: list[int],
               raws: list[str]) -> str:
    sections = []
    for t, grid, id2value, n, raw in zip(tables, grids, id2values, counts, raws):
        title = t["section_title"] or t["section_key"]
        img_uri = img_data_uri(pathlib.Path(t["image"]))
        data_html = grid_to_html(grid, id2value)
        sections.append(textwrap.dedent(f"""
        <section>
          <h2>{title}</h2>
          <div class='pair'>
            <div class='screenshot'>
              <p class='label'>Screenshot</p>
              <img src='{img_uri}' alt='table screenshot'>
            </div>
            <div class='extracted'>
              <p class='label'>Grid (cells reference the long CSV) — {n} numeric cells</p>
              {data_html}
            </div>
          </div>
          <details>
            <summary>Raw long CSV from Claude</summary>
            <pre>{raw}</pre>
          </details>
        </section>
        """))
    body = "\n".join(sections)
    return textwrap.dedent(f"""
    <!DOCTYPE html>
    <html lang='en'>
    <head>
      <meta charset='utf-8'>
      <title>gpt-oss model card — normalized tables</title>
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
      <h1>gpt-oss model card — normalized tables ({URL})</h1>
      <p>Each table has a long CSV (the data, one row per cell) and a grid CSV
      whose cells reference it; the combined frame is <code>gpt_oss_long.csv</code>.</p>
      {body}
    </body>
    </html>
    """).strip()


# ---------------------------------------------------------------------------
def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

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

    grids, id2values, counts, raws = [], [], [], []
    combined: list[dict] = []
    for t in tables:
        img_path = pathlib.Path(t["image"])
        stem = img_path.with_suffix("")
        long_path = pathlib.Path(f"{stem}.long.csv")
        grid_path = pathlib.Path(f"{stem}.grid.csv")
        raw_path = pathlib.Path(f"{stem}.long.raw.csv")

        if long_path.exists() and raw_path.exists():
            print(f"  {t['section_key']}: using cached long CSV")
            records = read_long_csv(long_path)
            raw = raw_path.read_text()
        else:
            print(f"  Transcribing {t['section_key']} ({img_path.name}) …")
            raw = transcribe_raw(img_path, prompt=_LONG_PROMPT, max_tokens=8000)
            records = parse_records(raw)
            raw_path.write_text(raw)
            write_long_csv(records, long_path)
            print(f"    → {len(records)} numeric cells")

        grid = build_grid(records)
        write_grid_csv(grid, grid_path)
        id2value = {i: rec["value"] for i, rec in enumerate(records)}

        grids.append(grid)
        id2values.append(id2value)
        counts.append(len(records))
        raws.append(raw)
        for rec in records:
            combined.append({"table": t["section_key"], **{k: rec[k] for k in
                             ("model", "condition", "benchmark", "metric", "value")}})

    # The standardized dataframe: every number from every table, one row each.
    with COMBINED.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["table", "model", "condition", "benchmark", "metric", "value"])
        for r in combined:
            w.writerow([r["table"], r["model"], r["condition"], r["benchmark"],
                        r["metric"], r["value"]])

    REPORT.write_text(build_html(tables, grids, id2values, counts, raws), encoding="utf-8")
    print(f"\nCombined frame → {COMBINED} ({len(combined)} rows)")
    print(f"Report written → {REPORT}")


if __name__ == "__main__":
    main()
