/* shellSniffer web UI – The roaming pixel dog: shows up on some panel, walks, sniffs, disappears.
 * On/off is the server setting "dog" (⚙ Settings or a click on the logo).
 */
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
