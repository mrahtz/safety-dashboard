"""Screenshot every data table AND labelled figure from the gpt-oss model card,
transcribe with Claude into a normalized long format, and emit:

  * one ``*_long.csv`` per table   -- the single source of the numbers, one row
    per numeric cell (id, row, col, model, condition, benchmark, metric, value),
  * one ``*_grid.csv`` per table   -- the paper's 2-D layout, whose data cells
    hold an ``#id`` REFERENCE into the long CSV (no values duplicated),
  * one ``*_long.csv`` per figure  -- the printed values read off a chart
    (id, model, condition, benchmark, metric, value),
  * ``gpt_oss_long.csv``           -- tables + figures concatenated, the
    standardized frame (kind, source, model, condition, benchmark, metric, value),
  * ``report.html``                -- screenshot | extracted values, side by side.

Same screenshot+VLM pipeline for both: tables are screenshots of ``<table>``
elements, figures are screenshots of ``<figure>`` elements. Charts without
printed value labels (scatter/line plots, text diagrams) yield no rows and are
dropped, so only graphs with actual numbers land in the frame. The numbers live
only in the long CSVs.
"""

import base64
import csv
import io
import json
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
    "not round or compute), but NEVER use thousands separators (write 2439, not "
    "2,439) -- a comma would corrupt the CSV.\n"
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

# Charts have no 2-D grid, so no row/col -- just the semantic axes + value. The
# model reads PRINTED bar/point labels (not pixel positions); if a figure has no
# printed values it must return the header alone, so it self-filters out.
_CHART_PROMPT = (
    "You are reading ONE figure (a chart) from an AI model/system card "
    "screenshot. Transcribe a number ONLY IF that exact number is written as "
    "text on the chart (a data label, e.g. printed above a bar). This is "
    "transcription, NOT reading a graph: if a bar/point has only a category "
    "label (like 'low'/'medium'/'high' or a model name) and no printed number, "
    "or you would have to judge its height against the y-axis / gridlines, then "
    "DO NOT output it. A scatter or line plot whose points are not annotated "
    "with their numeric value yields NO rows. Never estimate, round, or infer a "
    "value. If the figure has no printed numeric data labels at all (an "
    "unlabelled scatter/line plot, or a text/diagram), output ONLY the header "
    "line and nothing else.\n"
    "Output CSV and NOTHING else -- no prose, no fences. First line exactly:\n"
    "model,condition,benchmark,metric,value\n"
    "Then one line per printed value:\n"
    "- value: the number exactly as printed (keep % and decimals), but NEVER use "
    "thousands separators (write 2439, not 2,439) -- a comma would corrupt the CSV.\n"
    "- model: the model that bar/point belongs to, from its x-axis label or the "
    "legend -- the base model name (e.g. gpt-oss-120b, o4-mini, DeepSeek "
    "R1-0528). Carry a legend/subplot model across its bars.\n"
    "- condition: any setting attached to the bar/point or its subplot -- e.g. "
    "browsing, no browsing, with tools, without tools, low, medium, high, launch "
    "candidate, helpful-only. EMPTY if none.\n"
    "- benchmark: what is measured -- the chart title or the subplot/panel title "
    "(e.g. ProtocolQA Open-Ended, AIME 2024, GPQA Diamond).\n"
    "- metric: the y-axis label (e.g. pass@1, Accuracy (%), Elo). If unclear use "
    "'score'.\n"
    "Strip footnote markers. One line per printed numeric label; emit nothing for "
    "unlabelled marks."
)

# Find every <figure> that carries a chart image; tag, title, and note its image
# source (used to skip charts we know can't be transcribed -- see SKIP_FIG_SRC).
_FIGURES_JS = r"""
() => {
  const out = [];
  [...document.querySelectorAll('figure')].forEach((f, fi) => {
    const img = f.querySelector('img');
    if (!img) return;
    const skey = 'f' + fi;
    f.setAttribute('data-llm-fig', skey);
    const cap = f.querySelector('figcaption');
    out.push({section_key: skey, selector: '[data-llm-fig="' + skey + '"]',
              title: (cap ? cap.innerText : '').trim().slice(0, 160),
              src: img.currentSrc || img.src || ''});
  });
  return out;
}
"""

