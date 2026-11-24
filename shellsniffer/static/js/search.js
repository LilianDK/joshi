/* shellSniffer web UI – Sniffing the logs: the two search fields (BM25 / with shroom), SniffOptions
 * (time range, hybrid balance), search results incl. documentation hits, and the topic list
 * on the left (renderTopics) which the results replace while a search is active.
 */
// ---------- Left side: topics
// "[3]" in a SniffMagic summary refers to command 3 of the session (the numbered "All commands" list)
function linkRefs(html, events){
  return html.replace(/\[(\d{1,3})\]/g, (m, n) => { const e = (events || [])[n - 1];
    return e ? `<span class="cref" title="${esc(e.command)}">${n}</span>` : m });
}
// ---------- BM25 search over the logs (server side, SQLite FTS5)
let searchHits = null, searchSeq = 0, searchTimer = null;
// Waiting indicator for everything the shroom does: a spinning ring with a mushroom, "shrooming.."
// (the dog – if it is allowed to roam – sits next to it and sniffs at the shroom like crazy)
function shroomSpinner(hint = ""){
  const dogOn = !(typeof status !== "undefined" && status.settings && status.settings.dog === false);
  const svg = dogOn ? (document.querySelector("#roamer svg")?.outerHTML || "") : "";
  return `<div class="shroomspin" role="status" aria-live="polite"><div class="spinrow">${svg ? `<div class="sniffdog" aria-hidden="true">${svg}</div>` : ""}<div class="spin"><span>🍄</span></div></div><div class="slabel">shrooming..</div>${hint ? `<div class="sohint">${hint}</div>` : ""}</div>`;
}

// search options (time range, shroom) – kept per browser
var shroomOn = false;   // true while the "Sniff logs with shroom" field is the active search; declared early
const toLocalInput = d => new Date(d - d.getTimezoneOffset()*60000).toISOString().slice(0,16);
function searchRange(){
  const v = id => $(id).value ? new Date($(id).value).getTime()/1000 : null;
  return {start: v("#so-start"), end: v("#so-end") !== null ? v("#so-end") + 59.999 : null};   // "to" includes that whole minute
}
function searchActive(){ const r = searchRange(); return !!(queryText() || r.start !== null || r.end !== null) }
function paintSearchOpts(){
  const r = searchRange(), any = r.start !== null || r.end !== null, preset = $("#sopts").dataset.preset || "";
  $("#sopts-btn").classList.toggle("active", any);
  $("#sopts-btn").textContent = any ? "🦴 SniffOptions •" : "🦴 SniffOptions";
  $("#sopts-btn").title = "SniffOptions – search settings" + (any ? " · time range set" : "");
  try { localStorage.setItem("shellsniffer.searchopts", JSON.stringify({start:$("#so-start").value, end:$("#so-end").value, preset})) } catch(e) {}
  document.querySelectorAll("#sopts [data-preset]").forEach(b => b.classList.toggle("on", b.dataset.preset ? preset === b.dataset.preset : !any));
}
function applyPreset(p){
  const now = new Date(), day = 86400000;
  const from = {"1h": new Date(now - 3600000), "today": new Date(now.getFullYear(), now.getMonth(), now.getDate()),
                "7d": new Date(now - 7*day), "30d": new Date(now - 30*day)}[p];
  $("#so-start").value = from ? toLocalInput(from) : ""; $("#so-end").value = "";
  $("#sopts").dataset.preset = p;
}
$("#sopts-btn").onclick = () => { const o = $("#sopts").hidden; $("#sopts").hidden = !o; $("#sopts-btn").setAttribute("aria-expanded", o) };
$("#sopts").addEventListener("click", ev => { const b = ev.target.closest("[data-preset]"); if (!b) return; applyPreset(b.dataset.preset); paintSearchOpts(); runSearch() });
["#so-start","#so-end"].forEach(id => $(id).addEventListener("change", () => { $("#sopts").dataset.preset = "custom"; paintSearchOpts(); runSearch() }));
try {
  const o = JSON.parse(localStorage.getItem("shellsniffer.searchopts") || "{}");
  if (o.preset && o.preset !== "custom") applyPreset(o.preset);        // relative presets move with the clock
  else { $("#so-start").value = o.start || ""; $("#so-end").value = o.end || ""; $("#sopts").dataset.preset = o.preset || "" }
} catch(e) {}
paintSearchOpts();

