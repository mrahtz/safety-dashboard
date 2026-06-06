"""Web UI for the ingestion pipeline. Grows across prototypes:

- P1: paste an HTML URL -> list of extracted number-strings, each beside a tight
  highlighted crop of exactly where it came from, with its context and the
  OCR-presence self-check (section 4.4).
- P2 adds the PDF path; P3 adds DB review; P4 the VLM column; P5 the dashboard.
"""

import json
import pathlib

import flask
import markupsafe

from llm_metrics import db, extract_pdf, ocr, paths, runner

app = flask.Flask(__name__)


def _conn():
    return db.connect()

PRESETS = [
    {"kind": "html", "label": "GPT-5.5 system card (HTML, Deployment Safety Hub)",
     "source": "https://deploymentsafety.openai.com/gpt-5-5", "table_index": 0},
    {"kind": "pdf", "label": "Gemini 3 Pro model card (PDF, safety-eval table)",
     "source": "https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf",
     "table_index": 0, "page": 7},
]

_BASE = """<!doctype html><meta charset=utf-8><title>safety-dashboard</title>
<style>
 body{{font:15px/1.5 system-ui,sans-serif;margin:0;color:#111;background:#f6f7f9}}
 header{{background:#111;color:#fff;padding:12px 20px;display:flex;gap:18px;align-items:baseline}}
 header a{{color:#9cf;text-decoration:none}} header b{{font-size:17px}}
 main{{padding:20px;max-width:1100px;margin:0 auto}}
 .card{{background:#fff;border:1px solid #dde;border-radius:8px;padding:14px;margin:10px 0}}
 form.row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
 input,select,button{{font:inherit;padding:7px 9px;border:1px solid #bbc;border-radius:6px}}
 input[type=text]{{flex:1;min-width:320px}}
 button{{background:#1565d8;color:#fff;border-color:#1565d8;cursor:pointer}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}}
 .cell img{{max-width:100%;border:1px solid #ccd;border-radius:4px;background:#fff}}
 .val{{font-weight:700;font-size:18px}}
 .ctx{{color:#555;font-size:13px;margin-top:6px}}
 .ok{{color:#0a7d28;font-weight:600}} .bad{{color:#c0143c;font-weight:600}}
 .pill{{display:inline-block;font-size:12px;padding:1px 7px;border-radius:10px;background:#eef}}
 table.kv td{{padding:2px 8px;vertical-align:top;font-size:13px}}
 tr.nr{{background:#fff7e6}} tr.verified{{background:#f1fbf3}}
 .statusflag{{font-size:12px;padding:1px 7px;border-radius:10px}}
 .s-verified{{background:#d7f5dd;color:#0a7d28}} .s-needs_review{{background:#ffe7b3;color:#8a5a00}}
 .s-accepted{{background:#dbeafe;color:#1356b3}} .s-rejected{{background:#fde2e2;color:#c0143c}}
 .s-pending{{background:#eee;color:#555}}
 .dwrap{{overflow:auto}} table.dash{{border-collapse:collapse;width:100%;font-size:13px;background:#fff}}
 table.dash th,table.dash td{{border:1px solid #e2e6ee;padding:6px 8px;text-align:left;vertical-align:top}}
 table.dash th{{background:#f0f3f8;position:sticky;top:0}}
 .thumb{{height:34px;border:1px solid #ccd;border-radius:3px;vertical-align:middle}}
 .pop{{position:relative}} .pop .big{{display:none;position:absolute;z-index:9;left:0;top:38px;
   box-shadow:0 6px 24px rgba(0,0,0,.25);border:1px solid #aaa;background:#fff}}
 .pop:hover .big{{display:block}} .pop .big img{{max-width:340px;display:block}}
</style>
<header><b>safety-dashboard</b>
 <a href="/dashboard">Dashboard (P5)</a>
 <a href="/review">Review (P3/P4)</a>
 <a href="/">Extract (P1/P2)</a>
 <span style=color:#888>every number is bound to a bounding box</span>
</header><main>{body}</main>"""