# Charts with no printed value labels -- the model estimates their marks off the
# axis no matter how firmly the prompt forbids it (an unlabelled scatter/line
# plot is reading a graph, not transcribing it), so we exclude them by image
# source. Matched as substrings of the figure's <img> src.
SKIP_FIG_SRC = ("scaling",)   # Fig 3: accuracy-vs-CoT-length scatter, points unlabelled

_FIELDS = ["row", "col", "model", "condition", "benchmark", "metric", "value"]
_FIG_FIELDS = ["model", "condition", "benchmark", "metric", "value"]
FIG_SCALE = 3  # charts render small in the page column; capture at 3x for legible labels
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


def list_figures_local(url: str, crops_dir: pathlib.Path, run_id: str) -> list[dict]:
    """Screenshot each <figure> (whole element incl. caption) at FIG_SCALE."""
    crops_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    with pw.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_HEADLESS_SHELL)
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=FIG_SCALE,
                                ignore_https_errors=True)
        page.goto(url, wait_until="networkidle", timeout=60000)
        for _ in range(16):                       # scroll to trigger lazy figure images
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(250)
        page.wait_for_timeout(800)
        for info in page.evaluate(_FIGURES_JS):
            img = crops_dir / f"{run_id}_{info['section_key']}_section.png"
            if _render_section(page, info["selector"], img):
                # Persist caption + src so a cached re-run keeps them (the PNG alone loses them).
                img.with_suffix(".meta.json").write_text(json.dumps(
                    {"section_title": info["title"], "src": info["src"]}))
                out.append({"section_key": info["section_key"],
                            "section_title": info["title"], "src": info["src"],
                            "image": str(img), "page": None})
        browser.close()
    return out


def _load_meta(image: str) -> dict:
    meta = pathlib.Path(image).with_suffix(".meta.json")
    return json.loads(meta.read_text()) if meta.exists() else {}


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


def parse_chart_records(raw: str) -> list[dict]:
    """VLM chart-CSV output -> list of {model, condition, benchmark, metric,
    value}. Drops anything whose value isn't numeric (so unlabelled charts and
    text figures collapse to nothing)."""
    reader = csv.DictReader(io.StringIO(_strip_fences(raw)))
    out: list[dict] = []
    for r in reader:
        val = (r.get("value") or "").strip()
        if not _is_numeric(val):
            continue
        out.append({"model": (r.get("model") or "").strip(),
                    "condition": (r.get("condition") or "").strip(),
                    "benchmark": (r.get("benchmark") or "").strip(),
                    "metric": (r.get("metric") or "").strip(),
                    "value": val})
    return out


def write_fig_long_csv(records: list[dict], path: pathlib.Path) -> None:
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", *_FIG_FIELDS])
        for i, rec in enumerate(records):
            w.writerow([i, *(rec[k] for k in _FIG_FIELDS)])


def read_fig_long_csv(path: pathlib.Path) -> list[dict]:
    return [{k: r[k] for k in _FIG_FIELDS} for r in csv.DictReader(path.open())]


# ---------------------------------------------------------------------------
# Second-pass verification: re-read each cell from the image guided ONLY by its
# (model, condition, benchmark) identity -- never the extracted value -- and flag
# where the fresh read disagrees. An independent read, not a "do you agree?".
# ---------------------------------------------------------------------------
_VERIFY_PROMPT = (
    "You are VERIFYING numbers transcribed from this image. Below is a list of "
    "cells, each with an id and its (model, condition, benchmark). For EACH id, "
    "locate that exact cell in the image and read the number printed there fresh "
    "-- do NOT assume or guess a value, read what is shown. Output CSV and "
    "NOTHING else, first line exactly:\n"
    "id,value\n"
    "then one line per id, value copied exactly as printed (keep %, +/- signs, "
    "decimals). Leave value empty if you cannot find that cell.\n\n"
    "Cells to verify:\n"
)