// Two search fields: "Sniff logs" (BM25) and "Sniff logs with shroom" (hybrid, Ollama).
// Typing in one clears the other; shroomOn = the shroom field is the active one.
shroomOn = false;
const ollamaOn = () => !(status.settings && status.settings.ollama === false);
function queryText(){ return (shroomOn ? $("#search-shroom") : $("#search")).value.trim() }
function paintShroomField(){
  const f = $("#search-shroom"), on = ollamaOn();
  f.disabled = !on;
  f.placeholder = on ? "🍄 Sniff logs with shroom – ask or describe, Enter = answer from your logs …"
                     : "🍄 Sniff logs with shroom – Ollama is disabled (enable it at the Ollama chip)";
  if (!on && shroomOn){ f.value = ""; shroomOn = false; runSearch() }
  if (!on && window.closeShroomAnswer) window.closeShroomAnswer();
}
let searchInfo = {};   // extra info of the last hybrid search (model, indexing)

// Hybrid balance: 0 = BM25 only … 100 = cosine only (only used with the shroom on)
let balance = 50;
try { const b = parseInt(localStorage.getItem("shellsniffer.balance"), 10); if (b >= 0 && b <= 100) balance = b } catch(e) {}
function paintBalance(){
  $("#balance").value = balance;
  $("#balance-val").textContent = balance === 0 ? "BM25 only" : balance === 100 ? "cosine only" : `${100 - balance} % BM25 · ${balance} % cosine`;
  const on = typeof status === "undefined" || ollamaOn();
  $("#balance").disabled = !on;
  $("#balance-row").style.opacity = on ? "" : ".5";
  $("#balance-row").title = on ? "Used by \"Sniff logs with shroom\"" : "Ollama is disabled";
}
$("#balance").addEventListener("input", ev => {
  balance = parseInt(ev.target.value, 10);
  try { localStorage.setItem("shellsniffer.balance", balance) } catch(e) {}
  paintBalance(); clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 350);
});
paintBalance();

