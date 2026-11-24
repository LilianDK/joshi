/* shellSniffer web UI – plain JavaScript, no build step, no dependencies.
 *
 * Sections (in this order):
 *   helpers, colours ........ formatting, escaping, domain/topic colours
 *   Markdown ................ tiny renderer for the model's summaries
 *   search .................. BM25 search over the logs + SniffOptions (time range)
 *   topics (left side) ...... documentation grouped by domain > topic > session
 *   header controls ......... SniffMagic, settings (Atuin / honeypot), theme, info dialog
 *   roaming dog ............. the mascot that sniffs its way across the page
 *   live stream (right) ..... every command as it arrives, filters per shell session
 *   init + WebSocket ........ initial load, then live updates from /ws
 *   journal view ............ days <-> topics graph
 *
 * Server API: see docs/architecture.md. Live messages: event, event_update, block, status,
 * topics_changed, reload.
 */
const $ = s => document.querySelector(s);
const esc = s => (s ?? "").toString().replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmtTime = ts => new Date(ts*1000).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit",second:"2-digit"});
const fmtDate = ts => new Date(ts*1000).toLocaleString(undefined,{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});
const fmtDur = ms => ms==null ? "" : ms<1000 ? ms+" ms" : (ms/1000).toFixed(ms<10000?1:0)+" s";
// Topic colours: fixed order by topic id, grey from topic 9 on (the name is always shown)
// Colours follow the fixed domain (ontology level 1); "Other" and unassigned are grey
let domains = [];              // [{key,name,description,slot}]
const domainOf = key => domains.find(d => d.key===key) || {key:"sonstiges", name:"Sonstiges", slot:9};
const domainColor = key => { const d = domainOf(key); return d.slot <= 8 && d.key!=="sonstiges" ? `var(--s${d.slot})` : "var(--s-other)" };
const topicColor = id => { const t = topics.find(t => t.id===id); return t ? domainColor(t.domain) : "var(--s-other)" };

let topics = [];               // from the server
const blocks = new Map();      // block_id -> info
let status = {};

// ---------- Mini Markdown (code blocks, inline code, bold, lists, headings)
function md(src){
  const parts = (src||"").split(/```[^\n]*\n?/);
  return parts.map((p,i) => {
    if (i%2) return `<pre><code>${esc(p.replace(/\n$/,""))}</code></pre>`;
    const inline = t => esc(t).replace(/`([^`]+)`/g,"<code>$1</code>").replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>");
    let html="", list=false;
    for (const line of p.split("\n")){
      const li = line.match(/^\s*[-*]\s+(.*)/), h = line.match(/^#{1,4}\s+(.*)/);
      if (li){ if(!list){html+="<ul>";list=true} html+=`<li>${inline(li[1])}</li>`; continue }
      if (list){html+="</ul>";list=false}
      if (h) html+=`<h4>${inline(h[1])}</h4>`; else if (line.trim()) html+=`<p>${inline(line)}</p>`;
    }
    return html + (list?"</ul>":"");
  }).join("");
}

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
// ---------- Light / dark mode (remembered per browser; default follows the system)
function currentTheme(){ return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") }
function paintThemeBtn(){ const d = currentTheme()==="dark"; $("#theme").textContent = d ? "☀" : "☾"; $("#theme").title = d ? "Switch to light mode" : "Switch to dark mode" }
function setTheme(t){   // "light" | "dark" | "system"
  if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  try { t === "system" ? localStorage.removeItem("shellsniffer.theme") : localStorage.setItem("shellsniffer.theme", t) } catch(e) {}
  paintThemeBtn(); if (typeof view !== "undefined" && view==="journal") renderJournal();
}
$("#theme").onclick = () => setTheme(currentTheme()==="dark" ? "light" : "dark");
paintThemeBtn();

// ---------- ⚙ Settings: general settings, stored on the server (theme: per browser)
(() => {
  const dlg = $("#settingsdlg");
  const paint = () => {
    const s = status.settings || {};
    $("#set-dog").checked = s.dog !== false;
    $("#set-ollama").checked = s.ollama !== false;
    $("#vectorize").disabled = s.ollama === false;
    document.querySelectorAll("#set-atuin button").forEach(b => b.classList.toggle("on", (b.dataset.atuin === "1") === (s.atuin !== false)));
    $("#set-honeypot").checked = !!s.honeypot;
    $("#set-honeypot").disabled = s.atuin !== false;
    $("#set-hp-row").style.opacity = s.atuin !== false ? ".5" : "";
    $("#set-hp-dir").textContent = status.honeypot_dir || "honeypot/";
    const t = document.documentElement.dataset.theme || "system";
    document.querySelectorAll("#set-theme button").forEach(b => b.classList.toggle("on", b.dataset.theme === t));
    if (typeof paintVectors === "function") paintVectors();
  };
  $("#settings-btn").onclick = () => { paint(); dlg.showModal() };
  $("#settings-close").onclick = () => dlg.close();
  dlg.addEventListener("click", ev => { if (ev.target === dlg) dlg.close() });
  $("#set-dog").addEventListener("change", ev => saveSettings({dog: ev.target.checked}));
  $("#set-ollama").addEventListener("change", ev => saveSettings({ollama: ev.target.checked}));
  $("#set-atuin").addEventListener("click", ev => { const b = ev.target.closest("button"); if (b) saveSettings({atuin: b.dataset.atuin === "1"}) });
  $("#set-honeypot").addEventListener("change", ev => saveSettings({honeypot: ev.target.checked}));
  $("#set-theme").addEventListener("click", ev => { const b = ev.target.closest("button"); if (b){ setTheme(b.dataset.theme); paint() } });
  window.paintSettings = paint;
})();

// ---------- Import of collected shell dumps (upload, one request per file)
(() => {
  const dlg = $("#importdlg"), zone = $("#dropzone"), out = $("#import-results");
  $("#import-btn").onclick = () => dlg.showModal();
  $("#import-close").onclick = () => dlg.close();
  dlg.addEventListener("click", ev => { if (ev.target === dlg) dlg.close() });
  $("#pick-files").onclick = () => $("#files-in").click();
  $("#pick-dir").onclick = () => $("#dir-in").click();
  $("#files-in").onchange = ev => upload([...ev.target.files]);
  $("#dir-in").onchange = ev => upload([...ev.target.files]);
  zone.addEventListener("dragover", ev => { ev.preventDefault(); zone.classList.add("over") });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", async ev => {
    ev.preventDefault(); zone.classList.remove("over");
    upload(await filesFromDrop(ev.dataTransfer));
  });
  // dropped folders are walked recursively
  async function filesFromDrop(dt){
    const entries = [...dt.items].map(i => i.webkitGetAsEntry && i.webkitGetAsEntry()).filter(Boolean);
    if (!entries.length) return [...dt.files];
    const files = [];
    async function walk(entry, path){
      if (entry.isFile) { const f = await new Promise((res, rej) => entry.file(res, rej)); f.relPath = path + f.name; files.push(f) }
      else if (entry.isDirectory) {
        const reader = entry.createReader();
        let batch;
        do { batch = await new Promise((res, rej) => reader.readEntries(res, rej)); for (const e of batch) await walk(e, path + entry.name + "/") } while (batch.length);
      }
    }
    for (const e of entries) await walk(e, "");
    return files;
  }
  async function upload(files){
    files = files.filter(f => f.size > 0 && !/\.(png|jpe?g|gif|zip|gz|exe|dll|pdf|db)$/i.test(f.name));
    if (!files.length){ out.innerHTML = `<p class="err">No text files to import.</p>`; return }
    const rows = [];
    let total = 0, dupes = 0;
    const paint = (busy) => {
      out.innerHTML = `<table><tr><th>File</th><th>Format</th><th class="num">Found</th><th class="num">Imported</th><th class="num">Already there</th></tr>` +
        rows.map(r => r.error ? `<tr><td>${esc(r.file)}</td><td colspan="4" class="err">${esc(r.error)}</td></tr>`
          : `<tr><td title="${r.with_output} with output, ${r.with_timestamp} with timestamp">${esc(r.file)}</td><td>${esc(r.format)}</td><td class="num">${r.found}</td><td class="num">${r.imported}</td><td class="num">${r.duplicates}</td></tr>`).join("") +
        `</table><div class="sum">${busy ? `Importing ${rows.length + 1} / ${files.length} …` : `Done: ${total} command${total===1?"":"s"} imported${dupes ? `, ${dupes} already there` : ""}. Click <b>✨ SniffMagic</b> to document them.`}</div>`;
    };
    for (const f of files){
      paint(true);
      const name = f.relPath || f.webkitRelativePath || f.name;
      const qs = new URLSearchParams({name, mtime: f.lastModified || Date.now()});
      try {
        const res = await fetch(`/api/import?${qs}`, {method: "POST", headers: {"content-type": "text/plain"}, body: f});
        const r = await res.json();
        if (!res.ok) rows.push({file: name, error: r.detail || `HTTP ${res.status}`});
        else { rows.push(r); total += r.imported; dupes += r.duplicates }
      } catch(e) { rows.push({file: name, error: "server not reachable"}) }
    }
    paint(false);
    $("#files-in").value = ""; $("#dir-in").value = "";
  }
})();

