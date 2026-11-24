/* shellSniffer web UI – Light / dark mode, the ⚙ Settings dialog, saving settings to the server and the
 * continuous-shrooming progress. Settings live on the server (GET/POST /api/settings).
 */
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
