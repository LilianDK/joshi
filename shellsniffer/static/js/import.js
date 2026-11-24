/* shellSniffer web UI – 📥 Import dumps: upload collected shell dumps (files or folders, drag & drop),
 * one POST /api/import per file; the server detects the format.
 */
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