// ---------- Ask the shroom: Enter in "Sniff logs with shroom" streams an LLM answer grounded in your logs
(() => {
  const box = $("#shroom-answer"), text = $("#answer-text"), srcBox = $("#answer-sources"), meta = $("#answer-meta");
  let ctrl = null, sources = [];
  const srcLabel = s => s.kind === "doc" ? `📄 ${s.title}` : `$ ${s.command.split("\n")[0].slice(0, 60)}`;
  // "[3]" in the answer -> clickable chip for source 3
  const cite = html => html.replace(/\[(\d{1,2})\]/g, (m, n) => {
    const s = sources.find(x => x.n === +n);
    return s ? `<span class="cref src" data-n="${n}" title="${esc(srcLabel(s))}">${n}</span>` : m;
  });
  function close(){ if (ctrl) ctrl.abort(); ctrl = null; box.hidden = true }
  async function ask(q){
    if (ctrl) ctrl.abort();
    ctrl = new AbortController();
    const my = ctrl, rg = searchRange();
    const qs = new URLSearchParams({q, semantic_weight: balance / 100});
    if (rg.start !== null) qs.set("start", rg.start);
    if (rg.end !== null) qs.set("end", rg.end);
    box.hidden = false; box.classList.add("busy"); sources = []; srcBox.innerHTML = "";
    text.innerHTML = shroomSpinner("searching your logs"); meta.textContent = "";
    let answer = "", t0 = Date.now();
    try {
      const res = await fetch(`/api/ask?${qs}`, {signal: my.signal});
      if (!res.ok){ const r = await res.json().catch(() => ({})); throw new Error(r.detail || `HTTP ${res.status}`) }
      const reader = res.body.getReader(), dec = new TextDecoder();
      let buf = "";
      for (;;){
        const {value, done} = await reader.read();
        if (done) break;
        buf += dec.decode(value, {stream: true});
        let i;
        while ((i = buf.indexOf("\n")) >= 0){
          const lineTxt = buf.slice(0, i); buf = buf.slice(i + 1);
          if (!lineTxt.trim()) continue;
          const m = JSON.parse(lineTxt);
          if (m.type === "sources"){
            sources = m.sources;
            srcBox.innerHTML = sources.length ? `<div class="sohint">Sources</div>` + sources.map(s =>
              `<div class="srcrow" data-n="${s.n}"><span class="cref">${s.n}</span> ${esc(srcLabel(s))} <span class="sohint">${fmtDate(s.ts)}</span></div>`).join("") : "";
            text.innerHTML = shroomSpinner(`${sources.length} sources found – the model is thinking`);
          } else if (m.type === "model"){ meta.textContent = m.model }
          else if (m.type === "token"){ answer += m.text; text.innerHTML = cite(md(answer)) }
          else if (m.type === "error"){ throw new Error(m.detail) }
          else if (m.type === "done"){
            meta.textContent += ` · ${((Date.now() - t0) / 1000).toFixed(1)} s`;
            // cited sources first, the rest folded away
            const cited = new Set([...answer.matchAll(/\[(\d{1,2})\]/g)].map(x => +x[1]));
            const row = s => `<div class="srcrow" data-n="${s.n}"><span class="cref">${s.n}</span> ${esc(srcLabel(s))} <span class="sohint">${fmtDate(s.ts)}</span></div>`;
            const used = sources.filter(s => cited.has(s.n)), rest = sources.filter(s => !cited.has(s.n));
            srcBox.innerHTML = (used.length ? `<div class="sohint">Cited sources</div>` + used.map(row).join("") : "") +
              (rest.length ? `<details><summary class="sohint">${used.length ? "Other" : "All"} ${rest.length} sources the shroom looked at</summary>${rest.map(row).join("")}</details>` : "");
          }
        }
      }
    } catch(e) {
      if (e.name === "AbortError") return;
      text.innerHTML = `<p style="color:var(--err)">The shroom could not answer: ${esc(e.message)}</p>` + (answer ? cite(md(answer)) : "");
    } finally { if (ctrl === my){ box.classList.remove("busy"); ctrl = null } }
  }
  // clicking a source (in the answer or the list) opens its topic, or shows the command in the hits
  box.addEventListener("click", ev => {
    const el = ev.target.closest("[data-n]"); if (!el) return;
    const s = sources.find(x => x.n === +el.dataset.n); if (!s) return;
    if (s.topic_id){
      // leave the search (the answer stays visible) and show the topic
      $("#search").value = ""; $("#search-shroom").value = ""; shroomOn = false;
      applyPreset(""); paintSearchOpts(); searchHits = null;
      renderTopics(s.topic_id);
      const t = $("#topics").querySelector(`.topic[data-id="${s.topic_id}"]`);
      if (t){ t.open = true; t.scrollIntoView({block:"start", behavior:"smooth"}) }
    }
  });
  $("#answer-close").onclick = close;
  $("#search-shroom").addEventListener("keydown", ev => {
    if (ev.key === "Enter" && $("#search-shroom").value.trim() && ollamaOn()){ ev.preventDefault(); ask($("#search-shroom").value.trim()) }
  });
  $("#search-shroom").addEventListener("input", () => { if (!$("#search-shroom").value.trim()) close() });
  $("#search").addEventListener("input", close);
  window.closeShroomAnswer = close;
})();

