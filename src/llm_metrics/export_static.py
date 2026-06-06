"""Render the dashboard to a self-contained static folder (offline-viewable).

Produces ``<out>/index.html`` plus ``<out>/crops/`` with every crop copied in,
using relative paths so it works opened from disk or unzipped anywhere -- no
server, no network. This is the "render-from-database dashboard" (P5) frozen to
static files: only verified/accepted rows are trustworthy, but every row is
shown with a status filter so nothing is hidden.
"""

import html
import json
import pathlib
import shutil
import sys

from llm_metrics import db, paths

_TRUST = ("verified", "accepted")


def _ctx(row) -> dict:
    return json.loads(row["context_json"])


def _row_html(r, crop_name: str) -> str:
    ctx = _ctx(r)
    norm = "" if r["structural_value"] is None else r["structural_value"]
    model = pathlib.PurePath(r["origin_url"]).name
    e = html.escape
    return (
        f'<tr data-status="{r["status"]}" data-source="{e(model)}">'
        f'<td class=pop><img class=thumb src="crops/{crop_name}" loading=lazy>'
        f'<span class=big><img src="crops/{crop_name}"></span></td>'
        f'<td class=raw>{e(r["value_string"])}</td>'
        f'<td>{norm}</td>'
        f'<td>{e(ctx["row_label"])[:70]}</td>'
        f'<td>{e(ctx["column_header"])[:46]}</td>'
        f'<td>{e(model)}</td>'
        f'<td><span class="flag s-{r["status"]}">{r["status"]}</span></td>'
        f'<td>{r["vlm_value"] if r["vlm_value"] is not None else ""}</td></tr>')


def export(out_dir: pathlib.Path, conn=None) -> dict:
    conn = conn or db.connect()
    out_dir.mkdir(parents=True, exist_ok=True)
    crops_out = out_dir / "crops"
    crops_out.mkdir(exist_ok=True)
    rows = db.candidates(conn, status="all")
    trs = []
    for r in rows:
        src = pathlib.Path(r["crop_path"])
        if not src.exists():
            continue
        shutil.copy(src, crops_out / src.name)
        trs.append(_row_html(r, src.name))
    counts = db.status_counts(conn)
    srcs = db.sources(conn)
    (out_dir / "index.html").write_text(_PAGE.format(
        rows="".join(trs), n=len(trs), nsources=len(srcs),
        nverified=sum(counts.get(s, 0) for s in _TRUST),
        counts_json=json.dumps(counts), sources_rows=_sources_rows(srcs)))
    return {"rows": len(trs), "sources": len(srcs), "out": str(out_dir)}


def _sources_rows(srcs) -> str:
    out = []
    for s in srcs:
        out.append(f'<tr><td>{html.escape(pathlib.PurePath(s["origin_url"]).name)}</td>'
                   f'<td>{s["kind"]}</td><td>{s["n_candidates"]}</td><td>{s["n_verified"]}</td>'
                   f'<td class=mono>{s["sha256"][:16]}…</td><td>{html.escape(s["retrieved_at"])}</td></tr>')
    return "".join(out)


_PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>LLM safety-metrics dashboard</title><style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:0;color:#111;background:#eef1f5}}
 header{{background:#0b1320;color:#fff;padding:16px 22px}}
 header h1{{margin:0;font-size:20px}} header .sub{{color:#9fb3c8;font-size:13px}}
 main{{padding:18px;max-width:1180px;margin:0 auto}}
 .card{{background:#fff;border:1px solid #d7dee8;border-radius:10px;padding:14px 16px;margin:12px 0}}
 .kpi{{display:flex;gap:22px;flex-wrap:wrap}} .kpi b{{font-size:24px}} .kpi div{{color:#566}}
 .controls{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
 button,input{{font:inherit;padding:6px 10px;border:1px solid #b9c2d0;border-radius:7px;background:#fff;cursor:pointer}}
 button.on{{background:#1565d8;color:#fff;border-color:#1565d8}}
 table{{border-collapse:collapse;width:100%;background:#fff}}
 th,td{{border:1px solid #e3e8ef;padding:6px 8px;text-align:left;vertical-align:top}}
 th{{background:#f1f4f9;position:sticky;top:0;cursor:pointer}}
 td.raw{{font-weight:700;white-space:nowrap}} .mono{{font-family:ui-monospace,monospace;font-size:12px}}
 .thumb{{height:32px;border:1px solid #ccd;border-radius:3px}}
 .pop{{position:relative}} .big{{display:none;position:absolute;z-index:9;left:0;top:36px;
   box-shadow:0 8px 30px rgba(0,0,0,.3);border:1px solid #999;background:#fff}}
 .pop:hover .big{{display:block}} .big img{{max-width:360px;display:block}}
 .flag{{font-size:12px;padding:1px 7px;border-radius:10px}}
 .s-verified{{background:#d7f5dd;color:#0a7d28}} .s-needs_review{{background:#ffe7b3;color:#8a5a00}}
 .s-accepted{{background:#dbeafe;color:#1356b3}} .s-rejected{{background:#fde2e2;color:#c0143c}}
 .s-pending{{background:#eee;color:#555}}
</style></head><body>
<header><h1>LLM safety-metrics dashboard</h1>
<div class=sub>Every number below is bound to a bounding box in its source; the crop is the exact provenance.
Structural extraction is the source of truth; an independent VLM read cross-checks it (verified = both agree + OCR-present).</div></header>
<main>
 <div class="card kpi">
   <div><b>{n}</b><div>numbers</div></div>
   <div><b>{nverified}</b><div>verified / accepted</div></div>
   <div><b>{nsources}</b><div>system cards</div></div>
 </div>
 <div class="card controls">
   <span>filter:</span>
   <button data-f=trust class=on onclick="setf(this,'trust')">verified+accepted</button>
   <button data-f=all onclick="setf(this,'all')">all</button>
   <button data-f=needs_review onclick="setf(this,'needs_review')">needs_review</button>
   <input id=q placeholder="search row/column/source…" oninput="apply()" style="flex:1;min-width:220px">
   <span id=shown class=sub></span>
 </div>
 <div class="card" style="overflow:auto;max-height:74vh">
 <table id=t><thead><tr>
   <th>crop</th><th onclick="sortby(1)">raw</th><th onclick="sortby(2)">value</th>
   <th onclick="sortby(3)">row / metric</th><th onclick="sortby(4)">column</th>
   <th onclick="sortby(5)">source</th><th onclick="sortby(6)">status</th><th>vlm</th>
 </tr></thead><tbody>{rows}</tbody></table></div>
 <div class=card><b>Sources</b> (frozen by sha256 at ingest):
 <table><tr><th>source<th>kind<th>candidates<th>verified<th>sha256<th>retrieved</tr>{sources_rows}</table></div>
 <div class=sub style="text-align:center;margin:20px">Generated offline from the content-addressed store. Hover any crop to enlarge.</div>
</main>
<script>
 const TRUST=['verified','accepted']; let mode='trust';
 function setf(btn,m){{mode=m;document.querySelectorAll('[data-f]').forEach(b=>b.classList.toggle('on',b===btn));apply();}}
 function apply(){{
   const q=document.getElementById('q').value.toLowerCase();let shown=0;
   document.querySelectorAll('#t tbody tr').forEach(tr=>{{
     const st=tr.dataset.status;
     let ok=(mode==='all')||(mode==='trust'&&TRUST.includes(st))||(mode===st);
     if(ok&&q) ok=tr.innerText.toLowerCase().includes(q);
     tr.style.display=ok?'':'none'; if(ok)shown++;
   }});
   document.getElementById('shown').textContent=shown+' shown';
 }}
 function sortby(i){{
   const tb=document.querySelector('#t tbody');const rows=[...tb.rows];
   rows.sort((a,b)=>a.cells[i].innerText.localeCompare(b.cells[i].innerText,undefined,{{numeric:true}}));
   rows.forEach(r=>tb.appendChild(r));
 }}
 apply();
</script></body></html>"""


def main() -> None:
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else paths.ROOT / "export"
    print(export(out))


if __name__ == "__main__":
    main()
