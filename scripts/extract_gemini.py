"""Run the long-format VLM extraction on a model-card PDF, one call per page.

DeepMind model cards use borderless tables that pdfplumber's line/text table
detection misses, so instead of detecting tables we render each whole PDF page
to an image and send it once to Claude with the long-format prompt. Every page's
numbers fold into the same normalized frame + report as the gpt-oss pipeline;
pages with no tabular numbers drop out.

Reuses the source-agnostic core (parse / grid / report) from extract_gpt_oss.
"""

import csv
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import fitz
from llm_metrics import fetch
from llm_metrics.vlm_table import transcribe_raw
import extract_gpt_oss as g

URL = "https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf"
OUT_DIR = ROOT / "var" / "gemini3_report"
REPORT = OUT_DIR / "report.html"
COMBINED = OUT_DIR / "gemini3_long.csv"
RUN = "gemini3"
PAGE_SCALE = 3  # render PDF pages at 3x so small table text is legible to the VLM

# Same long schema as the table path, but framed for a whole PDF page that may
# hold one table, several, or none (prose-only pages return just the header).
_PAGE_PROMPT = (
    "You are extracting numeric data from ONE PAGE of a model-card PDF. The page "
    "may contain a table, several tables, or none. Output CSV and NOTHING else -- "
    "no prose, no fences.\n"
    "The first line must be exactly this header:\n"
    "row,col,model,condition,benchmark,metric,value\n"
    "Then emit ONE line per NUMERIC data cell in any table on the page. If the "
    "page has no tabular numeric data, output ONLY the header line.\n"
    "- value: the number exactly as printed (keep %, +/- signs, decimals; do not "
    "round or compute), but NEVER use thousands separators (write 2439, not 2,439) "
    "-- a comma would corrupt the CSV.\n"
    "- model: the model/system the column belongs to, from the column header "
    "(e.g. Gemini 3 Pro, Gemini 2.5 Pro, Claude Sonnet 4.5, GPT-5.2). Carry a "
    "spanned header across every column it covers.\n"
    "- condition: any setting attached to the column or row (e.g. with tools, no "
    "tools, pass@1, a shot count). EMPTY if none.\n"
    "- benchmark: the row label (the benchmark / metric / category).\n"
    "- metric: what the number measures -- from an explicit metric column, a "
    "header note, or the caption. If unclear use 'score'.\n"
    "- row: 0-based index of this cell's row in the page's data body, top to "
    "bottom across every table on the page (skip header rows).\n"
    "- col: 0-based index among the VALUE columns only; the leftmost value column "
    "is 0. Do NOT count label/description columns.\n"
    "Strip footnote markers from model/condition/benchmark/metric; keep value "
    "verbatim. One line per numeric cell."
)


def render_pages(url: str, out_dir: pathlib.Path, run: str,
                 scale: int = PAGE_SCALE) -> list[dict]:
    """Render each PDF page to a PNG (cached); return one section dict per page."""
    out_dir.mkdir(parents=True, exist_ok=True)
    local = fetch.local_copy(url, ROOT / "cache")
    doc = fitz.open(local)
    out = []
    for pi in range(len(doc)):
        img = out_dir / f"{run}_p{pi}_section.png"
        if not img.exists():
            doc[pi].get_pixmap(matrix=fitz.Matrix(scale, scale)).save(str(img))
        out.append({"section_key": f"p{pi}", "section_title": f"Page {pi + 1}",
                    "image": str(img), "page": pi})
    return out


def _verify(img_path: pathlib.Path, stem: pathlib.Path, records: list[dict],
            section_key: str) -> dict[int, str]:
    """Guided re-read (cached) -> {id: re-read value} for cells that disagree."""
    verify_path = pathlib.Path(f"{stem}.verify.csv")
    if verify_path.exists():
        verified = {int(r["id"]): r["value"] for r in csv.DictReader(verify_path.open())}
    else:
        print(f"    verifying {section_key} …")
        verified = g.verify_records(img_path, records)
        with verify_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "value"])
            for i in sorted(verified):
                w.writerow([i, verified[i]])
    return g.flagged_ids(records, verified)