// ---------- Info dialog
$("#info").onclick = () => {
  $("#about-db").textContent = status.db_path || "~/.shellsniffer/shellsniffer.db";
  $("#about-dir").textContent = status.data_dir || "~/.shellsniffer";
  $("#about-hp").textContent = status.honeypot_dir || "<shellSniffer folder>/honeypot";
  $("#about-hpstate").textContent = status.honeypot_active ? "currently ON" : "currently off";
  $("#about").showModal();
};
$("#about-close").onclick = () => $("#about").close();
$("#about").addEventListener("click", ev => { if (ev.target === $("#about")) $("#about").close() });   // click on backdrop

// ---------- The roaming dog: pops up on top of some panel, walks a bit, sniffs around, disappears again
(() => {
  const dog = $("#roamer"), W = 57, H = 36, SPEED = 75;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let on = false, gen = 0, timer = null;   // set from the server setting "dog" (see window.dogRoam)
  const rand = (a, b) => a + Math.random() * (b - a);
  const wait = (ms, my) => new Promise((res, rej) => { timer = setTimeout(() => my === gen ? res() : rej(), ms) });
  function spots(){
    return [...document.querySelectorAll(".topic, .session, .ev, .hit, .domain h2, #live .head, #docs .toolbar, #jday .blk, .jtoolbar, #pending")]
      .map(e => e.getBoundingClientRect())
      .filter(r => r.width > W * 2.2 && r.top > H + 50 && r.top < innerHeight - 8 && r.left >= 0 && r.right <= innerWidth);
  }
  function place(x, y){ dog.style.transition = "opacity .4s"; dog.style.left = x + "px"; dog.style.top = y + "px"; void dog.offsetWidth }
  async function roam(my){
    try {
      await wait(rand(1500, 4000), my);
      while (on && my === gen){
        const s = document.hidden ? [] : spots();
        if (s.length){
          const r = s[Math.floor(Math.random() * s.length)];
          let x = rand(r.left, r.right - W);
          place(x, r.top - H + 3);
          dog.classList.toggle("flip", Math.random() < .5);
          dog.dataset.mode = "sniff"; dog.classList.add("show");
          await wait(rand(1500, 3200), my);
          for (let k = Math.floor(rand(1, 4)); k > 0; k--){
            const nx = rand(r.left, r.right - W), dur = Math.abs(nx - x) / SPEED;
            if (dur < .4) continue;
            dog.classList.toggle("flip", nx < x);             // the drawing faces right
            dog.dataset.mode = "walk";
            dog.style.transition = `left ${dur}s linear, opacity .4s`; dog.style.left = nx + "px"; x = nx;
            await wait(dur * 1000, my);
            dog.dataset.mode = "sniff";
            await wait(rand(1400, 3500), my);
          }
          dog.classList.remove("show");
        }
        await wait(rand(6000, 18000), my);
      }
    } catch(e) { /* interrupted: a newer run took over */ }
  }
  function restart(){ gen++; clearTimeout(timer); dog.classList.remove("show"); dog.dataset.mode = ""; if (on) roam(gen) }
  // whatever the dog stands on may move: then it trots off and shows up somewhere else later
  addEventListener("scroll", () => { if (dog.classList.contains("show")) restart() }, true);
  addEventListener("resize", () => { if (dog.classList.contains("show")) restart() });
  const brand = $("header h1.brand");
  const paintBrand = () => brand.title = on ? "Send the dog back to its basket" : "Let the dog roam around";
  brand.onclick = () => saveSettings({dog: !on});
  // called whenever the settings arrive from the server
  window.dogRoam = want => { want = !!want && !reduced; if (want === on) return; on = want; paintBrand(); restart() };
  paintBrand();
})();
// ---------- SniffMagic: choose which sessions Ollama documents (time range), preview, start
const magic = (() => {
  const panel = $("#magic"), btn = $("#cluster");
  const toSec = id => $(id).value ? new Date($(id).value).getTime()/1000 : null;
  const range = () => ({start: toSec("#mg-start"), end: toSec("#mg-end") !== null ? toSec("#mg-end") + 59.999 : null});
  let previewSeq = 0;
  function setPreset(p){
    const now = new Date(), day = 86400000;
    const from = {"1h": new Date(now - 3600000), "today": new Date(now.getFullYear(), now.getMonth(), now.getDate()),
                  "7d": new Date(now - 7*day), "30d": new Date(now - 30*day)}[p];
    $("#mg-start").value = from ? toLocalInput(from) : ""; $("#mg-end").value = "";
    panel.dataset.preset = p; paint();
  }
  function paint(){
    const p = panel.dataset.preset ?? "";
    panel.querySelectorAll("[data-mpreset]").forEach(b => b.classList.toggle("on", b.dataset.mpreset === p));
    preview();
  }
  async function preview(){
    const r = range(), seq = ++previewSeq, out = $("#mg-preview"), go = $("#mg-go");
    if (r.start !== null && r.end !== null && r.start > r.end){ out.textContent = "The time range ends before it starts."; go.disabled = true; return }
    const qs = new URLSearchParams(); if (r.start !== null) qs.set("start", r.start); if (r.end !== null) qs.set("end", r.end);
    try {
      const c = await (await fetch(`/api/cluster/preview?${qs}`)).json();
      if (seq !== previewSeq) return;
      const redo = $("#mg-redo").checked, n = c.waiting + (redo ? c.documented : 0);
      out.textContent = `→ ${c.waiting} session${c.waiting===1?"":"s"} not documented yet (${c.commands} commands)` +
        (c.documented ? `, ${c.documented} already documented${redo ? " – will be redone" : ""}` : "") +
        (status.ollama ? ` · model: ${status.model}` : " · Ollama not reachable – the heuristic will be used");
      go.disabled = n === 0;
      go.textContent = n ? `✨ Start SniffMagic (${n})` : "✨ Nothing to do";
    } catch(e) { if (seq === previewSeq){ out.textContent = "The shellSniffer server is not reachable."; go.disabled = true } }
  }
  function open(show){
    panel.hidden = !show; btn.setAttribute("aria-expanded", show);
    if (show){ if (panel.dataset.preset === undefined) setPreset(""); else preview() }
  }
  btn.onclick = () => open(panel.hidden);
  $("#mg-cancel").onclick = () => open(false);
  panel.addEventListener("click", ev => { const b = ev.target.closest("[data-mpreset]"); if (b) setPreset(b.dataset.mpreset) });
  ["#mg-start","#mg-end"].forEach(id => $(id).addEventListener("change", () => { panel.dataset.preset = "custom"; paint() }));
  $("#mg-redo").addEventListener("change", preview);
  $("#mg-go").onclick = async () => {
    const r = range();
    $("#mg-go").disabled = true;
    try {
      await fetch("/api/cluster", {method:"POST", headers:{"content-type":"application/json"},
        body: JSON.stringify({start: r.start, end: r.end, include_done: $("#mg-redo").checked})});
      open(false);
    } finally { $("#mg-go").disabled = false }
  };
  return {refresh: () => { if (!panel.hidden) preview() }};
})();
async function saveSettings(patch){
  const s = await (await fetch("/api/settings", {method:"POST", headers:{"content-type":"application/json"}, body:JSON.stringify(patch)})).json();
  status.settings = {atuin:s.atuin, honeypot:s.honeypot, vectorize:s.vectorize, dog:s.dog, ollama:s.ollama}; status.honeypot_active = s.honeypot_active; status.honeypot_dir = s.honeypot_dir;
  if ("ollama" in patch) { try { status = await (await fetch("/api/status")).json() } catch(e) {} }
  renderStatus();
}
$("#vectorize").addEventListener("change", ev => saveSettings({vectorize: ev.target.checked}));
$("#ollama-on").addEventListener("change", ev => saveSettings({ollama: ev.target.checked}));
// background vectorization: switch state + progress ("123 / 456 commands vectorized")
function paintVectors(){
  const on = !!(status.settings && status.settings.vectorize), v = status.vectors || {};
  $("#vectorize").checked = on; $("#vec-wrap").classList.toggle("on", on);
  const cov = v.total ? `${v.done} / ${v.total} commands vectorized${v.model ? ` (${v.model})` : ""}` : "";
  let txt;
  if (!on) txt = `off: commands are vectorized only while you search with the shroom${cov ? ` · ${cov}` : ""}`;
  else if (v.error) txt = `⚠ ${v.error} – retrying every 30 s`;
  else if (!v.model) txt = "starting …";
  else txt = `${v.done < v.total ? "vectorizing … " : "✓ up to date · "}${cov}` +
             (v.dedicated === false ? " · tip: ollama pull nomic-embed-text is faster" : "");
  $("#vec-status").textContent = txt;
  $("#vec-status").style.color = on && v.error ? "var(--err)" : "";
}
$("#search").addEventListener("input", () => {
  shroomOn = false; $("#search-shroom").value = "";
  clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 180);
});
$("#search-shroom").addEventListener("input", () => {
  shroomOn = true; $("#search").value = "";
  clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 600);
});

