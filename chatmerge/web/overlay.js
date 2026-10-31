"use strict";
// Pages:  /  = chat (all lines)   /events = support events only (bits, Super Chats, subs, raids...)
// URL options:  ?dock=1  ?max=8  ?scale=1.0  ?hide=0 (seconds before a line fades, 0 = never)
// Dock (?dock=1) adds a Hide button on each line and a Clear overlay button; both only
// change what the overlay shows and never touch Twitch or YouTube.
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
  const IMG_OK = ["https://static-cdn.jtvnw.net/", "https://cdn.7tv.app/", "/static/", "/custom/"];
  const ICONS = { sub: "✧", gift: "❖", bits: "◆", raid: "☄", superchat: "✦", supersticker: "✦", member: "✧" };
  const SVG_NS = "http://www.w3.org/2000/svg";
  // Sent by the app in "hello" (from [moderation] in config.toml).
  let settings = { removal_style: "fade", placeholder_text: "message removed" };
  let sock = null;

  // Dock only: status text plus a two-click "Clear overlay" button.
  const statusText = el("span", "status-text");
  statusEl.appendChild(statusText);
  if (dock) {
    const clearBtn = el("button", "clear-btn", "Clear overlay");
    clearBtn.type = "button";
    let armed = null;
    clearBtn.addEventListener("click", () => {
      if (!armed) {
        clearBtn.textContent = "Click again to clear";
        clearBtn.classList.add("armed");
        armed = setTimeout(() => { armed = null; clearBtn.textContent = "Clear overlay"; clearBtn.classList.remove("armed"); }, 3000);
        return;
      }
      clearTimeout(armed);
      armed = null;
      clearBtn.textContent = "Clear overlay";
      clearBtn.classList.remove("armed");
      send({ op: "clear_overlay" });
    });
    statusEl.appendChild(clearBtn);
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

  function renderFragments(target, fragments) {
    for (const f of fragments || []) {
      if (f.t === "emote" && typeof f.url === "string" && IMG_OK.some((p) => f.url.startsWith(p))) {
        const img = el("img", "emote");
        img.src = f.url;
        img.alt = f.name || "";
        img.title = f.name || "";
        img.decoding = "async";
        img.addEventListener("error", () => img.replaceWith(document.createTextNode(f.name || "")), { once: true });
        target.appendChild(img);
      } else if (f.t === "emote") {
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
    const hot = item.event && (item.kind === "raid" || (item.tier || 0) >= 4);

    const removed = !!item.removed;
    const root = el("article", ["msg", `p-${item.platform}`, top ? `r-${top}` : "",
      !removed && item.event ? `event k-${item.kind}` : "", !removed && hot ? "hot" : "",
      !removed && item.mention && !item.event ? "mention" : "", !removed && item.event && !hasBody ? "nobody" : "",
      removed ? "redacted" : "", instant ? "instant" : ""].filter(Boolean).join(" "));
    root.dataset.platform = item.platform;
    root.dataset.id = item.id;
    root.dataset.user = item.user_id;

    const head = el("div", "head");
    const plat = el("span", "plat");
    plat.appendChild(platformIcon(item.platform));
    head.append(plat, el("span", "spark", "✦"), el("span", "name", item.author || ""));
    if (!removed && item.amount && (item.kind === "superchat" || item.kind === "supersticker" || item.kind === "bits")) {
      head.appendChild(el("span", "amt", item.amount));
    }

    if (removed) {
      root.append(head, placeholderBubble());
      return root;
    }
    if (dock) {
      // Hide from the on-stream overlay only. Doesn't touch Twitch or YouTube.
      const hideBtn = el("button", "hide-btn", "Hide");
      hideBtn.type = "button";
      hideBtn.title = "Hide this message from the stream overlay";
      hideBtn.addEventListener("click", () => send({ op: "hide", platform: item.platform, id: item.id }));
      head.appendChild(hideBtn);
    }
    const bubble = el("div", "bubble");
    if (item.event && item.headline) {
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
    if (eventsMode && !item.event) return;
    const node = build(item, instant);
    feed.appendChild(node);
    if (hideAfter > 0) setTimeout(() => leave(node), hideAfter * 1000);
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
      if (ids && !ids.has(node.dataset.id)) continue;
      if (msg.user_id !== undefined && node.dataset.user !== msg.user_id) continue;
      if (msg.mode === "placeholder") redact(node); else removeNode(node);
    }
  }

  function showStatus(status) {
    const parts = Object.entries(status || {}).map(([k, v]) => `${k}: ${v}`);
    statusText.textContent = parts.join("   |   ");
  }

  function handle(msg) {
    switch (msg.op) {
      case "hello":
        if (msg.settings) settings = { ...settings, ...msg.settings };
        feed.replaceChildren();
        for (const item of ((eventsMode ? msg.recent_events : msg.recent) || []).slice(-max)) add(item, true);
        showStatus(msg.status);
        break;
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