def _verify_block(records: list[dict]) -> str:
    lines = ["id | model | condition | benchmark"]
    for i, r in enumerate(records):
        lines.append(f"{i} | {r['model']} | {r.get('condition', '')} | {r['benchmark']}")
    return "\n".join(lines)


_NUM_TOKEN = re.compile(r"[-+]?\$?\s*\d[\d,]*\.?\d*")


def _norm_value(v: str) -> str:
    """Compare by the leading numeric token only, so trailing annotations
    ('+0.2% (non-egregious)') and formatting ('$5,478.16') don't cause false
    disagreements."""
    v = v.replace("−", "-").replace("–", "-")
    m = _NUM_TOKEN.search(v)
    if not m:
        return re.sub(r"\s+", "", v).lower()
    return re.sub(r"[\s,$+]", "", m.group(0))


def verify_records(image_path, records: list[dict], max_tokens: int = 4000) -> dict[int, str]:
    """Guided re-read: returns {id: freshly-read value} for the given records."""
    if not records:
        return {}
    raw = transcribe_raw(pathlib.Path(image_path),
                         prompt=_VERIFY_PROMPT + _verify_block(records), max_tokens=max_tokens)
    out: dict[int, str] = {}
    # Split on the FIRST comma only -- values themselves can contain commas
    # ('$5,478.16'), which a naive CSV parse would truncate.
    for line in _strip_fences(raw).splitlines():
        line = line.strip()
        if not line or "," not in line or line.lower().startswith("id"):
            continue
        sid, val = line.split(",", 1)
        try:
            out[int(sid.strip())] = val.strip()
        except ValueError:
            continue
    return out


def flagged_ids(records: list[dict], verified: dict[int, str]) -> dict[int, str]:
    """ids whose fresh re-read disagrees with the extracted value -> the re-read."""
    flags: dict[int, str] = {}
    for i, rec in enumerate(records):
        v = verified.get(i)
        if v and _norm_value(rec["value"]) != _norm_value(v):
            flags[i] = v
    return flags


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
def grid_to_html(grid: list[list[str]], id2value: dict[int, str],
                 flags: dict[int, str] | None = None) -> str:
    """Render the grid, de-referencing #id cells to their value from the long CSV.
    Cells whose id is in ``flags`` are marked (the value the re-read disagreed on
    is shown on hover)."""
    if not grid:
        return "<p class='empty'>No data extracted.</p>"
    flags = flags or {}
    n_head = 1 + (1 if len(grid) > 1 and any(grid[1][1:]) and not any(
        c.startswith("#") for c in grid[1][1:]) else 0)

    def render_cell(tag, text):
        cid = int(text[1:]) if text.startswith("#") else None
        if cid is not None:
            text = id2value.get(cid, "?")
        classes = (["num"] if cid is not None and _is_numeric(text) else [])
        title = ""
        if cid in flags:
            classes.append("flag")
            title = f" title='re-read as: {flags[cid]}'"
        cls = f" class='{' '.join(classes)}'" if classes else ""
        return f"<{tag}{cls}{title}>{text}</{tag}>"

    html = "<table class='data'><thead>"
    for hr in grid[:n_head]:
        html += "<tr>" + "".join(render_cell("th", c) for c in hr) + "</tr>"
    html += "</thead><tbody>"
    for r in grid[n_head:]:
        html += "<tr>" + render_cell("th", r[0]) + "".join(
            render_cell("td", c) for c in r[1:]) + "</tr>"
    html += "</tbody></table>"
    return html