// ---------- Right side: live stream
const stream = $("#stream"), follow = $("#follow");
let atBottom = true;
stream.addEventListener("scroll", () => {
  atBottom = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 40;
  follow.style.display = atBottom ? "none" : "block";
});
follow.onclick = () => { stream.scrollTop = stream.scrollHeight };

function tagFor(blockId){
  const b = blocks.get(blockId);
  if (b && b.status==="done" && b.topic_id){
    const t = topics.find(t=>t.id===b.topic_id);
    return {cls:"done", html:`<span class="tag" style="--tc:${topicColor(b.topic_id)}">${esc(t?t.name:"Topic "+b.topic_id)}</span>`, color:topicColor(b.topic_id)};
  }
  const busy = status.clustering===blockId;
  return {cls:"", html:`<span class="tag wait">${busy?"clustering …":"session running"}</span>`};
}
// ---------- Parallel sessions: every shell session gets a number + colour
const SESSION_COLORS = ["#9ab4ff","#3ddc84","#f0a35b","#d77bff","#4fd1d9","#ff7b9c","#c3d65b","#ffb347"];
const sessions = new Map();   // "shell|session" -> {label, color}
const shellCounts = {};
let shellFilter = "all";
function sessionInfo(shell, session){
  const key = `${shell}|${session}`;
  if (!sessions.has(key)){
    shellCounts[shell] = (shellCounts[shell]||0) + 1;
    sessions.set(key, {label:`${shell} #${shellCounts[shell]}`, color:SESSION_COLORS[sessions.size % SESSION_COLORS.length]});
    renderFilters();
  }
  return sessions.get(key);
}
function renderFilters(){
  const shells = Object.keys(shellCounts);
  const box = $("#filters");
  if (shells.length < 2 && sessions.size < 2){ box.innerHTML = ""; return }
  const btn = (k, txt) => `<button class="${shellFilter===k?"on":""}" data-f="${esc(k)}">${esc(txt)}</button>`;
  box.innerHTML = btn("all", `All (${sessions.size} sessions)`) +
    shells.map(sh => btn(sh, `${sh} (${shellCounts[sh]})`)).join("");
}
$("#filters").addEventListener("click", ev => {
  const b = ev.target.closest("button"); if (!b) return;
  shellFilter = b.dataset.f; renderFilters();
  document.querySelectorAll(".ev").forEach(applyFilter);
  stream.scrollTop = stream.scrollHeight;
});
function applyFilter(div){ div.classList.toggle("hidden", shellFilter!=="all" && div.dataset.shell!==shellFilter) }

