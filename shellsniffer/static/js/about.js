/* shellSniffer web UI – ⓘ dialog: what shellSniffer is and where the logs are stored (paths from /api/status).
 */
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
