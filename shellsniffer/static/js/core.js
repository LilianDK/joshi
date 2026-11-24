/* shellSniffer web UI – Shared helpers and state: DOM/format helpers, colours, tiny Markdown renderer.
 * Globals used by all other files: $, esc, fmt*, domains, topics, blocks, status, md().
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