function addEvent(e, isNew){
  const div = document.createElement("div");
  div.className = "ev" + (e.exit_code ? " fail":"") + (isNew?" new":"");
  div.dataset.block = e.block_id;
  div.dataset.id = e.id;
  div.dataset.shell = e.shell;
  const si = sessionInfo(e.shell, e.session);
  div.style.setProperty("--sc", si.color);
  div.innerHTML = evHtml(e, isNew);
  stream.insertBefore(div, follow);
  applyTag(div);
  applyFilter(div);
  while (stream.querySelectorAll(".ev").length > 500) stream.querySelector(".ev").remove();
  if (atBottom) stream.scrollTop = stream.scrollHeight;
}
function evHtml(e, isNew){
  const si = sessionInfo(e.shell, e.session);
  const out = e.output ? `<details ${isNew && e.output.split("\n").length<=6 ? "open":""}><summary>Output (${e.output.split("\n").length} line${e.output.split("\n").length===1?"":"s"})</summary><pre>${esc(e.output)}</pre></details>` : "";
  return `<div class="ctx"><span>${fmtTime(e.ts)}</span><span class="shell" title="${esc(e.host)} · session ${esc(e.session)}">${esc(si.label)}</span><span>${esc(e.cwd)}</span>
    ${e.exit_code ? `<span class="exit">exit ${e.exit_code}</span>`:""}<span>${fmtDur(e.duration_ms)}</span><span class="tagslot"></span></div>
    <div class="cmd">${esc(e.command)}</div>${out}`;
}
function applyTag(div){
  const t = tagFor(+div.dataset.block);
  div.querySelector(".tagslot").innerHTML = t.html;
  div.classList.toggle("done", t.cls==="done");
  if (t.color) div.style.setProperty("--tc", t.color);
}
function refreshEventTags(blockId){
  const sel = blockId ? `.ev[data-block="${blockId}"]` : ".ev";
  document.querySelectorAll(sel).forEach(applyTag);
}
function renderPending(){
  const now = Date.now()/1000;
  const open = [...blocks.values()].filter(b => b.status!=="done");
  $("#pending").innerHTML = open.map(b => {
    const [, sh="", ses=""] = (b.skey||"").split("|");
    const shell = sessions.get(`${sh}|${ses}`)?.label || sh;
    if (status.clustering===b.id) return `<div class="busy">● Session in ${esc(shell)} (${b.n} commands) is being assigned to a topic …</div>`;
    const queued = status.cluster_requested ? "queued for Ollama" : "click “SniffMagic” to assign a topic";
    if (b.status==="ready") return `<div>○ Session in ${esc(shell)} (${b.n} command${b.n===1?"":"s"}) · ${queued}</div>`;
    return `<div>○ Running session in ${esc(shell)}: ${b.n} command${b.n===1?"":"s"} · ${queued}</div>`;
  }).join("");
}
setInterval(renderPending, 1000);

