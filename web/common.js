/* Shared config + helpers for the three dashboard pages.
 * All three pages are fully static: they read from Supabase (PostgREST) with the
 * public read-only anon key, and crops from public Storage. The review page also
 * uses Supabase Auth so a signed-in reviewer can write table-level decisions. */

const SUPABASE_URL = "https://rapkltwpfvzleejytgmq.supabase.co";
const ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJhcGtsdHdwZnZ6bGVlanl0Z21xIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA3MTY4MjksImV4cCI6MjA5NjI5MjgyOX0.DbXAds_FJmhB5RbhbMUpMjoBe7wZ6H6vOiBJtRs7wfE";

const TRUST = ["verified", "accepted"];
const STATUS_ORDER = { verified: 0, accepted: 1, needs_review: 2, pending: 3, rejected: 4 };

// Friendly titles for each source card (falls back to a prettified URL).
const SOURCE_LABELS = {
  "https://deploymentsafety.openai.com/gpt-5-5": "GPT-5.5 system card",
  "https://deploymentsafety.openai.com/gpt-5-2": "GPT-5.2 system card",
  "https://deploymentsafety.openai.com/gpt-5-1": "GPT-5.1 system card",
  "https://deploymentsafety.openai.com/gpt-5": "GPT-5 system card",
  "https://deploymentsafety.openai.com/o3": "OpenAI o3 system card",
  "https://deploymentsafety.openai.com/sora-2": "Sora 2 system card",
  "https://deploymentsafety.openai.com/gpt-oss": "gpt-oss system card",
  "https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-Pro-Model-Card.pdf": "Gemini 3 Pro model card",
  "https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-3-1-Pro-Model-Card.pdf": "Gemini 3.1 Pro model card",
  "https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-2-5-Pro-Model-Card.pdf": "Gemini 2.5 Pro model card",
  "https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-2-5-Flash-Model-Card.pdf": "Gemini 2.5 Flash model card",
  "https://storage.googleapis.com/deepmind-media/Model-Cards/Gemini-2-0-Flash-Model-Card.pdf": "Gemini 2.0 Flash model card",
};
function prettyFromUrl(url) {
  const last = (url || "").split("/").pop().replace(/\.pdf$/i, "").replace(/-/g, " ");
  return last || url;
}
function sourceLabel(url) { return SOURCE_LABELS[url] || prettyFromUrl(url); }

const esc = s => (s ?? "").toString().replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Stable key for a table/section, used to attach review decisions so the weekly
// re-ingest (which rebuilds candidate rows) does not wipe them.
function tableKey(originUrl, sectionKey) { return originUrl + "::" + (sectionKey || "?"); }

async function sbGet(path) {
  const res = await fetch(SUPABASE_URL + "/rest/v1/" + path,
    { headers: { apikey: ANON, Authorization: "Bearer " + ANON } });
  if (!res.ok) throw new Error("HTTP " + res.status + ": " + (await res.text()).slice(0, 200));
  return res.json();
}

// Page through PostgREST (default max 1000 rows/request) until exhausted.
async function sbGetAll(table, select) {
  const out = []; const step = 1000;
  for (let from = 0; ; from += step) {
    const res = await fetch(SUPABASE_URL + "/rest/v1/" + table + "?select=" + select + "&order=id",
      { headers: { apikey: ANON, Authorization: "Bearer " + ANON, Range: `${from}-${from + step - 1}` } });
    if (!res.ok) throw new Error("HTTP " + res.status + ": " + (await res.text()).slice(0, 200));
    const chunk = await res.json();
    out.push(...chunk);
    if (chunk.length < step) break;
  }
  return out;
}

async function loadCandidates() {
  return sbGetAll("candidates",
    "id,value_string,structural_value,vlm_value,status,crop_url,context,page,source_id,sources(origin_url)");
}

// Returns { table_key: {status, note, reviewer, updated_at} }. Tolerates the
// reviews table not existing yet (returns {} so read-only pages still work).
async function loadReviews() {
  try {
    const rows = await sbGet("reviews?select=table_key,status,note,reviewer,updated_at");
    const m = {};
    for (const r of rows) m[r.table_key] = r;
    return m;
  } catch (e) { console.warn("reviews unavailable:", e.message); return {}; }
}

// A table-level review decision overrides each number's automatic status.
function effectiveStatus(cand, reviews) {
  const r = reviews[tableKey(cand.sources?.origin_url, cand.context?.section_key)];
  return r ? r.status : cand.status;
}

const NAV = [
  ["index.html", "Dashboard"],
  ["sources.html", "Sources"],
  ["review.html", "Review"],
];
function mountChrome(active) {
  const css = document.createElement("style");
  css.textContent = `
   body{font:14px/1.5 system-ui,sans-serif;margin:0;color:#111;background:#eef1f5}
   header.top{background:#0b1320;color:#fff;padding:12px 22px;display:flex;gap:20px;align-items:baseline}
   header.top b{font-size:17px} header.top nav{display:flex;gap:14px}
   header.top a{color:#9fb3c8;text-decoration:none;padding:3px 4px;border-bottom:2px solid transparent}
   header.top a.on{color:#fff;border-bottom-color:#4f93ff}
   header.top .sp{margin-left:auto;color:#7e93a8;font-size:12px}
   main{padding:18px;max-width:1380px;margin:0 auto}
   .card{background:#fff;border:1px solid #d7dee8;border-radius:10px;padding:14px 16px;margin:12px 0}
   .muted{color:#566;font-size:13px}
   button,input,select{font:inherit;padding:6px 10px;border:1px solid #b9c2d0;border-radius:7px;background:#fff;cursor:pointer}
   button.on{background:#1565d8;color:#fff;border-color:#1565d8}
   .flag{font-size:12px;padding:1px 7px;border-radius:10px;white-space:nowrap}
   .s-verified{background:#d7f5dd;color:#0a7d28}.s-needs_review{background:#ffe7b3;color:#8a5a00}
   .s-accepted{background:#dbeafe;color:#1356b3}.s-rejected{background:#fde2e2;color:#c0143c}.s-pending{background:#eee;color:#555}
   .thumb{height:30px;border:1px solid #ccd;border-radius:3px;vertical-align:middle;background:#fff}
   .pop{position:relative}.pop .big{display:none;position:absolute;z-index:30;left:0;top:34px;
     box-shadow:0 8px 30px rgba(0,0,0,.35);border:1px solid #999;background:#fff}
   .pop:hover .big{display:block}.pop .big img{max-width:420px;display:block}
   .modal-bg{position:fixed;inset:0;background:rgba(8,14,26,.55);display:none;z-index:50;padding:28px;overflow:auto}
   .modal-bg.show{display:block}
   .modal{background:#fff;max-width:760px;margin:0 auto;border-radius:12px;padding:18px}
  `;
  document.head.appendChild(css);
  const h = document.createElement("header");
  h.className = "top";
  h.innerHTML = `<b>LLM safety-metrics</b><nav>` +
    NAV.map(([href, label]) => `<a href="${href}" class="${href === active ? "on" : ""}">${label}</a>`).join("") +
    `</nav><span class="sp">every number is bound to a bounding box in its source</span>`;
  document.body.prepend(h);
}