async function runSearch(){
  const q = queryText(), rg = searchRange();
  if (!searchActive()){ searchHits = null; renderTopics(); return }
  if (rg.start !== null && rg.end !== null && rg.start > rg.end){ searchError = "the time range ends before it starts"; searchHits = []; renderTopics(); return }
  const seq = ++searchSeq;
  const params = new URLSearchParams({q, limit: 100});
  if (shroomOn && q){ params.set("mode", "hybrid"); params.set("semantic_weight", balance / 100) }
  if (shroomOn && q){ searchError = null; searchHits = searchHits || []; $("#topics").innerHTML = shroomSpinner("the first search vectorizes your commands and takes a moment") }
  if (rg.start !== null) params.set("start", rg.start);
  if (rg.end !== null) params.set("end", rg.end);
  try {
    const res = await fetch(`/api/search?${params}`);
    const r = await res.json();
    if (seq !== searchSeq) return;
    searchRanked = r.ranked !== false;
    searchInfo = r.mode === "hybrid" ? r : {};
    searchError = res.ok ? null : (r.detail || `HTTP ${res.status}`);
    searchHits = r.hits || [];
    searchDocs = r.docs || [];
  } catch(e) {
    if (seq !== searchSeq) return;
    searchError = "The shellSniffer server is not reachable – start it with start.ps1 / start.sh.";
    searchHits = [];
  }
  renderTopics();
}
let searchError = null, searchRanked = true, searchDocs = [];
const markHl = s => esc(s||"").replace(/\x01/g,"<mark>").replace(/\x02/g,"</mark>");
function renderSearch(){
  const box = $("#topics");
  if (searchError){ box.innerHTML = `<div class="empty" style="color:var(--err)">Search failed: ${esc(searchError)}</div>`; return }
  const rg = searchRange(), fmtR = t => new Date(t*1000).toLocaleString(undefined,{dateStyle:"short",timeStyle:"short"});
  const rangeTxt = rg.start!==null || rg.end!==null ? ` · ${rg.start!==null ? fmtR(rg.start) : "…"} – ${rg.end!==null ? fmtR(rg.end) : "now"}` : "";
  if (!searchHits.length && !searchDocs.length){ box.innerHTML = `<div class="empty">No matches in the logs${esc(rangeTxt)}.</div>`; return }
  const hy = searchInfo.mode === "hybrid";
  const how = !searchRanked ? `command${searchHits.length===1?"":"s"} · newest first`
            : hy ? `hit${searchHits.length===1?"":"s"} · 🍄 ${Math.round((1 - searchInfo.semantic_weight) * 100)} % BM25 + ${Math.round(searchInfo.semantic_weight * 100)} % cosine (${esc(searchInfo.model)})` : `hit${searchHits.length===1?"":"s"} · ranked by BM25`;
  const tips = !hy ? "" :
    (searchInfo.not_indexed ? `<span class="tip">${searchInfo.not_indexed} older commands are not sniffed by the shroom yet – search again to continue.</span>` : "") +
    (!searchInfo.embedding_model ? `<span class="tip">Using the chat model for meaning – faster and better with an embedding model: <code>ollama pull nomic-embed-text</code></span>` : "");
  const why = h => hy ? `${h.bm25_rank ? `<span class="why" title="rank in the BM25 word search">BM25 #${h.bm25_rank}</span>` : ""}${h.similarity !== null && h.similarity !== undefined ? `<span class="why" title="semantic similarity (cosine) from the shroom">🍄 ${h.similarity.toFixed(2)}</span>` : ""}` : "";
  // documentation written by SniffMagic that matches the query
  const docsHtml = searchDocs.length ? `<div class="shead">📄 ${searchDocs.length} documented session${searchDocs.length===1?"":"s"} match</div>` +
    searchDocs.map(d => `
    <div class="hit doc linked" data-topic="${d.topic_id||""}" title="Open topic">
      <div class="meta"><span>${fmtDate(d.start_ts)}</span><span class="pchip" style="border-color:${topicColor(d.topic_id)}">${esc(d.topic_name || "")}</span>
        <span>${d.n} command${d.n===1?"":"s"}</span>${d.project ? `<span class="pchip">📁 ${esc(d.project)}</span>` : ""}
        ${why(d)}<span class="score" title="${hy ? "fused score" : "BM25 score"}">${(d.score ?? 0).toFixed(2)}</span></div>
      <div class="dtitle">${markHl(d.title_hl)}</div>
      ${d.summary_snip ? `<div class="hout">${markHl(d.summary_snip.replace(/\*\*/g, ""))}</div>` : ""}
    </div>`).join("") : "";
  box.innerHTML = docsHtml + (!searchHits.length ? "" : `<div class="shead">${searchHits.length} ${how}${esc(rangeTxt)}${tips}</div>`) +
    searchHits.map(h => `
    <div class="hit ${h.topic_id ? "linked" : ""}" data-topic="${h.topic_id||""}" title="${h.topic_id ? "Open topic" : "Not documented yet"}">
      <div class="meta"><span>${fmtDate(h.ts)}</span><span class="tchip">${esc(h.shell)}</span><span>${esc(h.cwd)}</span>
        ${h.exit_code ? `<span style="color:var(--err)">exit ${h.exit_code}</span>` : ""}
        ${h.topic_id ? `<span class="pchip" style="border-color:${topicColor(h.topic_id)}">${esc(h.topic_name)}</span>` : `<span class="pchip">not documented yet</span>`}
        ${hy ? `${h.bm25_rank ? `<span class="why" title="rank in the BM25 word search">BM25 #${h.bm25_rank}</span>` : ""}${h.similarity !== null && h.similarity !== undefined ? `<span class="why" title="semantic similarity (cosine) from the shroom">🍄 ${h.similarity.toFixed(2)}</span>` : ""}` : ""}
        ${h.score !== null && h.score !== undefined ? `<span class="score" title="${hy ? "fused score (Reciprocal Rank Fusion)" : "BM25 score"}">${h.score.toFixed(2)}</span>` : ""}</div>
      <div class="hcmd">${markHl(h.command_hl)}</div>
      ${h.output_snip && (!searchRanked || h.output_snip.includes("\x01")) ? `<div class="hout">${markHl(h.output_snip)}</div>` : ""}
    </div>`).join("");
}
$("#topics").addEventListener("click", ev => {
  const h = ev.target.closest(".hit.linked"); if (!h) return;
  $("#search").value = ""; $("#search-shroom").value = ""; shroomOn = false; applyPreset(""); paintSearchOpts(); searchHits = null;
  renderTopics(+h.dataset.topic);
  const el = $("#topics").querySelector(`.topic[data-id="${h.dataset.topic}"]`);
  if (el){ el.open = true; el.scrollIntoView({block:"start", behavior:"smooth"}) }
});