async function softReload(){
  document.querySelectorAll(".ev").forEach(x => x.remove());
  for (const b of await (await fetch("/api/blocks/open")).json()) blocks.set(b.id, b);
  await loadTopics();
  for (const e of await (await fetch("/api/events?limit=300")).json()) addEvent(e, false);
  stream.scrollTop = stream.scrollHeight;
  if (view==="journal") renderJournal();
}
function renderStatus(){
  if (typeof paintVectors === "function") paintVectors();
  if (window.dogRoam && status.settings) window.dogRoam(status.settings.dog !== false);
  if (window.paintSettings && $("#settingsdlg").open) window.paintSettings();
  const a = status.atuin || {}, names = Object.keys(a).filter(k => k!=="worker");
  const at = $("#st-atuin");
  const cb = $("#cluster");
  cb.classList.toggle("busy", !!(status.cluster_requested || status.clustering));
  cb.textContent = status.cluster_requested || status.clustering ? `✨ SniffMagic running …${status.queued ? ` (${status.queued} left)` : ""}` : `✨ SniffMagic${status.pending ? ` (${status.pending})` : ""}`;
  if (typeof magic !== "undefined") magic.refresh();
  at.hidden = !names.length;
  if (names.length){
    const ok = names.filter(k => a[k].ok);
    at.hidden = false;
    at.textContent = ok.length ? `Atuin: ${ok.map(k => k==="local" ? "Windows" : k.replace("wsl:","WSL ")).join(", ")}` : "Atuin not found";
    at.title = names.map(k => `${k}: ${a[k].ok ? (a[k].db||"ok") : a[k].error}`).join("\n");
    at.className = "chip " + (ok.length ? "ok" : "");
  }
  const o = $("#st-ollama"), ow = $("#st-ollama-wrap"), oon = ollamaOn();
  $("#ollama-on").checked = oon;
  o.textContent = !oon ? "Ollama disabled" : status.ollama ? `Ollama: ${status.model}` : "Ollama not reachable · heuristic";
  ow.className = "chip ollamachip " + (!oon ? "off" : status.ollama ? "ok" : "err");
  cb.disabled = !oon;
  cb.title = oon ? "SniffMagic: choose a time range, then Ollama sorts those sessions into topics with a title and summary. Ollama never runs on its own."
                 : "SniffMagic needs Ollama – it is disabled (Ollama chip or ⚙ Settings)";
  if (!oon && !$("#magic").hidden){ $("#magic").hidden = true; cb.setAttribute("aria-expanded", false) }
  paintShroomField(); paintBalance();
  $("#st-events").textContent = `${status.events ?? 0} command${status.events===1?"":"s"}`;
  $("#st-topics").textContent = `${status.topics ?? 0} topic${status.topics===1?"":"s"}`;
}

