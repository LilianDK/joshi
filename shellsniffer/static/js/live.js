/* shellSniffer web UI – Right side: live stream of every command, per-session colours and filters, the
 * header status (renderStatus), initial load and the WebSocket for live updates.
 */
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
