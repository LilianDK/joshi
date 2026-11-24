/* shellSniffer web UI – Ask the shroom: Enter in "Sniff logs with shroom" streams an LLM answer grounded in
 * the user's logs (GET /api/ask, NDJSON), with clickable [n] sources.
 */
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
