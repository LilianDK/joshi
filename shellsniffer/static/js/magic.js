/* shellSniffer web UI – ✨ SniffMagic: choose a time range, preview how many sessions are affected, start
 * the Ollama documentation run (POST /api/cluster).
 */
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
