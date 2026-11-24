/* shellSniffer web UI – Journal view: days on the left, topics/domains on the right, lines weighted by the
 * number of commands. Also starts the app (init()) – load this file last.
 */
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
