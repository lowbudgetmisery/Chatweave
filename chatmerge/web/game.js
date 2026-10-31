"use strict";
// Starcatch browser source (/game). Shows the current star, a timer, who caught it, and
// !stars toasts. Everything comes from the app over the same websocket as the chat overlay.
// URL options: ?scale=1.0  ?bg=1 (preview background) / ?bg=0 (transparent)
(() => {
  const params = new URLSearchParams(location.search);
  const scale = Number.parseFloat(params.get("scale") || "1");
  if (Number.isFinite(scale) && scale > 0.3 && scale < 4) document.documentElement.style.setProperty("--s", String(scale));
  const bgParam = params.get("bg");
  const inOBS = typeof window.obsstudio !== "undefined";
  if (bgParam === "1" || (bgParam !== "0" && !inOBS)) document.body.classList.add("preview");

  const stage = document.getElementById("stage");
  const toasts = document.getElementById("toasts");
  const hint = document.getElementById("hint");
  const SVG_NS = "http://www.w3.org/2000/svg";
  const LEAD = { common: "A star drifted into view", rare: "Something rare is drifting by", legendary: "A legendary star appeared!" };
  let shownId = null;
  let card = null;

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function svg(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
    return node;
  }

  // Same species key -> same constellation every time (seeded random).
  function seeded(key) {
    let h = 2166136261;
    for (const ch of key) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
    return () => {
      h = (h + 0x6d2b79f5) | 0;
      let t = Math.imul(h ^ (h >>> 15), 1 | h);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function constellation(key, rarity) {
    const rand = seeded(key);
    const W = 400, H = 170, pad = 26;
    const count = { common: 6, rare: 8, legendary: 10 }[rarity] || 6;
    const pts = [];
    for (let tries = 0; pts.length < count && tries < 500; tries++) {
      const p = { x: pad + rand() * (W - 2 * pad), y: pad + rand() * (H - 2 * pad) };
      if (pts.every((q) => Math.hypot(p.x - q.x, p.y - q.y) > 46)) pts.push(p);
    }
    // Minimum spanning tree (Prim): every star joined, no long lines crossing the figure.
    const inTree = [0];
    const edges = [];
    while (inTree.length < pts.length) {
      let best = null;
      for (const i of inTree) for (let j = 0; j < pts.length; j++) {
        if (inTree.includes(j)) continue;
        const d = Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y);
        if (!best || d < best.d) best = { i, j, d };
      }
      edges.push(best);
      inTree.push(best.j);
    }

    const root = svg("svg", { class: "sky", viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true" });
    const id = `g${key.replace(/[^a-z0-9]/gi, "")}`;
    const defs = svg("defs", {});
    const glow = svg("filter", { id, x: "-150%", y: "-150%", width: "400%", height: "400%" });
    glow.append(svg("feGaussianBlur", { stdDeviation: 4, result: "b" }));
    const merge = svg("feMerge", {});
    merge.append(svg("feMergeNode", { in: "b" }), svg("feMergeNode", { in: "SourceGraphic" }));
    glow.append(merge);
    defs.append(glow);
    root.append(defs);
    edges.forEach((e, n) => {
      const a = pts[e.i], b = pts[e.j];
      const len = Math.ceil(Math.hypot(a.x - b.x, a.y - b.y));
      const line = svg("line", { class: "link", x1: a.x, y1: a.y, x2: b.x, y2: b.y });
      line.style.setProperty("--len", String(len));
      line.style.setProperty("--delay", `${200 + n * 140}ms`);
      root.append(line);
    });
    // First point is the focal star: biggest, with the strongest halo.
    pts.forEach((p, n) => {
      const r = n === 0 ? 5.2 : 2 + rand() * 1.8;
      root.append(svg("circle", { class: n === 0 ? "halo focal" : "halo", cx: p.x, cy: p.y, r: r * (n === 0 ? 2.6 : 2.0), filter: `url(#${id})` }));
      const star = svg("circle", { class: "star", cx: p.x, cy: p.y, r });
      star.style.setProperty("--tw", `${Math.round(rand() * 3000)}ms`);
      root.append(star);
    });
    return root;
  }

  function drawCatchers(box, spawn) {
    box.replaceChildren();
    const names = spawn.catchers.map((c) => c.name);
    if (!names.length) {
      if (spawn.misses) box.append(el("span", "dim", `${spawn.misses} ${spawn.misses === 1 ? "throw" : "throws"} missed...`));
      return;
    }
    box.append(el("span", "dim", "Caught by "));
    const first = names.slice(0, 4);
    first.forEach((n, i) => {
      box.append(el("b", "", n));
      if (i < first.length - 1) box.append(document.createTextNode(", "));
    });
    if (names.length > first.length) box.append(el("span", "dim", ` +${names.length - first.length} more`));
  }

  function build(game) {
    const spawn = game.spawn;
    const sp = spawn.species;
    const root = el("article", `card r-${sp.rarity}`);
    root.style.setProperty("--species", sp.color);
    const top = el("div", "top");
    top.append(el("span", "rarity", sp.rarity), el("span", "lead", LEAD[sp.rarity] || LEAD.common));
    const prompt = el("div", "prompt");
    prompt.append(document.createTextNode("Type"), el("span", "cmd", game.command), document.createTextNode("to catch it"));
    const timer = el("div", "timer");
    const bar = el("i");
    timer.append(bar);
    const catchers = el("div", "catchers");
    root.append(top, constellation(sp.key, sp.rarity), el("h1", "name", sp.name), prompt, timer, catchers);
    // Call after the card is on the page. The timer runs from what's left now, so late page
    // loads and OBS refreshes stay in sync.
    function startTimer() {
      const left = Math.max(0, spawn.ends_at * 1000 - Date.now());
      const windowMs = Math.max(1, (game.window_s || 60) * 1000);
      bar.style.transform = `scaleX(${Math.min(1, left / windowMs)})`;
      void bar.offsetWidth; // commit the starting width before animating (else it jumps to empty)
      bar.style.transition = `transform ${left}ms linear`;
      bar.style.transform = "scaleX(0)";
    }
    return { root, catchers, prompt, timer, startTimer };
  }

  function show(game) {
    hint.classList.toggle("show", !game || !game.enabled);
    if (!game || !game.enabled) {
      hint.textContent = "Starcatch is off. Turn it on in config.toml under [game], then restart.";
      stage.replaceChildren();
      shownId = null;
      return;
    }
    const spawn = game.spawn;
    if (!spawn) {
      if (card) {
        const old = card.root;
        old.classList.add("leaving");
        old.addEventListener("animationend", () => old.remove(), { once: true });
        setTimeout(() => old.remove(), 1000);
      }
      card = null;
      shownId = null;
      hint.classList.add("show");
      hint.textContent = `Waiting for the next star (or use "Spawn a star" in the dock)`;
      return;
    }
    hint.classList.remove("show");
    if (spawn.id !== shownId) {
      stage.replaceChildren();
      card = build(game);
      stage.append(card.root);
      card.startTimer();
      shownId = spawn.id;
    }
    drawCatchers(card.catchers, spawn);
    if (spawn.over && !card.root.classList.contains("over")) {
      card.root.classList.add("over");
      card.prompt.remove();
      card.timer.remove();
      const n = spawn.catchers.length;
      const text = n ? `${n} ${n === 1 ? "viewer" : "viewers"} caught the ${spawn.species.name}!`
        : `The ${spawn.species.name} slipped away...`;
      card.root.insertBefore(el("div", "result", text), card.catchers);
    }
  }

  function toast(text) {
    const t = el("div", "toast", text);
    toasts.append(t);
    while (toasts.children.length > 3) toasts.firstChild.remove();
    setTimeout(() => t.remove(), 6200);
  }

  function handle(msg) {
    if (msg.op === "hello" || msg.op === "resync") show(msg.game);
    else if (msg.op === "game") show(msg.game);
    else if (msg.op === "game_toast") toast(msg.text);
  }

  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    ws.addEventListener("message", (ev) => {
      try { handle(JSON.parse(ev.data)); } catch (err) { console.error("bad message", err, ev.data); }
    });
    ws.addEventListener("close", () => setTimeout(connect, 2000));
  }
  connect();
})();
