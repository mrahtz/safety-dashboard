/* Shared config + helpers for the three dashboard pages.
 * All three pages are fully static: they read from Supabase (PostgREST) with the
 * public read-only anon key, and page images from public Storage. The review page also
 * uses Supabase Auth so a signed-in reviewer can write table-level decisions. */

const SUPABASE_URL = "https://rapkltwpfvzleejytgmq.supabase.co";
const ANON = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJhcGtsdHdwZnZ6bGVlanl0Z21xIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA3MTY4MjksImV4cCI6MjA5NjI5MjgyOX0.DbXAds_FJmhB5RbhbMUpMjoBe7wZ6H6vOiBJtRs7wfE";

const TRUST = ["accepted"];
const STATUS_ORDER = { accepted: 0, needs_review: 1 };

// Friendly titles for each source card (falls back to a prettified URL).
const SOURCE_LABELS = {
  "https://www.anthropic.com/claude-fable-5-mythos-5-system-card": "Claude Fable 5 & Mythos 5 system card",
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
  "https://cdn.openai.com/gpt-5-system-card.pdf": "GPT-5 system card",
  "https://www.anthropic.com/document/claude-mythos-preview-system-card": "Claude Mythos Preview system card",
  "https://assets.anthropic.com/m/64823ba7485345a7/Claude-Opus-4-5-System-Card.pdf": "Claude Opus 4.5 system card",
  "https://data.x.ai/2025-08-20-grok-4-model-card.pdf": "Grok 4 model card",
  "https://arxiv.org/abs/2506.13585": "MiniMax-M1 technical report",
  "https://arxiv.org/abs/2507.20534": "Kimi K2 technical report",
  "https://arxiv.org/abs/2506.10910": "Magistral technical report",
  "https://arxiv.org/abs/2507.06261": "Gemini 2.5 technical report",
  "https://arxiv.org/abs/2412.19437": "DeepSeek-V3 technical report",
  "https://arxiv.org/abs/2508.06471": "GLM-4.5 technical report",
  "https://arxiv.org/abs/2505.09388": "Qwen3 technical report",
  "https://arxiv.org/abs/2408.08926": "Cybench: cybersecurity LLM benchmark paper",
  "https://arxiv.org/abs/2510.24317": "CAIBench: cybersecurity AI benchmark paper",
  "https://arxiv.org/abs/2602.08023": "CTFExplorer: multi-target web CTF benchmark paper",
  "https://arxiv.org/abs/2410.17141": "AutoPentest: LLM penetration testing benchmark paper",
  "https://arxiv.org/abs/2602.11685": "DRACO: deep research benchmark paper",
};
function prettyFromUrl(url) {
  const last = (url || "").split("/").pop().replace(/\.pdf$/i, "").replace(/-/g, " ");
  return last || url;
}
function sourceLabel(url) { return SOURCE_LABELS[url] || prettyFromUrl(url); }

const esc = s => (s ?? "").toString().replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Group metrics by their source's origin_url. Returns Map(url -> cells[]).
// Shared by sources.html and review.html so the two stay in sync.
function groupBySource(metrics, fallback = "?") {
  const m = new Map();
  for (const c of metrics) {
    const url = c.sources?.origin_url || fallback;
    if (!m.has(url)) m.set(url, []);
    m.get(url).push(c);
  }
  return m;
}

// Group a source's cells by section_key (its original table/figure). Returns
// Map(section_key -> cells[]).
function groupBySection(cells, fallback = "?") {
  const m = new Map();
  for (const c of cells) {
    const k = c.section_key ?? fallback;
    if (!m.has(k)) m.set(k, []);
    m.get(k).push(c);
  }
  return m;
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

async function loadMetrics() {
  return sbGetAll("metrics",
    "id,source_id,model,condition,subset,benchmark,category,value,units,row_idx,col_idx,accepted," +
    "section_key,page_num,sources(origin_url,num_pages_total)");
}

function effectiveStatus(cand) {
  return cand.accepted ? "accepted" : "needs_review";
}

// Render a value with its units: "75.0%", "$5478.16", "2439 Elo", or bare.
function fmtVal(value, units) {
  const v = (value ?? "").toString();
  if (units === "%") return v + "%";
  if (units === "$") return "$" + v;
  return units ? v + " " + units : v;
}

const NAV = [
  ["index.html", "Dashboard"],
  ["sources.html", "Sources"],
  ["review.html", "Review"],
  ["db-state.html", "DB state"],
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
   .s-needs_review{background:#ffe7b3;color:#8a5a00}.s-accepted{background:#dbeafe;color:#1356b3}
   .modal-bg{position:fixed;inset:0;background:rgba(8,14,26,.55);display:none;z-index:50;padding:28px;overflow:auto}
   .modal-bg.show{display:block}
   .modal{background:#fff;max-width:760px;margin:0 auto;border-radius:12px;padding:18px}
  `;
  document.head.appendChild(css);
  const h = document.createElement("header");
  h.className = "top";
  h.innerHTML = `<b>LLM safety-metrics</b><nav>` +
    NAV.map(([href, label]) => `<a href="${href}" class="${href === active ? "on" : ""}">${label}</a>`).join("") +
    `</nav><span class="sp">every number links back to the source page it came from</span>`;
  document.body.prepend(h);
}