// ---------- Initial load + WebSocket
async function init(){
  domains = (await (await fetch("/api/ontology")).json()).domains;
  status = await (await fetch("/api/status")).json(); renderStatus();
  for (const b of await (await fetch("/api/blocks/open")).json()) blocks.set(b.id, b);
  await loadTopics();
  for (const t of topics) for (const b of t.blocks) blocks.set(b.id, {...b, status:"done", topic_id:t.id});
  for (const e of await (await fetch("/api/events?limit=300")).json()) addEvent(e, false);
  stream.scrollTop = stream.scrollHeight;
  connect();
}
function connect(){
  const ws = new WebSocket(`${location.protocol==="https:"?"wss":"ws"}://${location.host}/ws`);
  ws.onopen = () => { $("#pulse").classList.add("on"); $("#conn").textContent = "observing"; };
  ws.onclose = () => { $("#pulse").classList.remove("on"); $("#conn").textContent = "disconnected – reconnecting …"; setTimeout(connect, 2000) };
  ws.onmessage = async m => {
    const msg = JSON.parse(m.data);
    if (msg.type==="event"){ addEvent(msg.event, true); status.events=(status.events||0)+1; renderStatus() }
    else if (msg.type==="block" && msg.block){ blocks.set(msg.block.id, msg.block); refreshEventTags(msg.block.id); renderPending(); if (view==="journal") { clearTimeout(window.__jt); window.__jt = setTimeout(renderJournal, 400) } }
    else if (msg.type==="event_update"){
      const div = document.querySelector(`.ev[data-id="${msg.event.id}"]`);
      if (div){ div.innerHTML = evHtml(msg.event, true); applyTag(div) }
    }
    else if (msg.type==="reload"){ clearTimeout(window.__rl); window.__rl = setTimeout(softReload, 800) }
    else if (msg.type==="status"){ status = msg.status; renderStatus(); renderPending(); refreshEventTags() }
    else if (msg.type==="topics_changed"){ await loadTopics(msg.topic_id); if (view==="journal") renderJournal() }
  };
}
// ================================================================ Journal view
let view = "live", selDay = null;
document.querySelectorAll("#views button").forEach(b => b.onclick = () => setView(b.dataset.view));
function setView(v){
  view = v;
  document.querySelectorAll("#views button").forEach(b => b.classList.toggle("on", b.dataset.view===v));
  $("#livemain").hidden = v!=="live";
  $("#journal").hidden = v!=="journal";
  try { localStorage.setItem("shellsniffer.view", v) } catch(e) {}
  if (v==="journal") renderJournal();
}
["#jrange","#jlevel","#jdomain","#jproject","#jtool"].forEach(id => $(id).onchange = () => renderJournal());
function fillFilters(){
  const keep = (sel, opts, first) => {
    const el = $(sel), cur = el.value;
    el.innerHTML = `<option value="">${first}</option>` + opts.map(([v,l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join("");
    if (opts.some(([v]) => v===cur)) el.value = cur;
  };
  const all = topics.flatMap(t => t.blocks);
  const cnt = f => { const m = new Map(); all.forEach(b => f(b).forEach(k => k && m.set(k, (m.get(k)||0) + b.n))); return [...m].sort((a,b) => b[1]-a[1]) };
  keep("#jdomain", domains.filter(d => topics.some(t => (t.domain||"sonstiges")===d.key && t.blocks.length)).map(d => [d.key, d.name]), "All domains");
  keep("#jproject", cnt(b => [b.project]).map(([k,n]) => [k, `${k} (${n})`]), "All projects");
  keep("#jtool", cnt(b => b.tools||[]).slice(0,40).map(([k,n]) => [k, `${k} (${n})`]), "All tools");
}
window.addEventListener("resize", () => { if (view==="journal") renderJournal() });

const dayKey = ts => { const d = new Date(ts*1000); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}` };
const dayLabel = k => new Date(k+"T12:00").toLocaleDateString(undefined,{weekday:"short",day:"2-digit",month:"2-digit",year:"numeric"});
const OPEN_ID = 0;   // pseudo topic for commands not clustered yet

function journalData(){
  const days = new Map(), tps = new Map(), links = new Map();
  const add = (day, tid, name, n, block) => {
    if (!days.has(day)) days.set(day, {key:day, n:0, blocks:[]});
    if (!tps.has(tid)) tps.set(tid, {id:tid, name, n:0});
    const d = days.get(day); d.n += n; if (block) d.blocks.push(block);
    tps.get(tid).n += n;
    const k = day+"|"+tid;
    links.set(k, {day, tid, n:(links.get(k)?.n||0)+n});
  };
  const level = $("#jlevel").value, fd = $("#jdomain").value, fp = $("#jproject").value, ft = $("#jtool").value;
  for (const t of topics){
    const dom = t.domain || "sonstiges";
    if (fd && dom!==fd) continue;
    for (const b of t.blocks){
      if (fp && b.project!==fp) continue;
      if (ft && !(b.tools||[]).includes(ft)) continue;
      const id = level==="domain" ? "d:"+dom : t.id, name = level==="domain" ? domainOf(dom).name : t.name;
      add(dayKey(b.start_ts), id, name, b.events.length || b.n, {...b, topic:t});
      tps.get(id).domain = dom;
    }
  }
  if (!fd && !fp && !ft) for (const b of blocks.values()) if (b.status!=="done") add(dayKey(b.start_ts), OPEN_ID, "Not assigned yet", b.n, null);
  let dayList = [...days.values()].sort((a,b) => b.key.localeCompare(a.key));
  const range = +$("#jrange").value;
  if (range){ const cut = dayKey(Date.now()/1000 - (range-1)*86400); dayList = dayList.filter(d => d.key >= cut) }
  const keep = new Set(dayList.map(d=>d.key));
  const linkList = [...links.values()].filter(l => keep.has(l.day));
  const tn = new Map(); linkList.forEach(l => tn.set(l.tid, (tn.get(l.tid)||0) + l.n));
  const topicList = [...tps.values()].filter(t => tn.has(t.id)).map(t => ({...t, n:tn.get(t.id)}));
  return {dayList, topicList, linkList};
}

function renderJournal(){
  const {dayList, topicList, linkList} = journalData();
  const wrap = $("#jsvgwrap");
  if (!dayList.length){
    wrap.innerHTML = `<div class="empty">No entries in the selected time range yet.</div>`;
    $("#jday").innerHTML = ""; return;
  }
  if (!selDay || !dayList.some(d => d.key===selDay)) selDay = dayList[0].key;
  const W = Math.max(520, wrap.clientWidth), NW = Math.min(210, W*0.3), GAP = 12, PAD = 8, HEAD = 26;
  const maxN = Math.max(...linkList.map(l => l.n));
  const lw = n => Math.max(2, 26 * n / maxN);          // line width linear to the number of commands
  const nodeH = (sum) => Math.max(42, sum + PAD*2 + 16);
  // days: newest on top; height from the sum of line widths
  const sumW = (arr) => arr.reduce((a,l) => a + lw(l.n) + 2, 0);
  let y = HEAD;
  for (const d of dayList){ d.links = linkList.filter(l => l.day===d.key); d.h = nodeH(sumW(d.links)); d.y = y; y += d.h + GAP }
  const dayH = y;
  // sort topics by the mean position of their days (fewer crossings); "open" last
  const dayPos = new Map(dayList.map(d => [d.key, d.y + d.h/2]));
  for (const t of topicList){
    t.links = linkList.filter(l => l.tid===t.id);
    t.bc = t.links.reduce((a,l) => a + dayPos.get(l.day)*l.n, 0) / t.n;
  }
  // topics grouped by domain (fixed order), within a domain by the position of their days; "open" last
  topicList.sort((a,b) => (a.id===OPEN_ID) - (b.id===OPEN_ID) || domainOf(a.domain).slot - domainOf(b.domain).slot || a.bc - b.bc);
  let ty = HEAD;
  for (const t of topicList){ t.h = nodeH(sumW(t.links)); t.y = ty; ty += t.h + GAP }
  const H = Math.max(dayH, ty);
  // centre vertically if one column is shorter
  const shift = (list, colH) => { const off = (H - colH)/2; list.forEach(n => n.y += off) };
  shift(dayList, dayH); shift(topicList, ty);
  const tpos = new Map(topicList.map(t => [t.id, t]));
  // stack the attachment points (sorted by the opposite side)
  const ends = new Map();
  for (const d of dayList){
    let yy = d.y + PAD + 16;
    d.links.sort((a,b) => tpos.get(a.tid).y - tpos.get(b.tid).y)
      .forEach(l => { const w = lw(l.n); ends.set(l, {y1: yy + w/2}); yy += w + 2 });
  }
  for (const t of topicList){
    let yy = t.y + PAD + 16;
    t.links.sort((a,b) => dayPos.get(a.day) - dayPos.get(b.day))
      .forEach(l => { const w = lw(l.n); ends.get(l).y2 = yy + w/2; yy += w + 2 });
  }
  const x1 = NW, x2 = W - NW, xm = (x1 + x2)/2;
  const tdom = new Map(topicList.map(t => [String(t.id), t.domain]));
  const color = tid => tid===OPEN_ID ? "var(--s-other)" : domainColor(tdom.get(String(tid)));
  let svg = `<svg class="jsvg" viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Days and topics">`;
  svg += `<text class="colhead" x="0" y="14">Days</text><text class="colhead" x="${W}" y="14" text-anchor="end">${$("#jlevel").value==="domain" ? "Domains" : "Topics"}</text>`;
  linkList.forEach((l, i) => {
    const e = ends.get(l); if (!e || e.y2==null) return;
    svg += `<path class="link" data-i="${i}" data-day="${l.day}" data-tid="${l.tid}" style="stroke:${color(l.tid)}"
      stroke-width="${lw(l.n).toFixed(1)}" d="M${x1},${e.y1} C${xm},${e.y1} ${xm},${e.y2} ${x2},${e.y2}"/>`;
  });
  for (const d of dayList){
    svg += `<g class="node day ${d.key===selDay?"sel":""}" data-day="${d.key}" transform="translate(0,${d.y})">
      <rect width="${NW}" height="${d.h}" rx="8"/>
      <text class="lbl" x="10" y="19">${esc(dayLabel(d.key))}</text>
      <text class="cnt" x="10" y="${d.h-9}">${d.n} commands · ${d.links.filter(l=>l.tid!==OPEN_ID).length} ${$("#jlevel").value==="domain" ? (d.links.filter(l=>l.tid!==OPEN_ID).length===1 ? "domain" : "domains") : (d.links.filter(l=>l.tid!==OPEN_ID).length===1 ? "topic" : "topics")}</text></g>`;
  }
  for (const t of topicList){
    const name = t.name.length > 24 ? t.name.slice(0,23)+"…" : t.name;
    svg += `<g class="node topic" data-tid="${t.id}" transform="translate(${x2},${t.y})">
      <rect width="${NW}" height="${t.h}" rx="8"/>
      <rect width="4" height="${t.h-16}" x="0" y="8" rx="2" style="fill:${color(t.id)};stroke:none"/>
      <text class="lbl" x="12" y="19"><title>${esc(t.name)}</title>${esc(name)}</text>
      <text class="cnt" x="12" y="${t.h-9}">${t.id===OPEN_ID || String(t.id).startsWith("d:") ? "" : esc(domainOf(t.domain).name) + " · "}${t.n} commands</text></g>`;
  }
  svg += `</svg>`;
  wrap.innerHTML = svg;
  const el = wrap.querySelector("svg"), tip = $("#jtip");
  const focus = (pred) => { el.classList.add("focus"); el.querySelectorAll(".link").forEach(p => p.classList.toggle("hl", pred(p))) };
  const unfocus = () => { el.classList.remove("focus"); tip.hidden = true };
  const showTip = (ev, html) => {
    tip.innerHTML = html; tip.hidden = false;
    const r = $("#journal").getBoundingClientRect();
    tip.style.left = (ev.clientX - r.left + 14) + "px"; tip.style.top = (ev.clientY - r.top + 12) + "px";
  };
  const tname = tid => topicList.find(t => t.id==tid)?.name || "";
  el.querySelectorAll(".link").forEach(p => {
    p.addEventListener("mousemove", ev => { const l = linkList[+p.dataset.i]; focus(q => q===p);
      showTip(ev, `<b>${esc(dayLabel(l.day))}</b> → ${esc(tname(l.tid))}<br>${l.n} command${l.n===1?"":"s"}`) });
    p.addEventListener("mouseleave", unfocus);
    p.addEventListener("click", () => selectDay(p.dataset.day));
  });
  el.querySelectorAll(".node.day").forEach(g => {
    g.addEventListener("mouseenter", () => focus(p => p.dataset.day===g.dataset.day));
    g.addEventListener("mouseleave", unfocus);
    g.addEventListener("click", () => selectDay(g.dataset.day));
  });
  el.querySelectorAll(".node.topic").forEach(g => {
    g.addEventListener("mouseenter", () => focus(p => p.dataset.tid===g.dataset.tid));
    g.addEventListener("mouseleave", unfocus);
  });
  renderDay(dayList.find(d => d.key===selDay), linkList, topicList);
}
function selectDay(k){ selDay = k; renderJournal() }

function renderDay(d, linkList, topicList){
  const byTopic = new Map();
  for (const b of d.blocks.sort((a,b) => a.start_ts - b.start_ts)){
    if (!byTopic.has(b.topic.id)) byTopic.set(b.topic.id, []);
    byTopic.get(b.topic.id).push(b);
  }
  const open = linkList.find(l => l.day===d.key && l.tid===OPEN_ID);
  const fmtHM = ts => new Date(ts*1000).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"});
  let html = `<h2>${esc(dayLabel(d.key))}</h2><div class="sub">${d.n} commands in ${byTopic.size} topic${byTopic.size===1?"":"s"}${open?` · ${open.n} not assigned yet`:""}</div>`;
  const order = [...byTopic.entries()].sort((a,b) => b[1].reduce((s,x)=>s+x.n,0) - a[1].reduce((s,x)=>s+x.n,0));
  for (const [tid, bl] of order){
    const t = bl[0].topic, n = bl.reduce((s,x) => s + (x.events.length||x.n), 0);
    html += `<div class="tsec"><span class="dot" style="width:10px;height:10px;border-radius:50%;background:${topicColor(tid)}"></span>${esc(t.name)}<span class="n">${esc(domainOf(t.domain).name)} · ${n} commands</span></div>`;
    for (const b of bl){
      html += `<div class="blk"><h3>${esc(b.title)}</h3><div class="meta">${fmtHM(b.start_ts)}–${fmtHM(b.end_ts)} · ${b.n} commands</div><div class="md">${linkRefs(md(b.summary), b.events)}</div></div>`;
    }
  }
  $("#jday").innerHTML = html;
}

init().then(() => { let v = "live"; try { v = localStorage.getItem("shellsniffer.view") || "live" } catch(e) {} if (v!=="live") setView(v) });
