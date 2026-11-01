"use strict";
// Chat game browser source (/game). Shows the current heist or steal race: sign-up timer,
// crew, the heist playing out, the result and the loot. Plus !stash toasts. Everything comes
// from the app over the same websocket as the chat overlay; nothing here talks to chat.
// Labels, colors and the emblem come from the theme file ([card] in heist.toml).
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
  let card = null; // { id, phase, root, body, ... }
  let themed = null; // the theme last applied (colors, page title)

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

  // Gliese 667C: a red dwarf with a planet on its orbit (Lumi's home star).
  function emblem(theme) {
    if (theme.emblem === "none") return null;
    if (theme.emblem === "image" && typeof theme.emblem_url === "string" && theme.emblem_url.startsWith("/game/emblem")) {
      const img = el("img", "emblem emblem-img");
      img.src = theme.emblem_url;
      img.alt = "";
      img.addEventListener("error", () => img.remove(), { once: true });
      return img;
    }
    const root = svg("svg", { class: "emblem", viewBox: "0 0 64 64", "aria-hidden": "true" });
    const defs = svg("defs", {});
    const grad = svg("radialGradient", { id: "dwarf", cx: "38%", cy: "35%", r: "65%" });
    grad.append(svg("stop", { offset: "0%", "stop-color": "#fff2d6" }), svg("stop", { offset: "35%", "stop-color": "#ffb36b" }),
      svg("stop", { offset: "80%", "stop-color": "#e8483a" }), svg("stop", { offset: "100%", "stop-color": "#8f1f2c" }));
    const glow = svg("radialGradient", { id: "dwarfGlow", cx: "50%", cy: "50%", r: "50%" });
    glow.append(svg("stop", { offset: "40%", "stop-color": "rgba(255,110,80,0.55)" }), svg("stop", { offset: "100%", "stop-color": "rgba(255,110,80,0)" }));
    defs.append(grad, glow);
    root.append(defs, svg("circle", { cx: 32, cy: 32, r: 30, fill: "url(#dwarfGlow)", class: "corona" }));
    root.append(svg("ellipse", { cx: 32, cy: 32, rx: 27, ry: 9, class: "orbit", transform: "rotate(-18 32 32)" }));
    root.append(svg("circle", { cx: 32, cy: 32, r: 12, fill: "url(#dwarf)" }));
    // the planet rides the orbit ellipse (same shape and tilt as the drawn orbit)
    const tilt = svg("g", { transform: "rotate(-18 32 32)" });
    const planet = svg("circle", { class: "planet", r: 2.6 });
    const move = svg("animateMotion", { dur: "9s", repeatCount: "indefinite", path: "M59,32 a27,9 0 1,1 -54,0 a27,9 0 1,1 54,0" });
    planet.append(move);
    tilt.append(planet);
    root.append(tilt);
    return root;
  }

  function cmdChip(text) { return el("span", "cmd", text); }

  // "Type {command} to join the crew" -> text, chip, text
  function promptLine(template, command) {
    const box = el("div", "prompt");
    const [before, after] = template.split("{command}");
    if (before.trim()) box.append(document.createTextNode(before.trim()));
    box.append(cmdChip(command));
    if ((after || "").trim()) box.append(document.createTextNode(after.trim()));
    return box;
  }

  function rgba(hex, alpha) {
    const n = Number.parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  // mix toward white (no color-mix(): older OBS browsers don't have it)
  function lighten(hex, amount) {
    const n = Number.parseInt(hex.slice(1), 16);
    const ch = (v) => Math.round(v + (255 - v) * amount);
    return `rgb(${ch((n >> 16) & 255)}, ${ch((n >> 8) & 255)}, ${ch(n & 255)})`;
  }

  function applyTheme(theme) {
    if (themed === theme) return;
    themed = theme;
    const root = document.documentElement.style;
    for (const [name, value] of [["--dwarf", theme.accent], ["--gold", theme.gold], ["--alarm", theme.alarm]]) {
      if (/^#[0-9a-fA-F]{6}$/.test(value || "")) root.setProperty(name, value);
    }
    if (/^#[0-9a-fA-F]{6}$/.test(theme.accent || "")) {
      root.setProperty("--glow", rgba(theme.accent, 0.2));
      root.setProperty("--tag-start", lighten(theme.accent, 0.55));
    }
    if (theme.title) document.title = theme.title;
  }

  // "1 Gumi", "2 Gumis"
  function crewWord(game, n) { return n === 1 ? game.crew.one : game.crew.name; }

  function names(box, list, total, label) {
    box.replaceChildren();
    if (!total) return;
    box.append(el("span", "count", `${total} ${label}`));
    const shown = list.map((m) => m.name);
    if (shown.length) {
      box.append(el("span", "dim", " · "));
      shown.forEach((n, i) => {
        box.append(el("b", "", n));
        if (i < shown.length - 1) box.append(document.createTextNode(", "));
      });
      if (total > shown.length) box.append(el("span", "dim", ` +${total - shown.length}`));
    }
  }

  function timerBar(ev) {
    const timer = el("div", "timer");
    const bar = el("i");
    timer.append(bar);
    // Runs from what's left now, so late page loads and OBS refreshes stay in sync.
    const start = () => {
      const left = Math.max(0, ev.ends_at * 1000 - Date.now());
      const windowMs = Math.max(1, (ev.window_s || 60) * 1000);
      bar.style.transform = `scaleX(${Math.min(1, left / windowMs)})`;
      void bar.offsetWidth; // commit the starting width before animating
      bar.style.transition = `transform ${left}ms linear`;
      bar.style.transform = "scaleX(0)";
    };
    return { timer, start };
  }

  function lootLine(entry) {
    const row = el("li", `loot r-${entry.loot.rarity}`);
    row.append(el("b", "", entry.name), el("span", "item", entry.loot.name), el("span", "rarity", entry.loot.rarity));
    return row;
  }

  // ---- card per event -------------------------------------------------------------
  function newCard(game, ev) {
    const root = el("article", `card k-${ev.kind}`);
    const head = el("header", "head");
    const titles = el("div", "titles");
    const theme = game.card;
    titles.append(el("span", "tag", ev.kind === "heist" ? theme.heist_tag : theme.steal_tag));
    if (theme.subtitle) titles.append(el("span", "from", theme.subtitle));
    head.append(titles);
    const mark = emblem(theme);
    if (mark) head.append(mark);
    const body = el("div", "body");
    root.append(head, body);
    stage.replaceChildren(root);
    return { id: ev.id, phase: null, root, body, game };
  }

  function drawJoin(c, ev, game) {
    const b = c.body;
    b.replaceChildren();
    if (ev.kind === "heist") {
      const label = el("div", "label");
      label.append(el("span", "", game.card.target_label),
        el("span", `heat h${ev.target.difficulty}`, game.card.heat[ev.target.difficulty - 1] || ""));
      b.append(label, el("h1", "name", ev.target.name), el("p", "line", ev.text));
      const prompt = promptLine(game.card.join_prompt, game.commands.heist);
      const t = timerBar(ev);
      const crewRow = el("div", "crew");
      const odds = el("div", "odds");
      b.append(prompt, t.timer, crewRow, odds);
      c.crewRow = crewRow;
      c.odds = odds;
      requestAnimationFrame(t.start);
    } else {
      const label = el("div", "label");
      label.append(el("span", "", game.card.prize_label), el("span", `rarity-tag r-${ev.prize.rarity}`, ev.prize.rarity));
      b.append(label, el("h1", "name", ev.prize.name), el("p", "line", ev.text));
      const prompt = promptLine(game.card.steal_prompt, game.commands.steal);
      const t = timerBar(ev);
      b.append(prompt, t.timer);
      requestAnimationFrame(t.start);
    }
  }

  function updateJoin(c, ev, game) {
    if (ev.kind !== "heist") return;
    names(c.crewRow, ev.crew_recent, ev.crew_count, crewWord(game, ev.crew_count));
    c.odds.replaceChildren();
    if (ev.crew_count) {
      c.odds.append(el("span", "dim", "Odds "), el("b", "", `${Math.round(ev.chance * 100)}%`),
        el("span", "dim", ` · ${game.card.odds_hint}`));
    } else {
      c.odds.append(el("span", "dim", game.card.nobody_yet));
    }
  }

  function drawRunning(c, ev, game) {
    const b = c.body;
    b.replaceChildren();
    b.append(el("h1", "name small", ev.target.name));
    const list = el("ol", "beats");
    ev.beats.forEach((text, i) => {
      const li = el("li", "", text);
      li.style.animationDelay = `${i * (ev.beat_s || 3.5)}s`;
      list.append(li);
    });
    const sneak = el("div", "sneak");
    for (let i = 0; i < 3; i++) sneak.append(el("i"));
    const crewRow = el("div", "crew");
    names(crewRow, ev.crew_recent, ev.crew_count, crewWord(game, ev.crew_count));
    b.append(list, sneak, crewRow);
  }

  function drawDone(c, ev, game) {
    const b = c.body;
    b.replaceChildren();
    c.root.classList.add(`o-${ev.outcome}`);
    const stamp = el("div", "stamp", game.card[`stamp_${ev.outcome}`] || ev.outcome);
    const title = ev.kind === "heist" ? ev.target.name : ev.prize.name;
    b.append(stamp, el("h1", "name small", title), el("p", "result", ev.result));
    if (ev.outcome === "fail") {
      const crewRow = el("div", "crew");
      names(crewRow, ev.crew_recent, ev.crew_count, `${crewWord(game, ev.crew_count)} ${game.card.caught}`);
      b.append(crewRow);
    }
    if (ev.payouts.length) {
      const list = el("ul", "payouts");
      ev.payouts.forEach((p) => list.append(lootLine(p)));
      b.append(list);
      const more = ev.payout_count - ev.payouts.length;
      if (more > 0) b.append(el("div", "more", `+${more} more ${crewWord(game, more)} ${game.card.got_loot}`));
    }
  }

  function leave() {
    if (!card) return;
    const old = card.root;
    old.classList.add("leaving");
    old.addEventListener("animationend", () => old.remove(), { once: true });
    setTimeout(() => old.remove(), 1000);
    card = null;
  }

  function show(game) {
    if (!game || !game.enabled) {
      leave();
      hint.classList.add("show");
      hint.textContent = "The chat game is off. Turn it on in config.toml under [game], then restart.";
      return;
    }
    applyTheme(game.card);
    const ev = game.event;
    if (!ev) {
      leave();
      hint.classList.add("show");
      hint.textContent = `${game.card.title}: waiting for the next one (or use ${game.card.dock_heist} / ${game.card.dock_steal} in the dock)`;
      return;
    }
    hint.classList.remove("show");
    if (!card || card.id !== ev.id) card = newCard(game, ev);
    if (card.phase !== ev.phase) {
      card.phase = ev.phase;
      card.root.dataset.phase = ev.phase;
      if (ev.phase === "join") drawJoin(card, ev, game);
      else if (ev.phase === "running") drawRunning(card, ev, game);
      else drawDone(card, ev, game);
    }
    if (ev.phase === "join") updateJoin(card, ev, game);
  }

  function toast(text) {
    const t = el("div", "toast", text);
    toasts.append(t);
    while (toasts.children.length > 3) toasts.firstChild.remove();
    setTimeout(() => t.remove(), 6200);
  }

  function handle(msg) {
    if (msg.op === "hello" || msg.op === "resync" || msg.op === "game") show(msg.game);
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