def records_to_values_html(records: list[dict]) -> str:
    """Plain values table for a figure (no paper grid to reconstruct)."""
    if not records:
        return "<p class='empty'>No printed values.</p>"
    html = ("<table class='data'><thead><tr><th>Model</th><th>Condition</th>"
            "<th>Benchmark</th><th>Metric</th><th>Value</th></tr></thead><tbody>")
    for r in records:
        vcls = " class='num'" if _is_numeric(r["value"]) else ""
        html += (f"<tr><td>{r['model']}</td><td>{r['condition']}</td>"
                 f"<td>{r['benchmark']}</td><td>{r['metric']}</td>"
                 f"<td{vcls}>{r['value']}</td></tr>")
    html += "</tbody></table>"
    return html


def build_html(sections: list[dict], heading: str | None = None,
               intro: str | None = None) -> str:
    """Render generic sections (table/figure/page): screenshot | extracted values."""
    heading = heading or f"gpt-oss model card — normalized tables &amp; figures ({URL})"
    intro = intro or (
        "Tables and labelled figures, extracted by the same screenshot+VLM "
        "pipeline into one schema. Tables also get a grid CSV whose cells "
        "reference their long CSV; figures hold only printed values. The combined "
        "frame (<code>kind, source, model, condition, benchmark, metric, value</code>) "
        "is <code>gpt_oss_long.csv</code>.")
    blocks = []
    for s in sections:
        img_uri = img_data_uri(pathlib.Path(s["image"]))
        badge = f"<span class='badge {s['kind']}'>{s['kind']}</span>"
        blocks.append(textwrap.dedent(f"""
        <section>
          <h2>{badge} {s['title']}</h2>
          <div class='pair'>
            <div class='screenshot'>
              <p class='label'>Screenshot</p>
              <img src='{img_uri}' alt='{s['kind']} screenshot'>
            </div>
            <div class='extracted'>
              <p class='label'>{s['label']}</p>
              {s['values_html']}
            </div>
          </div>
          <details>
            <summary>Raw CSV from Claude</summary>
            <pre>{s['raw']}</pre>
          </details>
        </section>
        """))
    body = "\n".join(blocks)
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
        table.data td.flag, table.data th.flag {{ background: #ffe0e0; outline: 2px solid #e03030;
            cursor: help; }}
        .empty {{ color: #888; font-style: italic; }}
        details {{ margin-top: .5rem; }}
        pre {{ background: #f8f8f8; padding: .5rem; overflow-x: auto; font-size: .8rem; }}
        section {{ margin-bottom: 3rem; }}
        .badge {{ font-size: .7rem; text-transform: uppercase; letter-spacing: .04em;
                  padding: .1rem .4rem; border-radius: .25rem; vertical-align: middle; }}
        .badge.table {{ background: #e3f0ff; color: #1a4f8a; }}
        .badge.figure {{ background: #fff0db; color: #8a5a1a; }}
        .badge.page {{ background: #e8f5e9; color: #2e6b32; }}
      </style>
    </head>
    <body>
      <h1>{heading}</h1>
      <p>{intro}</p>
      {body}
    </body>
    </html>
    """).strip()


# ---------------------------------------------------------------------------
def _cached_or_screenshot(glob_pat: str, lister, label: str) -> list[dict]:
    """Reuse cached *_section.png screenshots if present, else screenshot fresh."""
    cached = sorted(OUT_DIR.glob(glob_pat))
    if cached:
        print(f"Using {len(cached)} cached {label} screenshots")
        return [{"section_key": p.stem.removeprefix("gpt_oss_").removesuffix("_section"),
                 "section_title": "", "image": str(p), "page": None, **_load_meta(str(p))}
                for p in cached]
    print(f"Screenshotting {label} from {URL} …")
    items = lister(URL, OUT_DIR, "gpt_oss")
    print(f"Found {len(items)} {label}")
    return items


def process_table(t: dict) -> tuple[dict, list[dict]]:
    """Returns (report-section, combined-rows) for one table."""
    img_path = pathlib.Path(t["image"])
    stem = img_path.with_suffix("")
    long_path, grid_path = pathlib.Path(f"{stem}.long.csv"), pathlib.Path(f"{stem}.grid.csv")
    raw_path = pathlib.Path(f"{stem}.long.raw.csv")

    if long_path.exists() and raw_path.exists():
        print(f"  {t['section_key']}: using cached long CSV")
        records, raw = read_long_csv(long_path), raw_path.read_text()
    else:
        print(f"  Transcribing table {t['section_key']} ({img_path.name}) …")
        raw = transcribe_raw(img_path, prompt=_LONG_PROMPT, max_tokens=8000)
        records = parse_records(raw)
        raw_path.write_text(raw)
        write_long_csv(records, long_path)
        print(f"    → {len(records)} numeric cells")

    grid = build_grid(records)
    write_grid_csv(grid, grid_path)
    id2value = {i: rec["value"] for i, rec in enumerate(records)}
    section = {"kind": "table", "title": t["section_title"] or t["section_key"],
               "image": t["image"], "raw": raw,
               "label": f"Grid (cells reference the long CSV) — {len(records)} numeric cells",
               "values_html": grid_to_html(grid, id2value)}
    rows = [{"kind": "table", "source": t["section_key"],
             **{k: rec[k] for k in _FIG_FIELDS}} for rec in records]
    return section, rows


def process_figure(t: dict) -> tuple[dict, list[dict]] | None:
    """Returns (report-section, combined-rows), or None if the figure has no
    printed values (so only graphs with actual numbers are kept)."""
    src = t.get("src", "")
    if any(p in src for p in SKIP_FIG_SRC):
        print(f"  {t['section_key']}: unlabelled scatter/line plot "
              f"({src.rsplit('/', 1)[-1]}) — skipped")
        return None

    img_path = pathlib.Path(t["image"])
    stem = img_path.with_suffix("")
    long_path, raw_path = pathlib.Path(f"{stem}.long.csv"), pathlib.Path(f"{stem}.long.raw.csv")

    if long_path.exists() and raw_path.exists():
        print(f"  {t['section_key']}: using cached long CSV")
        records, raw = read_fig_long_csv(long_path), raw_path.read_text()
    else:
        print(f"  Reading figure {t['section_key']} ({img_path.name}) …")
        raw = transcribe_raw(img_path, prompt=_CHART_PROMPT, max_tokens=4000)
        records = parse_chart_records(raw)
        raw_path.write_text(raw)
        write_fig_long_csv(records, long_path)
        print(f"    → {len(records)} printed values"
              + ("" if records else " (no numbers — skipped)"))

    if not records:
        return None
    section = {"kind": "figure", "title": t["section_title"] or t["section_key"],
               "image": t["image"], "raw": raw,
               "label": f"Extracted values — {len(records)} printed numbers",
               "values_html": records_to_values_html(records)}
    rows = [{"kind": "figure", "source": t["section_key"],
             **{k: rec[k] for k in _FIG_FIELDS}} for rec in records]
    return section, rows


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    tables = _cached_or_screenshot("gpt_oss_t*_section.png", list_tables_local, "tables")
    figures = _cached_or_screenshot("gpt_oss_f*_section.png", list_figures_local, "figures")

    sections: list[dict] = []
    combined: list[dict] = []
    for t in tables:
        section, rows = process_table(t)
        sections.append(section)
        combined.extend(rows)
    for t in figures:
        result = process_figure(t)
        if result is not None:
            section, rows = result
            sections.append(section)
            combined.extend(rows)

    # The standardized frame: every number from every table + labelled figure.
    cols = ["kind", "source", *_FIG_FIELDS]
    with COMBINED.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in combined:
            w.writerow([r[c] for c in cols])

    REPORT.write_text(build_html(sections), encoding="utf-8")
    n_tab = sum(r["kind"] == "table" for r in combined)
    n_fig = len(combined) - n_tab
    print(f"\nCombined frame → {COMBINED} ({len(combined)} rows: {n_tab} table, {n_fig} figure)")
    print(f"Report written → {REPORT}")


if __name__ == "__main__":
    main()
