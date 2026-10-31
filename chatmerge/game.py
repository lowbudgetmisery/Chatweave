"""Starcatch: an overlay-only catch game. A star creature drifts in, viewers on Twitch or
YouTube type !catch, and the result shows on the /game browser source. It never posts to
chat, so it needs no bot account and costs no YouTube allowance. Off unless [game] enabled.

Original species and art (constellations drawn in the browser from each species' key).
"""
from __future__ import annotations

import asyncio
import logging
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hub import Hub, Reply
from .models import ChatItem

log = logging.getLogger("chatmerge.game")


@dataclass(frozen=True, slots=True)
class Species:
    key: str
    name: str
    rarity: str  # common / rare / legendary
    color: str


SPECIES: tuple[Species, ...] = (
    Species("glimmer_moth", "Glimmer Moth", "common", "#9fb8ff"),
    Species("pebble_star", "Pebble Star", "common", "#ffe1a0"),
    Species("comet_kit", "Comet Kit", "common", "#8fe3ff"),
    Species("moon_jelly", "Moon Jelly", "common", "#c9b8ff"),
    Species("ember_sprite", "Ember Sprite", "common", "#ffb38a"),
    Species("lantern_fox", "Lantern Fox", "rare", "#ffd27a"),
    Species("aurora_koi", "Aurora Koi", "rare", "#7fe3b0"),
    Species("halo_crab", "Halo Crab", "rare", "#ff9ec7"),
    Species("prism_wisp", "Prism Wisp", "rare", "#b4f0ff"),
    Species("eclipse_owl", "Eclipse Owl", "legendary", "#ff6b6b"),
    Species("nova_whale", "Nova Whale", "legendary", "#ffe9a8"),
)
RARITY_WEIGHT = {"common": 70, "rare": 25, "legendary": 5}
CATCH_CHANCE = {"common": 0.75, "rare": 0.45, "legendary": 0.2}
RESULT_SHOWN_S = 10.0
DEX_COOLDOWN_S = 30.0


@dataclass(slots=True)
class GameConfig:
    enabled: bool = False
    catch_command: str = "!catch"
    dex_command: str = "!stars"
    spawn_min_s: float = 420.0  # 7 minutes
    spawn_max_s: float = 900.0  # 15 minutes
    window_s: float = 60.0      # how long !catch works after a spawn

    def validate(self) -> list[str]:
        problems = []
        for name in ("catch_command", "dex_command"):
            value = getattr(self, name)
            if not (value.startswith("!") and 1 < len(value) <= 25 and " " not in value):
                problems.append(f'[game] {name} must look like "!word" (got {value!r})')
        if self.catch_command.lower() == self.dex_command.lower():
            problems.append("[game] catch_command and dex_command must be different")
        if self.spawn_min_s < 60:
            problems.append(f"[game] spawn_min_s must be at least 60 (got {self.spawn_min_s:g})")
        if self.spawn_max_s < self.spawn_min_s:
            problems.append("[game] spawn_max_s must be at least spawn_min_s")
        if not 15 <= self.window_s <= 300:
            problems.append(f"[game] window_s must be between 15 and 300 (got {self.window_s:g})")
        return problems