def page(body: str) -> str:
    return _BASE.format(body=body)


def _form() -> str:
    import json
    presets_js = json.dumps(PRESETS)
    opts = "".join(f'<option value="{i}">{p["label"]}</option>' for i, p in enumerate(PRESETS))
    return f"""<div class=card><form class=row method=post action=/extract>
       <select onchange="fill(this.value)"><option value="">— preset —</option>{opts}</select>
       <select name=kind><option>html</option><option>pdf</option></select>
       <input type=text name=source placeholder="URL or path" value="{PRESETS[0]['source']}">
       <label>table #<input name=table_index value=0 size=2></label>
       <label>pdf page<input name=page value="" size=3></label>
       <button>Extract + crop</button>
      </form>
      <p class=ctx>Pick a preset to autofill, or paste your own URL/path. PDF needs a page number (0-indexed).</p></div>
      <script>const P={presets_js};function fill(i){{if(i==="")return;const p=P[i];
        document.querySelector('[name=kind]').value=p.kind;
        document.querySelector('[name=source]').value=p.source;
        document.querySelector('[name=table_index]').value=p.table_index;
        document.querySelector('[name=page]').value=(p.page??"");}}</script>"""


@app.get("/")
def index():
    return page(_form())


@app.post("/extract")
def extract():
    kind = flask.request.form["kind"]
    source = flask.request.form["source"].strip()
    table_index = int(flask.request.form.get("table_index") or 0)
    page_arg = flask.request.form.get("page") or ""
    if kind == "html":
        cands = runner.run_html(source, table_index)
        loc = f"HTML table #{table_index}"
    else:
        if not page_arg:
            return page(_form() + "<div class=card class=bad>PDF requires a page number.</div>")
        cands = extract_pdf.extract(source, int(page_arg), table_index, paths.CROPS, _rid())
        loc = f"PDF page {page_arg}, table #{table_index}"
    return page(_form() + _results(source, loc, cands))


def _rid() -> str:
    import uuid
    return uuid.uuid4().hex[:10]


def _results(source, loc, cands) -> str:
    items = []
    for c in cands:
        present = ocr.value_present_in_crop(c.value_string, c.crop_path)
        badge = '<span class=ok>OCR&nbsp;✓</span>' if present else '<span class=bad>OCR&nbsp;✗</span>'
        rel = c.crop_path.name
        fn = "<br>".join("• " + f for f in c.context.footnotes)
        items.append(f"""<div class="card cell">
          <div class=val>{markupsafe.escape(c.value_string)} &nbsp; {badge}</div>
          <img src="/crops/{rel}" loading=lazy>
          <table class=kv>
           <tr><td>row<td>{markupsafe.escape(c.context.row_label)}</tr>
           <tr><td>column<td>{markupsafe.escape(c.context.column_header)}</tr>
           <tr><td>bbox<td>{', '.join(str(round(b,1)) for b in c.source_ref.bbox)}</tr>
           {('<tr><td>footnote<td>'+markupsafe.escape(fn)+'</tr>') if fn else ''}
          </table></div>""")
    head = (f"<div class=card><b>{len(cands)}</b> numeric cells from {loc} "
            f"&nbsp;<span class=pill>{markupsafe.escape(source)}</span><br>"
            f"<span class=ctx>Each crop is a tight, highlighted crop of exactly the bounding box stored for that number.</span></div>")
    return head + f"<div class=grid>{''.join(items)}</div>"


def _ctx(row) -> dict:
    return json.loads(row["context_json"])


def _crop_cell(row) -> str:
    name = markupsafe.escape(pathlib.PurePath(row["crop_path"]).name)
    return (f'<span class=pop><img class=thumb src="/crops/{name}">'
            f'<span class=big><img src="/crops/{name}"></span></span>')