function renderTopics(flashId){
  if (searchActive() && searchHits) return renderSearch();
  const q = "";
  const open = new Set([...document.querySelectorAll(".topic[open]")].map(d=>+d.dataset.id));
  const box = $("#topics");
  const list = topics;
  if (!list.length){ box.innerHTML = `<div class="empty">No documentation yet. Work in an observed shell, then click <b>SniffMagic</b> – Ollama turns your sessions into topics.</div>`; return }
  const topicHtml = t => `
    <details class="topic" data-id="${t.id}" ${open.has(t.id)||q||t.id===flashId?"open":""}>
      <summary><span class="dot" style="background:${topicColor(t.id)}"></span>
        <span class="name">${esc(t.name)}</span><span class="desc">${esc(t.description)}</span>
        <span class="count">${t.blocks.length} session${t.blocks.length===1?"":"s"}</span></summary>
      ${t.blocks.map(b => `
        <div class="session">
          <h3>${esc(b.title)}</h3>
          <div class="meta"><span>${fmtDate(b.start_ts)}</span><span>${b.n} commands</span>
            ${b.project ? `<span class="pchip" title="Project">📁 ${esc(b.project)}</span>` : ""}
            ${(b.tools||[]).slice(0,4).map(x => `<span class="tchip" title="Tool">${esc(x)}</span>`).join("")}
            <span title="How this session was clustered">${esc(b.method)}</span>
            <button style="font-size:11px;padding:1px 6px" onclick="recluster(${b.id})">re-cluster</button></div>
          <div class="md">${linkRefs(md(b.summary), b.events)}</div>
          <details class="cmds"><summary>All commands</summary><ol>
            ${b.events.map(e=>`<li class="${e.exit_code?"fail":""}" title="${esc(e.cwd)}">${esc(e.command)}</li>`).join("")}
          </ol></details>
        </div>`).join("")}
    </details>`;
  box.innerHTML = domains.map(d => {
    const ts = list.filter(t => (t.domain||"sonstiges")===d.key);
    if (!ts.length) return "";
    const n = ts.reduce((a,t) => a + t.blocks.length, 0);
    return `<div class="domain"><h2><span class="dot" style="background:${domainColor(d.key)}"></span>${esc(d.name)}
      <span class="dn">${ts.length} topic${ts.length===1?"":"s"} · ${n} session${n===1?"":"s"}</span></h2>${ts.map(topicHtml).join("")}</div>`;
  }).join("");
  if (flashId){
    const el = box.querySelector(`.topic[data-id="${flashId}"]`);
    if (el){ el.classList.add("flash"); setTimeout(()=>el.classList.remove("flash"),1500) }
  }
}
async function loadTopics(flashId){
  topics = await (await fetch("/api/topics")).json();
  renderTopics(flashId);
  refreshEventTags();
  fillFilters();
}
async function recluster(id){ await fetch(`/api/blocks/${id}/recluster`,{method:"POST"}) }
