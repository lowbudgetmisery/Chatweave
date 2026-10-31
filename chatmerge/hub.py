"""Fan-out hub: sources push items in, overlay websockets get them out."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from collections.abc import Callable
from typing import Any

from aiohttp import web

from . import mentions
from .custom_emotes import CustomEmotes
from .models import EVENT_KINDS, ChatItem
from .moderation import ACTIONS, BlockFilter, ModerationSettings

log = logging.getLogger("chatmerge.hub")

PLATFORMS = ("twitch", "youtube", "demo")
Predicate = Callable[[dict[str, Any]], bool]


class Hub:
    def __init__(
        self,
        backlog: int = 50,
        mention_pattern: re.Pattern[str] | None = None,
        hide_users: list[str] | None = None,
        custom_emotes: CustomEmotes | None = None,
        moderation: ModerationSettings | None = None,
    ) -> None:
        self._mention_pattern = mention_pattern
        self._hidden = {u.strip().lstrip("@").lower() for u in (hide_users or []) if u.strip()}
        self._custom_emotes = custom_emotes
        self.moderation = moderation or ModerationSettings()
        self._blocker = BlockFilter(self.moderation)
        self._recent: deque[dict[str, Any]] = deque(maxlen=backlog)
        # Support events get their own backlog so a busy chat can't push them out
        # before the events window (re)connects.
        self._events: deque[dict[str, Any]] = deque(maxlen=20)
        self._clients: set[web.WebSocketResponse] = set()
        self._status: dict[str, str] = {}
        self._lock = asyncio.Lock()

    # ---- sources -> hub -------------------------------------------------
    async def add(self, item: ChatItem) -> None:
        if item.author.lstrip("@").lower() in self._hidden:
            return  # bots and other accounts listed in [display] hide_users
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
        self._recent.append(payload)
        if payload["event"]:
            self._events.append(payload)
        await self._broadcast({"op": "add", "item": payload})

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
        mode = self.moderation.mode_for(action)
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

    # ---- dock buttons -> hub --------------------------------------------
    async def handle_client(self, raw: str) -> None:
        """Requests from the dock: {"op":"hide","platform":..,"id":..} or {"op":"clear_overlay"}."""
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
            if platform not in PLATFORMS or not isinstance(item_id, str) or not 0 < len(item_id) <= 200:
                log.warning("ignored malformed hide request")
                return
            await self.moderate(platform, "dock_hide", ids=[item_id])
        elif op == "clear_overlay":
            await self.moderate("*", "dock_clear")
            log.info("overlay cleared from the dock")
        else:
            log.warning("ignored unknown request %r from a browser page", op)

    # ---- hub -> overlay clients -----------------------------------------
    async def attach(self, ws: web.WebSocketResponse) -> None:
        async with self._lock:
            self._clients.add(ws)
        await ws.send_str(
            json.dumps(
                {
                    "op": "hello",
                    "recent": self.recent(),
                    "recent_events": self.recent_events(),
                    "status": self.status,
                    "settings": self.moderation.client_settings(),
                }
            )
        )

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