@app.get("/dashboard")
def dashboard():
    status = flask.request.args.get("status", "verified")
    conn = _conn()
    counts = db.status_counts(conn)
    rows = db.candidates(conn, status=status)
    filt = "".join(
        f'<a class="statusflag s-{s}" href="/dashboard?status={s}">{s}: {counts.get(s,0)}</a> '
        for s in ("verified", "accepted", "needs_review", "rejected", "pending"))
    filt += f'<a class="statusflag s-pending" href="/dashboard?status=all">all: {sum(counts.values())}</a>'
    body = [f"<div class=card><b>Dashboard</b> &mdash; showing <b>{markupsafe.escape(status)}</b> rows. "
            f"Filter: {filt}<br><span class=ctx>Only verified/accepted numbers are trustworthy; "
            f"each crop is the exact provenance for its number.</span></div>"]
    trs = []
    for r in rows:
        ctx = _ctx(r)
        model = pathlib.PurePath(r["origin_url"]).name
        norm = r["structural_value"]
        trs.append(
            f'<tr class={"verified" if r["status"]=="verified" else ""}>'
            f'<td>{_crop_cell(r)}</td>'
            f'<td><b>{markupsafe.escape(r["value_string"])}</b></td>'
            f'<td>{"" if norm is None else norm}</td>'
            f'<td>{markupsafe.escape(ctx["row_label"])[:60]}</td>'
            f'<td>{markupsafe.escape(ctx["column_header"])[:40]}</td>'
            f'<td>{markupsafe.escape(model)}</td>'
            f'<td><span class="statusflag s-{r["status"]}">{r["status"]}</span></td></tr>')
    body.append('<div class="card dwrap"><table class=dash><tr><th>crop<th>raw<th>value'
                '<th>row<th>column<th>source<th>status</tr>' + "".join(trs) + "</table></div>")
    return page("".join(body))


@app.get("/review")
def review():
    status = flask.request.args.get("status", "needs_review")
    conn = _conn()
    rows = db.candidates(conn, status=status)
    cards = []
    for r in rows:
        ctx = _ctx(r)
        name = markupsafe.escape(pathlib.PurePath(r["crop_path"]).name)
        cards.append(f"""<div class="card cell">
          <div class=val>{markupsafe.escape(r['value_string'])}
            <span class="statusflag s-{r['status']}">{r['status']}</span></div>
          <img src="/crops/{name}" loading=lazy>
          <table class=kv>
           <tr><td>structural<td>{r['structural_value']}</tr>
           <tr><td>vlm<td>{r['vlm_value']}</tr>
           <tr><td>row<td>{markupsafe.escape(ctx['row_label'])[:80]}</tr>
           <tr><td>column<td>{markupsafe.escape(ctx['column_header'])[:60]}</tr>
          </table>
          <form class=row method=post action="/candidate/{r['id']}/status">
           <input type=hidden name=back value="{markupsafe.escape(status)}">
           <button name=status value=accepted>Accept</button>
           <button name=status value=rejected style=background:#c0143c;border-color:#c0143c>Reject</button>
          </form></div>""")
    return page(f"<div class=card><b>Review queue</b> &mdash; status="
                f"<b>{markupsafe.escape(status)}</b> ({len(rows)} items). "
                f"<a href=/review?status=needs_review>needs_review</a> | "
                f"<a href=/review?status=verified>verified</a> | "
                f"<a href=/review?status=pending>pending</a></div>"
                f"<div class=grid>{''.join(cards)}</div>")


@app.post("/candidate/<int:cid>/status")
def set_candidate_status(cid):
    new = flask.request.form["status"]
    back = flask.request.form.get("back", "needs_review")
    conn = _conn()
    db.set_status(conn, cid, new)
    db.add_attempt(conn, cid, "structural", f"human:{new}", new)
    return flask.redirect(f"/review?status={back}")


@app.get("/crops/<path:name>")
def crops(name):
    return flask.send_from_directory(paths.CROPS, name)


@app.get("/health")
def health():
    return "ok"
