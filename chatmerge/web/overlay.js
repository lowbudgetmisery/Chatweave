"use strict";
// Pages:  /  = chat (all lines)   /events = support events only (bits, Super Chats, subs, raids...)
// URL options:  ?dock=1  ?max=8  ?scale=1.0  ?hide=0 (seconds before a line fades, 0 = never)
// Dock (?dock=1) adds Hide and Hide user buttons on each line, a Clear overlay button and a
// Settings panel. All of them only change what the overlay shows; never Twitch or YouTube.
(() => {
  const params = new URLSearchParams(location.search);
  const dock = params.get("dock") === "1";
  const eventsMode = location.pathname.replace(/\/+$/, "") === "/events";
  const max = clampInt(params.get("max"), dock ? 60 : eventsMode ? 5 : 8, 1, 200);
  const hideAfter = clampInt(params.get("hide"), 0, 0, 3600);
  const scale = Number.parseFloat(params.get("scale") || (eventsMode ? "1.15" : "1"));
  if (Number.isFinite(scale) && scale > 0.3 && scale < 4) {
    document.documentElement.style.setProperty("--s", String(scale));
  }
  if (dock) document.body.classList.add("dock");
  if (eventsMode) document.body.classList.add("events-mode");
  // Outside OBS (a normal browser tab) the transparent page would look like a blank white
  // screen, so show a dark preview background. OBS's browser defines window.obsstudio.
  // Force either way with ?bg=1 (preview) or ?bg=0 (transparent).
  const bgParam = params.get("bg");
  const inOBS = typeof window.obsstudio !== "undefined";
  if (!dock && (bgParam === "1" || (bgParam !== "0" && !inOBS))) document.body.classList.add("preview");

  const feed = document.getElementById("feed");
  const statusEl = document.getElementById("status");
  const IMG_OK = ["https://static-cdn.jtvnw.net/", "https://cdn.7tv.app/", "https://cdn.betterttv.net/",
    "https://cdn.frankerfacez.com/", "/static/", "/custom/"];
  // Emote effects from BTTV modifiers (w! h! v! z! c! l! r! p! s!) and FFZ effect emotes.
  const FX_FILTERS = { cursed: "grayscale(1) brightness(0.7) contrast(2.5)",
    "hyper-red": "brightness(0.2) sepia(1) brightness(2.2) contrast(3) saturate(8)" };
  // Each of these gets its own wrapper, so a spin never cancels a shake or a flip.
  const FX_WRAPS = ["rotate-left", "rotate-right", "party", "rainbow", "shake", "hyper-shake", "spin", "jam", "bounce"];
  const ICONS = { sub: "✧", gift: "❖", bits: "◆", raid: "☄", superchat: "✦", supersticker: "✦", member: "✧",
    announcement: "✺", milestone: "✧" };
  const NOTICE_KINDS = new Set(["announcement", "milestone"]); // highlighted lines that aren't support events
  const SVG_NS = "http://www.w3.org/2000/svg";
  // Sent by the app in hello/resync: config.toml defaults plus anything saved from the dock.
  let settings = { removal_style: "fade", placeholder_text: "message removed", overlay_audience: "everyone" };
  let botNames = { chat: [], tip: [] };
  let sock = null;

  // Dock only: status text, Settings and a two-click "Clear overlay" button.
  const statusText = el("span", "status-text");
  const modeBox = el("span", "modes"); // chat modes (sub-only, members-only...) as chips that never get cut off
  statusEl.append(statusText, modeBox);
  let panel = null;
  let gameBtns = [];
  if (dock) {
    const settingsBtn = el("button", "settings-btn", "Settings");
    settingsBtn.type = "button";
    settingsBtn.addEventListener("click", () => panel.toggle());
    const clearBtn = el("button", "clear-btn", "Clear overlay");
    clearBtn.type = "button";
    armButton(clearBtn, "Clear overlay", "Click again to clear", () => send({ op: "clear_overlay" }));
    // Gliese Heist: only shown when [game] enabled = true.
    for (const [which, label] of [["heist", "Start heist"], ["steal", "Quick steal"]]) {
      const btn = el("button", "game-btn", label);
      btn.type = "button";
      btn.hidden = true;
      btn.dataset.which = which;
      btn.dataset.label = label;
      btn.addEventListener("click", () => send({ op: `game_${which}` }));
      gameBtns.push(btn);
    }
    statusEl.append(...gameBtns, settingsBtn, clearBtn);
  }

  // Two-click confirm: first click arms (red, new label) for 3 s, second click runs.
  function armButton(btn, idleText, armedText, onConfirm) {
    let armed = null;
    const disarm = () => { clearTimeout(armed); armed = null; btn.textContent = idleText; btn.classList.remove("armed"); };
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (!armed) {
        btn.textContent = armedText;
        btn.classList.add("armed");
        armed = setTimeout(disarm, 3000);
        return;
      }
      disarm();
      onConfirm();
    });
  }

  function shownOnStream(item) {
    return settings.overlay_audience !== "subs" || item.sub_ok !== false;
  }

  function send(obj) {
    if (sock && sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify(obj));
    else console.warn("not connected; request not sent", obj);
  }

  function clampInt(raw, fallback, lo, hi) {
    const n = Number.parseInt(raw ?? "", 10);
    return Number.isFinite(n) ? Math.min(hi, Math.max(lo, n)) : fallback;
  }

  function placeholderBubble() {
    const bubble = el("div", "bubble");
    const body = el("div", "body");
    body.append(el("span", "chev", "\u00BB"), el("span", "text removed-text", settings.placeholder_text));
    bubble.appendChild(body);
    return bubble;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function platformIcon(platform) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("fill", "#fff");
    // twitch: speech bubble, youtube: play triangle, demo: star
    const d = {
      twitch: "M4 4h16v11h-6l-4 4v-4H4z",
      youtube: "M8 5.5v13l11-6.5z",
      demo: "M12 2l2.6 7.4L22 12l-7.4 2.6L12 22l-2.6-7.4L2 12l7.4-2.6z",
    }[platform] || "M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16z";
    path.setAttribute("d", d);
    svg.appendChild(path);
    return svg;
  }

  function emoteNode(f) {
    const img = el("img", "emote");
    img.src = f.url;
    img.alt = f.name || "";
    img.title = f.name || "";
    img.decoding = "async";
    img.addEventListener("error", () => img.replaceWith(document.createTextNode(f.name || "")), { once: true });
    const fx = Array.isArray(f.fx) ? f.fx : [];
    if (!fx.length) return img;
    if (fx.includes("wide")) img.classList.add("fx-wide");
    const sx = fx.includes("flip-h") ? -1 : 1;
    const sy = fx.includes("flip-v") ? -1 : 1;
    if (sx < 0 || sy < 0) img.style.scale = `${sx} ${sy}`;
    const filters = Object.keys(FX_FILTERS).filter((k) => fx.includes(k)).map((k) => FX_FILTERS[k]);
    if (filters.length) img.style.filter = filters.join(" ");
    let node = img;
    for (const name of FX_WRAPS) {
      if (!fx.includes(name)) continue;
      const wrap = el("span", `fx fx-${name}`);
      wrap.append(node);
      node = wrap;
    }
    return node;
  }

  function renderFragments(target, fragments) {
    let lastEmote = null; // the node a zero-width emote stacks on
    for (const f of fragments || []) {
      if (f.t === "emote" && typeof f.url === "string" && IMG_OK.some((p) => f.url.startsWith(p))) {
        const node = emoteNode(f);
        if (f.zw && lastEmote) {
          // zero-width (7TV, BTTV cvMask..., FFZ overlays): draw on top of the emote before it
          let stack = lastEmote;
          if (!stack.classList.contains("emote-stack")) {
            stack = el("span", "emote-stack");
            lastEmote.replaceWith(stack);
            stack.append(lastEmote);
            lastEmote = stack;
          }
          stack.append(node);
        } else {
          target.appendChild(node);
          lastEmote = node;
        }
        continue;
      }
      lastEmote = null;
      if (f.t === "emote") {
        target.appendChild(document.createTextNode(f.name || ""));
      } else if (f.t === "ytemoji") {
        target.appendChild(el("span", "yte", f.v || ""));
      } else if (f.t === "mention") {
        target.appendChild(el("span", "at", f.v || ""));
      } else {
        target.appendChild(document.createTextNode(f.v || ""));
      }
    }
  }

  function build(item, instant) {
    const roles = item.roles || [];
    const top = ["broadcaster", "mod", "vip", "member"].find((r) => roles.includes(r));
    const hasBody = (item.fragments || []).some((f) => f.t === "emote" || f.t === "mention" || f.t === "ytemoji" || (f.v || "").trim());
    // A collab partner's subs and raids show as plain lines: no gold glow that reads as hers.
    const styled = item.event && !item.shared;
    const hot = styled && (item.kind === "raid" || (item.tier || 0) >= 4);

    const removed = !!item.removed;
    const root = el("article", ["msg", `p-${item.platform}`, top ? `r-${top}` : "",
      !removed && styled ? `event k-${item.kind}` : "", !removed && hot ? "hot" : "",
      !removed && item.mention && !item.event ? "mention" : "", !removed && styled && !hasBody ? "nobody" : "",
      removed ? "redacted" : "", instant ? "instant" : "", item.shared ? "shared" : "",
      dock && item.first_time && !removed ? "first" : "",
      !removed && NOTICE_KINDS.has(item.kind) ? `notice k-${item.kind}` : "",
      !removed && item.accent ? `a-${item.accent}` : ""].filter(Boolean).join(" "));
    root.dataset.platform = item.platform;
    root.dataset.id = item.id;
    root.dataset.user = item.user_id;
    if (item.source_id) root.dataset.alt = item.source_id; // Shared Chat: partner channel's id

    const head = el("div", "head");
    const plat = el("span", "plat");
    plat.appendChild(platformIcon(item.platform));
    head.append(plat, el("span", "spark", "✦"), el("span", "name", item.author || ""));
    if (item.shared && settings.shared_chat === "label") {
      const tag = el("span", "tag collab", "collab");
      tag.title = "From the partner channel's chat (Twitch Shared Chat)";
      head.appendChild(tag);
    }
    // Twitch shows "first message" to mods only, so this stays in the dock.
    if (dock && item.first_time && !removed) head.appendChild(el("span", "tag first-tag", "first message"));
    if (!removed && item.amount && (item.kind === "superchat" || item.kind === "supersticker" || item.kind === "bits")) {
      head.appendChild(el("span", "amt", item.amount));
    }

    if (removed) {
      root.append(head, placeholderBubble());
      return root;
    }
    if (dock) {
      // Hide from the on-stream overlay only. Doesn't touch Twitch or YouTube.
      const tools = el("span", "line-tools");
      const hideBtn = el("button", "hide-btn", "Hide");
      hideBtn.type = "button";
      hideBtn.title = "Hide this message from the stream overlay";
      hideBtn.addEventListener("click", () => send({ op: "hide", platform: item.platform, id: item.id }));
      const userBtn = el("button", "hide-btn hide-user-btn", "Hide user");
      userBtn.type = "button";
      userBtn.title = "Hide everything from this name on the overlay (undo in Settings)";
      armButton(userBtn, "Hide user", "Click again", () =>
        send({ op: "hide_user", platform: item.platform, user_id: item.user_id, name: item.author }));
      tools.append(hideBtn, userBtn);
      head.appendChild(tools);
      if (!shownOnStream(item)) {
        root.classList.add("offstage");
        root.title = "Not on stream: the overlay is set to subs and members only";
      }
    }
    const bubble = el("div", "bubble");
    if (item.reply_to && settings.show_replies !== false) {
      const reply = el("div", "reply");
      reply.append(el("span", "reply-arrow", "\u21AA"), el("span", "reply-name", `@${item.reply_to.name}`));
      if (item.reply_to.text) reply.append(el("span", "reply-text", item.reply_to.text));
      bubble.appendChild(reply);
    }
    if (item.headline && (item.event || NOTICE_KINDS.has(item.kind))) {
      const ev = el("div", "evline");
      ev.append(el("span", "ico", ICONS[item.kind] || "✦"), el("span", "", item.headline));
      bubble.appendChild(ev);
    }
    if (hasBody) {
      const body = el("div", "body");
      const text = el("span", "text");
      renderFragments(text, item.fragments);
      body.append(el("span", "chev", "»"), text);
      bubble.appendChild(body);
    }
    root.append(head, bubble);
    return root;
  }

  function live() {
    return Array.from(feed.children).filter((n) => !n.classList.contains("leaving"));
  }

  function leave(node) {
    if (!node || node.classList.contains("leaving")) return;
    node.classList.add("leaving");
    node.addEventListener("animationend", () => node.remove(), { once: true });
    setTimeout(() => node.remove(), 900); // in case animations are disabled
  }

  function add(item, instant) {
    if (eventsMode && (!item.event || item.shared)) return; // partner channels' events aren't hers
    if (!dock && !shownOnStream(item)) return;
    let hideIn = hideAfter * 1000;
    if (hideAfter > 0 && instant && item.ts) {
      // Redraw (OBS refresh, settings change): lines that already faded stay gone.
      hideIn -= Date.now() - item.ts * 1000;
      if (hideIn <= 0) return;
    }
    const node = build(item, instant);
    feed.appendChild(node);
    if (hideAfter > 0) setTimeout(() => leave(node), hideIn);
    const rows = live();
    for (let i = 0; i < rows.length - max; i++) {
      if (instant) rows[i].remove(); else leave(rows[i]);
    }
  }

  function removeNode(node) {
    if (node.classList.contains("removing")) return;
    if (settings.removal_style === "instant") { node.remove(); return; }
    // Fade out and close the gap smoothly.
    node.style.height = `${node.offsetHeight}px`;
    node.classList.add("removing");
    node.addEventListener("transitionend", () => node.remove(), { once: true });
    setTimeout(() => node.remove(), 900); // in case transitions are disabled
  }

  function redact(node) {
    if (node.classList.contains("redacted")) return;
    node.classList.remove("event", "hot", "mention", "nobody");
    for (const c of Array.from(node.classList)) if (c.startsWith("k-")) node.classList.remove(c);
    node.classList.add("redacted");
    node.querySelectorAll(".amt, .hide-btn").forEach((n) => n.remove());
    const old = node.querySelector(".bubble");
    if (old) old.replaceWith(placeholderBubble());
  }

  function moderate(msg) {
    const ids = msg.ids ? new Set(msg.ids) : null;
    for (const node of Array.from(feed.children)) {
      if (node.classList.contains("leaving") || node.classList.contains("removing")) continue;
      if (msg.platform !== "*" && node.dataset.platform !== msg.platform) continue;
      if (ids && !ids.has(node.dataset.id) && !(node.dataset.alt && ids.has(node.dataset.alt))) continue;
      if (msg.user_id !== undefined && node.dataset.user !== msg.user_id) continue;
      if (msg.mode === "placeholder") redact(node); else removeNode(node);
    }
  }

  function showStatus(status) {
    const entries = Object.entries(status || {});
    modeBox.replaceChildren();
    for (const [k, v] of entries.filter(([k]) => k.endsWith(" chat"))) {
      const chip = el("span", "mode-chip", `${k.replace(" chat", "")}: ${v}`);
      chip.title = `Your real ${k} is in this mode right now`;
      modeBox.appendChild(chip);
    }
    const parts = entries.filter(([k]) => !k.endsWith(" chat")).map(([k, v]) => `${k}: ${v}`);
    statusText.textContent = parts.join("   |   ");
    statusText.title = parts.join("\n"); // full text on hover when the bar cuts it off
  }

  function handle(msg) {
    switch (msg.op) {
      case "hello":
      case "resync": // settings changed: redraw from the app's backlog with the new rules
        if (msg.settings) settings = { ...settings, ...msg.settings };
        if (msg.bot_names) botNames = msg.bot_names;
        for (const btn of gameBtns) btn.hidden = !(msg.game && msg.game.enabled);
        feed.replaceChildren();
        for (const item of ((eventsMode ? msg.recent_events : msg.recent) || []).filter((i) => dock || shownOnStream(i)).slice(-max)) add(item, true);
        showStatus(msg.status);
        if (panel) panel.refresh();
        break;
      case "settings_saved":
        if (panel) panel.saved();
        break;
      case "settings_error":
        if (panel) panel.failed(msg.errors || []);
        break;
      case "hide_user_error":
        statusText.textContent = `Hide user failed: ${(msg.errors || []).join("; ")}`;
        break;
      case "game_busy":
        for (const btn of gameBtns.filter((b) => b.dataset.which === msg.which)) {
          btn.textContent = "One is running";
          setTimeout(() => { btn.textContent = btn.dataset.label; }, 2500);
        }
        break;
      case "game":
      case "game_toast":
        break; // shown on the /game browser source, not in chat
      case "add":
        add(msg.item, false);
        break;
      case "moderate":
        moderate(msg);
        break;
      case "status":
        showStatus(msg.status);
        break;
      default:
        console.warn("unknown op", msg.op);
    }
  }

  // ---- Settings panel (dock only) -------------------------------------------
  function makePanel() {
    const MODES = [["remove", "Remove the line"], ["placeholder", "Show \u201Cmessage removed\u201D"], ["keep", "Leave it"]];
    const root = el("section", "panel");
    root.hidden = true;
    const form = el("div", "panel-body");
    const controls = {};
    let dirty = false;
    let hidden = [];
    let base = {};          // settings as last loaded into the form
    let saving = false;     // a Save from this panel is waiting for its answer

    function row(label, control, hint) {
      const wrap = el("label", "row");
      wrap.append(el("span", "row-label", label), control);
      if (hint) wrap.appendChild(el("span", "row-hint", hint));
      return wrap;
    }
    function select(key, options) {
      const s = el("select");
      for (const [value, text] of options) {
        const o = el("option", "", text);
        o.value = value;
        s.appendChild(o);
      }
      s.addEventListener("change", () => { dirty = true; });
      controls[key] = s;
      return s;
    }
    function check(key, label, hint) {
      const box = el("input");
      box.type = "checkbox";
      box.addEventListener("change", () => { dirty = true; });
      controls[key] = box;
      const wrap = el("label", "row check");
      wrap.append(box, el("span", "row-label", label));
      if (hint) wrap.appendChild(el("span", "row-hint", hint));
      return wrap;
    }
    function heading(text) { return el("h3", "", text); }

    const chatBotHint = el("span", "row-hint");
    const tipBotHint = el("span", "row-hint");
    const hiddenList = el("div", "chips");
    const addInput = el("input");
    addInput.placeholder = "name to hide";
    addInput.maxLength = 100;
    const addBtn = el("button", "small", "Add");
    addBtn.type = "button";
    addBtn.addEventListener("click", () => {
      const name = addInput.value.trim().replace(/^@/, "");
      if (name && !hidden.some((h) => h.toLowerCase() === name.toLowerCase())) { hidden.push(name); dirty = true; drawHidden(); }
      addInput.value = "";
    });
    addInput.addEventListener("keydown", (ev) => { if (ev.key === "Enter") addBtn.click(); });
    function drawHidden() {
      hiddenList.replaceChildren();
      if (!hidden.length) hiddenList.appendChild(el("span", "row-hint", "Nobody hidden. Use Hide user on a line, or add a name."));
      for (const name of hidden) {
        const chip = el("span", "chip", name);
        const x = el("button", "chip-x", "\u00D7");
        x.type = "button";
        x.title = `Show ${name} again`;
        x.addEventListener("click", () => { hidden = hidden.filter((h) => h !== name); dirty = true; drawHidden(); });
        chip.appendChild(x);
        hiddenList.appendChild(chip);
      }
    }
    const words = el("textarea");
    words.rows = 4;
    words.placeholder = "one word or phrase per line";
    words.addEventListener("input", () => { dirty = true; });
    controls.blocked_words = words;
    const placeholder = el("input");
    placeholder.maxLength = 100;
    placeholder.addEventListener("input", () => { dirty = true; });
    controls.placeholder_text = placeholder;

    const chatBots = check("hide_known_bots", "Hide chat bots");
    chatBots.appendChild(chatBotHint);
    const tipBots = check("hide_tip_bots", "Hide tip bots", "Also hides their tip announcements");
    tipBots.appendChild(tipBotHint);
    const addRow = el("div", "add-row");
    addRow.append(addInput, addBtn);

    form.append(
      heading("On-stream chat"),
      row("Shows", select("overlay_audience", [["everyone", "Everyone"], ["subs", "Subs, members, VIPs and mods only"]]),
        "Support events always show. Your dock still shows everyone (dimmed lines are off stream)."),
      row("Collab chat", select("shared_chat", [["label", "Show with a \u201Ccollab\u201D tag"], ["hide", "Hide it"], ["show", "Show, no tag"]]),
        "Twitch Shared Chat: lines from the partner channel's chat. Their subs and raids never go in your events window."),
      check("show_replies", "Show what a reply is answering", "Twitch replies get a small \u201Creplying to @name\u201D line"),
      check("hide_commands", "Hide !commands", "Lines like !catch or !discord"),
      chatBots,
      tipBots,
      heading("Hidden users"),
      hiddenList,
      addRow,
      heading("Blocked words"),
      words,
      check("block_links", "Hide messages with links"),
      check("exempt_mods", "Never block you and your mods"),
      heading("When a mod acts on Twitch or YouTube"),
      row("Deleted message", select("on_delete", MODES)),
      row("Timeout", select("on_timeout", MODES)),
      row("Ban", select("on_ban", MODES)),
      row("Twitch /clear", select("on_clear", MODES)),
      row("Dock Hide button", select("on_dock_hide", MODES.slice(0, 2))),
      row("Placeholder words", placeholder),
      row("Removing lines", select("removal_style", [["fade", "Fade out"], ["instant", "Instantly"]])),
    );

    const msgBox = el("div", "panel-msg");
    const bar = el("div", "panel-bar");
    const save = el("button", "primary", "Save");
    save.type = "button";
    function formValues() {
      const out = {};
      for (const [key, c] of Object.entries(controls)) {
        if (key === "blocked_words") out[key] = c.value.split("\n").map((w) => w.trim()).filter(Boolean);
        else if (c.type === "checkbox") out[key] = c.checked;
        else out[key] = c.value;
      }
      out.hide_users = hidden.slice();
      return out;
    }
    const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
    save.addEventListener("click", () => {
      // Only what was changed here, so a Hide user click (or another dock) since opening isn't undone.
      const values = formValues();
      const out = {};
      for (const [key, v] of Object.entries(values)) if (!same(v, base[key])) out[key] = v;
      msgBox.className = "panel-msg";
      if (!Object.keys(out).length) { msgBox.textContent = "Nothing changed."; dirty = false; return; }
      msgBox.textContent = "Saving...";
      saving = true;
      send({ op: "update_settings", settings: out });
    });
    const close = el("button", "", "Close");
    close.type = "button";
    close.addEventListener("click", () => { dirty = false; root.hidden = true; });
    const reset = el("button", "", "Reset to config.toml");
    reset.type = "button";
    armButton(reset, "Reset to config.toml", "Click again to reset", () => { saving = true; send({ op: "reset_settings" }); });
    bar.append(save, close, reset);
    root.append(el("h2", "", "Overlay settings"), form, msgBox, bar);
    document.body.appendChild(root);

    function setField(key, v) {
      if (key === "hide_users") { hidden = (v || []).slice(); drawHidden(); return; }
      const c = controls[key];
      if (!c) return;
      if (key === "blocked_words") c.value = (v || []).join("\n");
      else if (c.type === "checkbox") c.checked = !!v;
      else if (v !== undefined) c.value = v;
    }
    // onlyUntouched: settings changed somewhere else while this form has edits. Take the new
    // values for fields not edited here and keep the edits.
    function fill(onlyUntouched) {
      const current = onlyUntouched ? formValues() : null;
      for (const key of [...Object.keys(controls), "hide_users"]) {
        if (onlyUntouched && !same(current[key], base[key])) continue;
        setField(key, settings[key]);
        base[key] = JSON.parse(JSON.stringify(settings[key] ?? null));
      }
      chatBotHint.textContent = (botNames.chat || []).join(", ");
      tipBotHint.textContent = (botNames.tip || []).join(", ");
      if (!onlyUntouched) dirty = false;
    }
    return {
      toggle() { if (root.hidden) { fill(false); msgBox.textContent = ""; } root.hidden = !root.hidden; },
      refresh() { if (!root.hidden) fill(dirty); },
      saved() {
        if (!saving) return;
        saving = false;
        dirty = false;
        fill(false);
        msgBox.className = "panel-msg ok";
        msgBox.textContent = "Saved. The overlay already uses it.";
      },
      failed(errors) {
        saving = false;
        if (root.hidden) { statusText.textContent = `Settings not saved: ${errors.join("; ")}`; return; }
        msgBox.className = "panel-msg bad";
        msgBox.textContent = `Not saved: ${errors.join("; ")}`;
      },
    };
  }
  if (dock) {
    panel = makePanel();
    // The bar can wrap to two rows in a narrow dock; keep the feed and panel just below it.
    const fit = () => document.documentElement.style.setProperty("--bar-h", `${statusEl.offsetHeight}px`);
    new ResizeObserver(fit).observe(statusEl);
    fit();
  }

  function connect() {
    const ws = new WebSocket(`ws://${location.host}/ws`);
    sock = ws;
    ws.addEventListener("message", (ev) => {
      try {
        handle(JSON.parse(ev.data));
      } catch (err) {
        console.error("bad message", err, ev.data);
      }
    });
    ws.addEventListener("close", () => {
      showStatus({ overlay: "lost connection to chatmerge, retrying" });
      setTimeout(connect, 2000);
    });
  }

  connect();
})();
