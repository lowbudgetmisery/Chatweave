"""Fan-out hub: sources push items in, overlay websockets get them out."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from . import mentions
from .custom_emotes import CustomEmotes
from .filters import CHAT_BOTS, TIP_BOTS, LineFilter, audience_ok, normalize_name
from .models import EVENT_KINDS, ChatItem
from .moderation import BlockFilter
from .settings import ACTIONS, OverlaySettings, SettingsError, SettingsStore, apply_changes

log = logging.getLogger("chatmerge.hub")

PLATFORMS = ("twitch", "youtube", "demo")
Predicate = Callable[[dict[str, Any]], bool]
Reply = Callable[[dict[str, Any]], Awaitable[None]]
# Called with every chat line before any filter runs (the catch game listens here).
ItemHook = Callable[[ChatItem], Awaitable[None]]
# A feature's handler for a dock request it owns (for example "game_spawn").
ClientOp = Callable[[dict[str, Any], Reply], Awaitable[None]]


async def _no_reply(_: dict[str, Any]) -> None:
    return None


class Hub:
    def __init__(
        self,
        backlog: int = 50,
        mention_pattern: re.Pattern[str] | None = None,
        custom_emotes: CustomEmotes | None = None,
        settings: OverlaySettings | None = None,
        store: SettingsStore | None = None,
    ) -> None:
        self._mention_pattern = mention_pattern
        self._custom_emotes = custom_emotes
        self._store = store
        self._settings = settings or OverlaySettings()
        self._lines = LineFilter(self._settings)
        self._blocker = BlockFilter(self._settings)
        self._hooks: list[ItemHook] = []
        self._client_ops: dict[str, ClientOp] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=backlog)
        # Support events get their own backlog so a busy chat can't push them out
        # before the events window (re)connects.
        self._events: deque[dict[str, Any]] = deque(maxlen=20)
        self._clients: set[web.WebSocketResponse] = set()
        self._status: dict[str, str] = {}
        # Extra hello/resync fields: bot names for the Settings panel, plus feature state (the game).
        self._extra: dict[str, Any] = {"bot_names": {"chat": sorted(CHAT_BOTS), "tip": sorted(TIP_BOTS)}}
        self._lock = asyncio.Lock()

    # ---- settings ---------------------------------------------------------
    @property
    def settings(self) -> OverlaySettings:
        return self._settings

    async def apply_settings(self, new: OverlaySettings, save: bool = True) -> None:
        """Swap in new settings, save them, and redraw every open page."""
        if save and self._store is not None:
            self._store.save(new)
        self._settings = new
        self._lines = LineFilter(new)
        self._blocker = BlockFilter(new)
        # Newly hidden users, bots or !commands disappear from what's already on screen too.
        self._drop(self._lines.drops_payload)
        await self._broadcast(self._snapshot("resync"))

    def add_hook(self, hook: ItemHook) -> None:
        self._hooks.append(hook)

    def add_client_op(self, op: str, handler: ClientOp) -> None:
        if op in _BUILTIN_OPS or op in self._client_ops:
            raise ValueError(f"client op {op!r} is already taken")
        self._client_ops[op] = handler

    def is_hidden(self, item: ChatItem) -> bool:
        """Hidden user, bot or tip bot under the current settings (commands don't count)."""
        return normalize_name(item.author) in self._lines.hidden_names

    async def set_extra(self, key: str, value: Any, broadcast: dict[str, Any] | None = None) -> None:
        """Feature state included in hello/resync (for example the game's current spawn)."""
        self._extra[key] = value
        if broadcast is not None:
            await self._broadcast(broadcast)

    # ---- sources -> hub -------------------------------------------------
    async def add(self, item: ChatItem) -> None:
        for hook in self._hooks:
            try:
                await hook(item)
            except Exception:  # noqa: BLE001 - a broken feature must never stop chat
                log.exception("chat hook %r failed on a %s line", hook, item.platform)
        reason = self._lines.drop_reason(item)
        if reason is not None:
            log.debug("not shown (%s): %s on %s", reason, item.author, item.platform)
            return
        if item.platform == "youtube" and self._custom_emotes is not None:
            item.fragments = self._custom_emotes.expand(item.fragments)
        if self._blocker.is_blocked(item):
            if item.kind not in EVENT_KINDS:
                log.info("blocked a %s message from %s", item.platform, item.author)
                return
            # Paid or support event: keep the headline (they paid), drop the text.
            log.info("blocked the text of a %s %s from %s", item.platform, item.kind, item.author)
            item.fragments = []
        item.mention = mentions.apply(item, self._mention_pattern)
        payload = item.to_json()
        payload["sub_ok"] = audience_ok(item)
        self._recent.append(payload)
        if payload["event"]:
            self._events.append(payload)
        await self._broadcast({"op": "add", "item": payload})

    async def announce(self, payload: dict[str, Any]) -> None:
        """Send a feature message (op set by the caller) to every open page."""
        await self._broadcast(payload)

    async def moderate(
        self,
        platform: str,
        action: str,
        ids: list[str] | None = None,
        user_id: str | None = None,
    ) -> str:
        """Apply a mod action to the overlay. platform "*" = every platform.
        Exactly one target: ids, user_id, or neither (= every line of that platform).
        Returns the mode used (remove / placeholder / keep)."""
        if action not in ACTIONS:
            raise ValueError(f"unknown moderation action {action!r}")
        mode = self._settings.mode_for(action)
        if mode == "keep":
            log.info("%s on %s: overlay left unchanged (keep)", action, platform)
            return mode
        wanted = set(ids or [])

        def match(m: dict[str, Any]) -> bool:
            if platform != "*" and m["platform"] != platform:
                return False
            if ids is not None:
                return m["id"] in wanted
            if user_id is not None:
                return m["user_id"] == user_id
            return True

        if mode == "remove":
            self._drop(match)
        else:
            self._redact(match)
        message: dict[str, Any] = {"op": "moderate", "mode": mode, "platform": platform}
        if ids is not None:
            message["ids"] = list(wanted)
        elif user_id is not None:
            message["user_id"] = user_id
        await self._broadcast(message)
        return mode

    async def set_status(self, source: str, text: str) -> None:
        if self._status.get(source) == text:
            return
        self._status[source] = text
        log.info("[%s] %s", source, text)
        await self._broadcast({"op": "status", "status": dict(self._status)})

    @property
    def status(self) -> dict[str, str]:
        return dict(self._status)

    def recent(self) -> list[dict[str, Any]]:
        return list(self._recent)

    def recent_events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def _drop(self, predicate: Predicate) -> None:
        for store in (self._recent, self._events):
            kept = [m for m in store if not predicate(m)]
            store.clear()
            store.extend(kept)

    def _redact(self, predicate: Predicate) -> None:
        for store in (self._recent, self._events):
            for m in store:
                if predicate(m):
                    m["fragments"] = []
                    m["removed"] = True

    # ---- dock -> hub ------------------------------------------------------
    async def handle_client(self, raw: str, reply: Reply = _no_reply) -> None:
        """Requests from the dock. Bad requests are logged and answered with an error;
        they never raise into the websocket loop."""
        try:
            msg = json.loads(raw)
        except ValueError:
            log.warning("ignored non-JSON message from a browser page")
            return
        if not isinstance(msg, dict):
            log.warning("ignored malformed message from a browser page")
            return
        op = msg.get("op")
        if op == "hide":
            platform, item_id = msg.get("platform"), msg.get("id")
            if platform not in PLATFORMS or not _short_text(item_id):
                log.warning("ignored malformed hide request")
                return
            await self.moderate(platform, "dock_hide", ids=[item_id])
        elif op == "hide_user":
            await self._hide_user(msg, reply)
        elif op == "clear_overlay":
            await self.moderate("*", "dock_clear")
            log.info("overlay cleared from the dock")
        elif op == "update_settings":
            await self._update_settings(msg.get("settings"), reply)
        elif op == "reset_settings":
            base = self._store.base if self._store is not None else OverlaySettings()
            if await self._save_and_reply(base, reply):
                log.info("dock settings reset to config.toml")
        elif isinstance(op, str) and op in self._client_ops:
            await self._client_ops[op](msg, reply)
        else:
            log.warning("ignored unknown request %r from a browser page", op)

    async def _hide_user(self, msg: dict[str, Any], reply: Reply) -> None:
        platform, user_id, name = msg.get("platform"), msg.get("user_id"), msg.get("name")
        if platform not in PLATFORMS or not _short_text(user_id) or not _short_text(name):
            log.warning("ignored malformed hide_user request")
            return
        if normalize_name(name) in {normalize_name(n) for n in self._settings.hide_users}:
            return
        # Saving the name drops their lines on both platforms (now and later) and redraws every page.
        # Replies use their own ops so an open Settings panel isn't told its unsaved edits were saved.
        try:
            new = apply_changes(self._settings, {"hide_users": [*self._settings.hide_users, name]})
            await self.apply_settings(new)
        except (SettingsError, OSError) as exc:
            problems = exc.problems if isinstance(exc, SettingsError) else [f"could not save settings.json: {exc}"]
            log.warning("hide user %s failed: %s", name, "; ".join(problems))
            await reply({"op": "hide_user_error", "errors": problems})
            return
        log.info("hid %s (%s) from the overlay", name, platform)

    async def _update_settings(self, changes: Any, reply: Reply) -> None:
        try:
            new = apply_changes(self._settings, changes)
        except SettingsError as exc:
            log.warning("dock settings rejected: %s", exc)
            await reply({"op": "settings_error", "errors": exc.problems})
            return
        await self._save_and_reply(new, reply)

    async def _save_and_reply(self, new: OverlaySettings, reply: Reply) -> bool:
        try:
            await self.apply_settings(new)
        except OSError as exc:
            log.error("could not save dock settings: %s", exc)
            await reply({"op": "settings_error", "errors": [f"could not save settings.json: {exc}"]})
            return False
        await reply({"op": "settings_saved"})
        return True

    # ---- hub -> overlay clients -----------------------------------------
    def _snapshot(self, op: str) -> dict[str, Any]:
        return {
            "op": op,
            "recent": self.recent(),
            "recent_events": self.recent_events(),
            "status": self.status,
            "settings": self._settings.client_settings(),
            **self._extra,
        }

    async def attach(self, ws: web.WebSocketResponse) -> None:
        async with self._lock:
            self._clients.add(ws)
        await ws.send_str(json.dumps(self._snapshot("hello")))

    async def detach(self, ws: web.WebSocketResponse) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        text = json.dumps(message)
        async with self._lock:
            clients = list(self._clients)
        dead: list[web.WebSocketResponse] = []
        for ws in clients:
            if ws.closed:
                dead.append(ws)
                continue
            try:
                await ws.send_str(text)
            except (ConnectionResetError, RuntimeError) as exc:
                log.warning("dropping overlay client: %s", exc)
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._clients.discard(ws)


_BUILTIN_OPS = frozenset({"hide", "hide_user", "clear_overlay", "update_settings", "reset_settings"})


def _short_text(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 200