def process_page(t: dict) -> tuple[dict, list[dict], list[dict]] | None:
    """One VLM call per page (+ one verification call). Returns (report-section,
    rows, flag-rows), or None if the page has no tabular numbers."""
    img_path = pathlib.Path(t["image"])
    stem = img_path.with_suffix("")
    long_path = pathlib.Path(f"{stem}.long.csv")
    grid_path = pathlib.Path(f"{stem}.grid.csv")
    raw_path = pathlib.Path(f"{stem}.long.raw.csv")

    if long_path.exists() and raw_path.exists():
        print(f"  {t['section_key']}: using cached long CSV")
        records, raw = g.read_long_csv(long_path), raw_path.read_text()
    else:
        print(f"  Reading {t['section_key']} ({img_path.name}) …")
        raw = transcribe_raw(img_path, prompt=_PAGE_PROMPT, max_tokens=8000)
        records = g.parse_records(raw)
        raw_path.write_text(raw)
        g.write_long_csv(records, long_path)
        print(f"    → {len(records)} numeric cells"
              + ("" if records else " (no table — skipped)"))

    if not records:
        return None
    grid = g.build_grid(records)
    g.write_grid_csv(grid, grid_path)
    id2value = {i: rec["value"] for i, rec in enumerate(records)}

    flags = _verify(img_path, stem, records, t["section_key"])
    if flags:
        print(f"    ⚑ {len(flags)} cell(s) flagged on re-read")

    label = f"Grid (cells reference the long CSV) — {len(records)} numeric cells"
    if flags:
        label += f" · <span style='color:#c00'>{len(flags)} flagged on re-read</span>"
    section = {"kind": "page", "title": t["section_title"],
               "image": t["image"], "raw": raw, "label": label,
               "values_html": g.grid_to_html(grid, id2value, flags)}
    rows = [{"kind": "page", "source": t["section_key"],
             **{k: rec[k] for k in g._FIG_FIELDS}} for rec in records]
    flag_rows = [{"source": t["section_key"], "benchmark": records[i]["benchmark"],
                  "model": records[i]["model"], "condition": records[i]["condition"],
                  "extracted": records[i]["value"], "reread": v}
                 for i, v in flags.items()]
    return section, rows, flag_rows


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pages = render_pages(URL, OUT_DIR, RUN)
    print(f"{len(pages)} pages rendered")

    sections: list[dict] = []
    combined: list[dict] = []
    all_flags: list[dict] = []
    for t in pages:
        result = process_page(t)
        if result is not None:
            section, rows, flag_rows = result
            sections.append(section)
            combined.extend(rows)
            all_flags.extend(flag_rows)

    cols = ["kind", "source", *g._FIG_FIELDS]
    with COMBINED.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in combined:
            w.writerow([r[c] for c in cols])

    # Disagreements between the first read and the guided re-read, for the user to adjudicate.
    flag_cols = ["source", "benchmark", "model", "condition", "extracted", "reread"]
    flags_csv = OUT_DIR / "gemini3_flags.csv"
    with flags_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(flag_cols)
        for r in all_flags:
            w.writerow([r[c] for c in flag_cols])

    heading = f"Gemini 3 Pro model card — normalized tables ({URL})"
    intro = ("Each PDF page was rendered and sent once to Claude with the "
             "long-format prompt (no pdfplumber table detection), then a second "
             "guided re-read verified each value. Pages with no tabular numbers "
             "are dropped. The combined frame (<code>kind, source, model, "
             "condition, benchmark, metric, value</code>) is "
             "<code>gemini3_long.csv</code>.")
    if all_flags:
        items = "".join(
            f"<li><b>{r['source']}</b> · {r['benchmark']} / {r['model']}"
            f"{(' / ' + r['condition']) if r['condition'] else ''}: extracted "
            f"<code>{r['extracted']}</code>, re-read <code>{r['reread']}</code></li>"
            for r in all_flags)
        intro += (f"<br><strong style='color:#c00'>{len(all_flags)} cell(s) flagged by "
                  "second-pass verification</strong> (first read vs an independent "
                  f"re-read — check these against the screenshot):<ul>{items}</ul>")

    REPORT.write_text(g.build_html(sections, heading=heading, intro=intro), encoding="utf-8")
    print(f"\nCombined frame → {COMBINED} ({len(combined)} rows from {len(sections)} pages)")
    print(f"Flags → {flags_csv} ({len(all_flags)} disagreements)")
    print(f"Report written → {REPORT}")


if __name__ == "__main__":
    main()