class StarDB:
    """data/starcatch.db: one row per catch. Small, local, survives restarts."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS catches ("
            " id INTEGER PRIMARY KEY, platform TEXT NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL,"
            " species TEXT NOT NULL, rarity TEXT NOT NULL, spawn_id TEXT NOT NULL, ts REAL NOT NULL)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS catches_user ON catches(platform, user_id)")
        self._conn.commit()

    def record(self, platform: str, user_id: str, name: str, species: Species, spawn_id: str) -> None:
        self._conn.execute(
            "INSERT INTO catches(platform, user_id, name, species, rarity, spawn_id, ts) VALUES (?,?,?,?,?,?,?)",
            (platform, user_id, name, species.key, species.rarity, spawn_id, time.time()),
        )
        self._conn.commit()

    def summary(self, platform: str, user_id: str) -> tuple[int, int]:
        """(total catches, different species) for one viewer on one platform."""
        row = self._conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT species) FROM catches WHERE platform=? AND user_id=?",
            (platform, user_id),
        ).fetchone()
        return int(row[0]), int(row[1])

    def close(self) -> None:
        self._conn.close()


@dataclass(slots=True)
class Spawn:
    id: str
    species: Species
    ends_at: float  # time.time() when !catch stops working
    attempted: set[tuple[str, str]] = field(default_factory=set)
    catchers: list[dict[str, str]] = field(default_factory=list)
    misses: int = 0
    over: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "species": {"key": self.species.key, "name": self.species.name,
                        "rarity": self.species.rarity, "color": self.species.color},
            "ends_at": self.ends_at,
            "catchers": self.catchers,
            "misses": self.misses,
            "over": self.over,
        }


class Starcatch:
    def __init__(self, hub: Hub, cfg: GameConfig, db: StarDB, rng: random.Random | None = None) -> None:
        self.hub = hub
        self.cfg = cfg
        self.db = db
        self.rng = rng or random.Random()
        self.spawn: Spawn | None = None
        self._end_task: asyncio.Task[None] | None = None
        self._dex_last: dict[tuple[str, str], float] = {}
        hub.add_hook(self.on_item)
        hub.add_client_op("game_spawn", self._dock_spawn)

    async def start(self) -> None:
        await self.hub.set_extra("game", self._state())

    async def run(self) -> None:
        await self.start()
        while True:
            await asyncio.sleep(self.rng.uniform(self.cfg.spawn_min_s, self.cfg.spawn_max_s))
            await self.spawn_now()

    def pick(self) -> Species:
        weights = [RARITY_WEIGHT[s.rarity] / sum(1 for x in SPECIES if x.rarity == s.rarity) for s in SPECIES]
        return self.rng.choices(SPECIES, weights=weights, k=1)[0]

    async def spawn_now(self, species: Species | None = None) -> bool:
        """False if one is already out."""
        if self.spawn is not None and not self.spawn.over:
            return False
        self.spawn = Spawn(uuid.uuid4().hex[:12], species or self.pick(), time.time() + self.cfg.window_s)
        log.info("Starcatch: %s (%s) appeared", self.spawn.species.name, self.spawn.species.rarity)
        await self._publish()
        self._end_task = asyncio.create_task(self._end_after(self.spawn, self.cfg.window_s))
        return True

    async def _end_after(self, spawn: Spawn, delay: float) -> None:
        await asyncio.sleep(delay)
        await self.finish(spawn)
        await asyncio.sleep(RESULT_SHOWN_S)
        if self.spawn is spawn:
            self.spawn = None
            await self._publish()

    async def finish(self, spawn: Spawn) -> None:
        if spawn.over:
            return
        spawn.over = True
        log.info("Starcatch: %s caught by %d, %d missed", spawn.species.name, len(spawn.catchers), spawn.misses)
        await self._publish()

    async def on_item(self, item: ChatItem) -> None:
        if item.kind != "chat" or self.hub.is_hidden(item):
            return  # hidden users and bots can't play (their names would show on stream)
        text = next((f["v"] for f in item.fragments if f["t"] == "text" and f["v"].strip()), "")
        word = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
        if word == self.cfg.catch_command.lower():
            await self._try_catch(item)
        elif word == self.cfg.dex_command.lower():
            await self._dex(item)

    async def _try_catch(self, item: ChatItem) -> None:
        spawn = self.spawn
        if spawn is None or spawn.over or time.time() > spawn.ends_at:
            return
        who = (item.platform, item.user_id)
        if who in spawn.attempted:
            return  # one throw per viewer per star
        spawn.attempted.add(who)
        if self.rng.random() < CATCH_CHANCE[spawn.species.rarity]:
            spawn.catchers.append({"name": item.author, "platform": item.platform})
            await asyncio.to_thread(self.db.record, item.platform, item.user_id, item.author, spawn.species, spawn.id)
        else:
            spawn.misses += 1
        await self._publish()

    async def _dex(self, item: ChatItem) -> None:
        who = (item.platform, item.user_id)
        now = time.monotonic()
        if now - self._dex_last.get(who, -DEX_COOLDOWN_S) < DEX_COOLDOWN_S:
            return
        self._dex_last[who] = now
        total, kinds = await asyncio.to_thread(self.db.summary, item.platform, item.user_id)
        text = (f"{item.author}: {total} caught, {kinds} of {len(SPECIES)} kinds" if total
                else f"{item.author}: no stars yet. Type {self.cfg.catch_command} when one appears!")
        await self.hub.announce({"op": "game_toast", "text": text, "platform": item.platform})

    async def _dock_spawn(self, _: dict[str, Any], reply: Reply) -> None:
        if not await self.spawn_now():
            await reply({"op": "game_busy"})

    async def _publish(self) -> None:
        state = self._state()
        await self.hub.set_extra("game", state, broadcast={"op": "game", "game": state})

    def _state(self) -> dict[str, Any]:
        return {"enabled": True, "spawn": self.spawn.to_json() if self.spawn else None,
                "command": self.cfg.catch_command, "dex_command": self.cfg.dex_command, "window_s": self.cfg.window_s}
