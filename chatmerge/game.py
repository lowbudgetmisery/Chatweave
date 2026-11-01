"""Gliese Heist: an overlay-only chat game in Kaneko Lumi's space-thief style.

Lumi, the star Gliese 667C and the universe's greatest thief, plans heists on Earth and her
chat (the Gumis) joins the crew. Two kinds of event:
  heist   viewers type !heist during the sign-up window; more crew = better odds; on success
          every crew member gets a piece of loot for their stash.
  steal   a quick race: the first viewer to type !steal takes the item.
Events start at random times or from the dock. Results show on the /game browser source only:
nothing is posted to chat, so there's no bot account and no YouTube allowance used.
Everything the game says lives in heist.toml so it can be edited without touching code.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import sqlite3
import threading
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .filters import audience_ok
from .hub import Hub, Reply
from .models import ChatItem

log = logging.getLogger("chatmerge.game")

RARITIES = ("common", "rare", "legendary")
# Loot rarity odds (common, rare, legendary) by heist difficulty, and for the steal race.
LOOT_ODDS: dict[str, tuple[int, int, int]] = {"1": (80, 18, 2), "2": (65, 30, 5), "3": (50, 38, 12),
                                              "steal": (40, 45, 15)}
BASE_SUCCESS = {1: 0.70, 2: 0.50, 3: 0.30}
PER_MEMBER = 0.03      # each extra crew member adds 3 points of success chance
MAX_SUCCESS = 0.92     # never a sure thing
HEIST_SHARE = 0.6      # random events: 60% heists, 40% steal races
BEAT_S = 3.5           # each story line while the heist plays out
RESULT_SHOWN_S = 14.0
STASH_COOLDOWN_S = 30.0
SHOWN_NAMES = 8        # names sent to the card (the full crew can be hundreds)
SHOWN_PAYOUTS = 6
PUBLISH_EVERY_S = 0.25  # joins in a busy chat are batched into one update

PLACEHOLDERS = ("crew", "one", "streamer", "count", "target", "item", "name")
REQUIRED_LINES = ("open", "plan", "sneak", "success", "fail", "solo", "steal_open", "steal_won", "steal_nobody")
OPTIONAL_LINES = ("fail_teasing",)


# ---- content (heist.toml) ---------------------------------------------------
@dataclass(frozen=True, slots=True)
class Target:
    name: str
    difficulty: int  # 1 easy, 2 normal, 3 hard


@dataclass(frozen=True, slots=True)
class Loot:
    name: str
    rarity: str

    def to_json(self) -> dict[str, str]:
        return {"name": self.name, "rarity": self.rarity}


@dataclass(frozen=True, slots=True)
class Rank:
    at: int
    name: str


HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
EMBLEM_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                ".webp": "image/webp", ".svg": "image/svg+xml"}
EMBLEM_MAX_BYTES = 2_000_000
CARD_PLACEHOLDERS = ("crew", "one", "streamer", "command")


@dataclass(frozen=True, slots=True)
class CardStyle:
    """Everything on the /game card that isn't a story line: labels, colors, emblem.
    The defaults are Lumi's Gliese Heist look, so a heist.toml without [card] keeps it."""

    title: str = "Gliese Heist"
    subtitle: str = "from Gliese 667C"          # may be empty
    heist_tag: str = "Heist"
    steal_tag: str = "Quick steal"
    target_label: str = "Target"
    prize_label: str = "Unguarded"
    join_prompt: str = "Type {command} to join the crew"
    steal_prompt: str = "First to type {command} keeps it"
    heat: tuple[str, str, str] = ("Easy job", "Risky job", "Impossible job")
    stamp_success: str = "Got away"
    stamp_fail: str = "Busted"
    stamp_solo: str = "Solo job"
    stamp_won: str = "Swiped"
    stamp_nobody: str = "Too slow"
    nobody_yet: str = "No {crew} yet. {streamer} might go alone..."
    odds_hint: str = "more {crew}, better odds"
    caught: str = "caught"                      # "2 Gumis caught" under a failed heist
    got_loot: str = "got loot"                  # "+5 more Gumis got loot"
    item_one: str = "item"                      # !stash: "14 items stolen"
    item_many: str = "items"
    stash_verb: str = "stolen"
    stash_empty: str = "no loot yet. Type {command} when a heist starts!"
    dock_heist: str = "Start heist"
    dock_steal: str = "Quick steal"
    emblem: str = "dwarf"                       # "dwarf" (Gliese 667C), "none", or an image file
    accent: str = "#ff7a59"
    gold: str = "#ffd98a"
    alarm: str = "#ff4d5e"


CARD_TEXT_LIMITS = {"title": 40, "subtitle": 60, "join_prompt": 80, "steal_prompt": 80, "nobody_yet": 100,
                    "odds_hint": 60, "stash_empty": 100}
NEEDS_COMMAND = ("join_prompt", "steal_prompt")
CARD_COLORS = ("accent", "gold", "alarm")


def _parse_card(raw: Any, base: Path, problems: list[str]) -> tuple[CardStyle, Path | None]:
    if raw is None:
        return CardStyle(), None
    if not isinstance(raw, dict):
        problems.append("[card] must be a section")
        return CardStyle(), None
    fields = {f: getattr(CardStyle(), f) for f in CardStyle.__slots__}  # type: ignore[attr-defined]
    unknown = sorted(set(raw) - set(fields))
    if unknown:
        problems.append(f"[card] has names the game doesn't use: {', '.join(unknown)} (check the spelling)")
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in fields:
            continue
        where = f"[card] {key}"
        if key == "heat":
            if not (isinstance(value, list) and len(value) == 3 and all(isinstance(v, str) and v.strip() for v in value)):
                problems.append(f'{where} must be 3 labels in quotes, easy to hard, like ["Easy job", "Risky job", "Impossible job"]')
                continue
            values[key] = tuple(v.strip() for v in value)
        elif key in CARD_COLORS:
            if not (isinstance(value, str) and HEX_COLOR.match(value.strip())):
                problems.append(f'{where} must be a color like "#ff7a59" (got {value!r})')
                continue
            values[key] = value.strip()
        elif key == "subtitle" and value == "":
            values[key] = ""  # no subtitle
        else:
            text = _text(value, where, problems, CARD_TEXT_LIMITS.get(key, 30))
            if not text:
                continue
            try:
                text.format_map({k: "x" for k in CARD_PLACEHOLDERS})
            except KeyError as exc:
                allowed = ", ".join("{" + k + "}" for k in CARD_PLACEHOLDERS)
                problems.append(f"{where} uses {{{exc.args[0]}}}, which isn't a placeholder (use {allowed})")
                continue
            except (ValueError, IndexError, AttributeError) as exc:
                problems.append(f"{where} has a stray {{ or }} ({exc}); write {{{{ or }}}} for a literal brace")
                continue
            if key in NEEDS_COMMAND and text.count("{command}") != 1:
                problems.append(f"{where} must contain {{command}} once (that's where the command chip goes)")
                continue
            values[key] = text
    card = CardStyle(**values)
    emblem_path: Path | None = None
    if card.emblem not in ("dwarf", "none"):
        emblem_path = (base / card.emblem).resolve()
        if emblem_path.suffix.lower() not in EMBLEM_TYPES:
            problems.append(f'[card] emblem must be "dwarf", "none", or a .png, .jpg, .gif, .webp or .svg file '
                            f"(got {card.emblem!r})")
            emblem_path = None
        elif not emblem_path.is_file():
            problems.append(f'[card] emblem file not found: "{emblem_path}" (put it next to this file)')
            emblem_path = None
        elif emblem_path.stat().st_size > EMBLEM_MAX_BYTES:
            problems.append(f'[card] emblem file is too big ({emblem_path.stat().st_size:,} bytes, at most 2 MB)')
            emblem_path = None
    return card, emblem_path


@dataclass(frozen=True, slots=True)
class HeistContent:
    crew: str
    one: str
    ranks: tuple[Rank, ...]
    targets: tuple[Target, ...]
    loot: tuple[Loot, ...]
    lines: dict[str, tuple[str, ...]]
    streamer: str = "Lumi"
    card: CardStyle = field(default_factory=CardStyle)
    emblem_path: Path | None = None

    def rank_for(self, total: int) -> str:
        name = self.ranks[0].name
        for rank in self.ranks:
            if total >= rank.at:
                name = rank.name
        return name


class ContentError(ValueError):
    def __init__(self, path: Path, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f'in "{path}": ' + "; ".join(problems))


def _text(value: Any, where: str, problems: list[str], limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        shown = repr(value) if len(repr(value)) <= 60 else repr(value)[:57] + "..."
        problems.append(f"{where} must be some text in quotes (got {shown})")
        return ""
    if len(value.strip()) > limit:
        problems.append(f"{where} is too long ({len(value.strip())} characters, at most {limit})")
    return value.strip()


def _check_line(line: str, where: str, problems: list[str]) -> None:
    sample = {k: "x" for k in PLACEHOLDERS}
    try:
        line.format_map(sample)
    except KeyError as exc:
        allowed = ", ".join("{" + k + "}" for k in PLACEHOLDERS)
        problems.append(f"{where} uses {{{exc.args[0]}}}, which isn't a placeholder (use {allowed})")
    except (ValueError, IndexError, AttributeError) as exc:
        problems.append(f"{where} has a stray {{ or }} ({exc}); write {{{{ or }}}} for a literal brace")


def load_content(path: Path) -> HeistContent:
    """Read and fully check heist.toml. Raises ContentError listing every problem found."""
    if not path.is_file():
        raise ContentError(path, ["file not found (it ships next to config.toml)"])
    try:
        raw = tomllib.loads(path.read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ContentError(path, [f"not valid TOML: {exc}"]) from exc
    sections = [k for k, v in raw.items() if isinstance(v, dict) and k not in ("lines", "card")]
    if sections:
        raise ContentError(path, [f"found a [{sections[0]}] header: streamer, crew, one, ranks, targets and loot must "
                                  "sit at the top of the file, above the [card] and [lines] headers"])
    problems: list[str] = []

    # crew, one, ranks, targets and loot sit at the top of the file, before [lines]: in TOML
    # anything written under a [section] header belongs to that section.
    crew = _text(raw.get("crew"), "crew", problems, 30)
    one = _text(raw.get("one"), "one", problems, 30)
    streamer = _text(raw.get("streamer", "Lumi"), "streamer", problems, 30)
    card, emblem_path = _parse_card(raw.get("card"), path.parent, problems)

    ranks: list[Rank] = []
    for i, entry in enumerate(_table_list(raw.get("ranks"), "ranks", problems), 1):
        at = entry.get("at")
        if isinstance(at, bool) or not isinstance(at, int) or at < 0:
            problems.append(f"ranks #{i}: at must be a whole number 0 or more (got {at!r})")
            continue
        ranks.append(Rank(at, _text(entry.get("name"), f"ranks #{i} name", problems, 40)))
    if ranks:
        if ranks[0].at != 0:
            problems.append("ranks: the first rank must start at 0")
        if any(b.at <= a.at for a, b in zip(ranks, ranks[1:])):
            problems.append("ranks must be listed lowest first, each with a bigger 'at' than the one before")

    targets: list[Target] = []
    for i, entry in enumerate(_table_list(raw.get("targets"), "targets", problems), 1):
        diff = entry.get("difficulty")
        if isinstance(diff, bool) or diff not in (1, 2, 3):
            problems.append(f"targets #{i}: difficulty must be 1, 2 or 3 (got {diff!r})")
            continue
        targets.append(Target(_text(entry.get("name"), f"targets #{i} name", problems, 120), diff))

    loot: list[Loot] = []
    for i, entry in enumerate(_table_list(raw.get("loot"), "loot", problems), 1):
        rarity = entry.get("rarity")
        if rarity not in RARITIES:
            problems.append(f"loot #{i}: rarity must be common, rare or legendary (got {rarity!r})")
            continue
        loot.append(Loot(_text(entry.get("name"), f"loot #{i} name", problems, 80), rarity))
    if loot and not any(item.rarity == "common" for item in loot):
        problems.append("loot needs at least one common item")

    lines_raw = raw.get("lines", {})
    if not isinstance(lines_raw, dict):
        lines_raw = {}
        problems.append("[lines] must be a section")
    lines: dict[str, tuple[str, ...]] = {}
    for key in REQUIRED_LINES + OPTIONAL_LINES:
        value = lines_raw.get(key)
        if value is None and key in OPTIONAL_LINES:
            lines[key] = ()
            continue
        if not isinstance(value, list) or not value:
            problems.append(f"[lines] {key} must be a list with at least one line in quotes")
            continue
        kept = []
        for i, line in enumerate(value, 1):
            text = _text(line, f"[lines] {key} #{i}", problems, 160)
            if text:
                _check_line(text, f"[lines] {key} #{i}", problems)
                kept.append(text)
        lines[key] = tuple(kept)
    unknown = sorted(set(lines_raw) - set(REQUIRED_LINES + OPTIONAL_LINES))
    if unknown:
        problems.append(f"[lines] has names the game doesn't use: {', '.join(unknown)} (check the spelling)")
    stray = sorted(set(raw) - {"streamer", "crew", "one", "ranks", "targets", "loot", "card", "lines"})
    if stray:
        problems.append(f"unknown names at the top of the file: {', '.join(stray)} (check the spelling, and keep "
                        "streamer, crew, one, ranks, targets and loot above the [card] and [lines] headers)")

    if problems:
        raise ContentError(path, problems)
    return HeistContent(crew, one, tuple(ranks), tuple(targets), tuple(loot), lines,
                        streamer=streamer, card=card, emblem_path=emblem_path)


def _table_list(value: Any, where: str, problems: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        problems.append(f"{where} must be a list with at least one entry")
        return []
    if not all(isinstance(v, dict) for v in value):
        problems.append(f"{where}: every entry must look like {{ name = \"...\", ... }}")
        return []
    return value


# ---- config ([game] in config.toml) -----------------------------------------
@dataclass(slots=True)
class GameConfig:
    enabled: bool = False
    heist_command: str = "!heist"
    steal_command: str = "!steal"
    stash_command: str = "!stash"
    event_min_s: float = 420.0  # 7 minutes
    event_max_s: float = 900.0  # 15 minutes
    join_s: float = 60.0        # heist sign-up window
    steal_s: float = 30.0       # steal race window
    teasing: bool = True        # also use the [lines] fail_teasing lines
    content: HeistContent | None = None  # loaded from heist.toml when enabled

    def validate(self) -> list[str]:
        problems = []
        commands = {"heist_command": self.heist_command, "steal_command": self.steal_command,
                    "stash_command": self.stash_command}
        for name, value in commands.items():
            if not (value.startswith("!") and 1 < len(value) <= 25 and " " not in value):
                problems.append(f'[game] {name} must look like "!word" (got {value!r})')
        if len({v.lower() for v in commands.values()}) < len(commands):
            problems.append("[game] heist_command, steal_command and stash_command must all be different")
        if self.event_min_s < 60:
            problems.append(f"[game] event_min_minutes must be at least 1 (got {self.event_min_s / 60:g})")
        if self.event_max_s < self.event_min_s:
            problems.append("[game] event_max_minutes must be at least event_min_minutes")
        if not 15 <= self.join_s <= 300:
            problems.append(f"[game] heist_join_seconds must be between 15 and 300 (got {self.join_s:g})")
        if not 10 <= self.steal_s <= 120:
            problems.append(f"[game] steal_seconds must be between 10 and 120 (got {self.steal_s:g})")
        return problems


# ---- stash database -----------------------------------------------------------
class LootDB:
    """data/heist.db: one row per stolen item. Small, local, survives restarts."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()  # one statement at a time across worker threads
        # Short busy timeout: if the file is open in another program, give up fast and log it.
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=2.0)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS loot ("
            " id INTEGER PRIMARY KEY, platform TEXT NOT NULL, user_id TEXT NOT NULL, name TEXT NOT NULL,"
            " item TEXT NOT NULL, rarity TEXT NOT NULL, event_id TEXT NOT NULL, ts REAL NOT NULL)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS loot_user ON loot(platform, user_id)")
        self._conn.commit()

    def record(self, rows: list[tuple[str, str, str, Loot]], event_id: str) -> None:
        """rows: (platform, user_id, display name, loot). One transaction per event."""
        now = time.time()
        with self._lock:
            self._conn.executemany(
                "INSERT INTO loot(platform, user_id, name, item, rarity, event_id, ts) VALUES (?,?,?,?,?,?,?)",
                [(p, u, n, loot.name, loot.rarity, event_id, now) for p, u, n, loot in rows],
            )
            self._conn.commit()

    def summary(self, platform: str, user_id: str) -> tuple[int, int, int]:
        """(total items, rare, legendary) for one viewer on one platform."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*), SUM(rarity='rare'), SUM(rarity='legendary') FROM loot"
                " WHERE platform=? AND user_id=?",
                (platform, user_id),
            ).fetchone()
        return int(row[0]), int(row[1] or 0), int(row[2] or 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ---- events ---------------------------------------------------------------------
@dataclass(slots=True)
class Member:
    name: str
    platform: str
    user_id: str

    def to_json(self) -> dict[str, str]:
        return {"name": self.name, "platform": self.platform}


@dataclass(slots=True)
class Event:
    id: str
    kind: str          # heist / steal
    ends_at: float     # time.time() when sign-up (heist) or the race (steal) closes
    window_s: float
    text: str          # the opening line
    target: Target | None = None   # heist
    prize: Loot | None = None      # steal
    phase: str = "join"            # join -> running (heist only) -> done
    crew: list[Member] = field(default_factory=list)
    joined: set[tuple[str, str]] = field(default_factory=set)
    beats: list[str] = field(default_factory=list)
    outcome: str = ""              # success / fail / solo / won / nobody
    result: str = ""
    payouts: list[tuple[Member, Loot]] = field(default_factory=list)
    winner: Member | None = None
    claimed: asyncio.Event = field(default_factory=asyncio.Event)
    chance: float = 0.0            # heist success chance for the crew so far

    def to_json(self) -> dict[str, Any]:
        best = sorted(self.payouts, key=lambda p: -RARITIES.index(p[1].rarity))[:SHOWN_PAYOUTS]
        return {
            "id": self.id, "kind": self.kind, "phase": self.phase, "beat_s": BEAT_S,
            "ends_at": self.ends_at, "window_s": self.window_s, "text": self.text,
            "target": {"name": self.target.name, "difficulty": self.target.difficulty} if self.target else None,
            "prize": self.prize.to_json() if self.prize else None,
            "crew_count": len(self.crew),
            "crew_recent": [m.to_json() for m in self.crew[-SHOWN_NAMES:]],
            "chance": round(self.chance, 2),
            "beats": self.beats, "outcome": self.outcome, "result": self.result,
            "payouts": [{**m.to_json(), "loot": loot.to_json()} for m, loot in best],
            "payout_count": len(self.payouts),
            "winner": self.winner.to_json() if self.winner else None,
        }


class GlieseHeist:
    def __init__(self, hub: Hub, cfg: GameConfig, db: LootDB, content: HeistContent,
                 rng: random.Random | None = None) -> None:
        self.hub = hub
        self.cfg = cfg
        self.db = db
        self.content = content
        self.rng = rng or random.Random()
        self.event: Event | None = None
        self._flow: asyncio.Task[None] | None = None
        self._publish_pending = False
        self._stash_last: dict[tuple[str, str], float] = {}
        self._background: set[asyncio.Task[None]] = set()
        self._card = self.card_json()  # fixed for the session: heist.toml is read once at start
        hub.add_hook(self.on_item)
        hub.add_client_op("game_heist", self._dock_heist)
        hub.add_client_op("game_steal", self._dock_steal)

    # ---- lifecycle --------------------------------------------------------
    async def start(self) -> None:
        await self.hub.set_extra("game", self._state())

    async def run(self) -> None:
        await self.start()
        try:
            while True:
                await asyncio.sleep(self.rng.uniform(self.cfg.event_min_s, self.cfg.event_max_s))
                if self.rng.random() < HEIST_SHARE:
                    await self.start_heist()
                else:
                    await self.start_steal()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Cancel the running event and background work (shutdown)."""
        tasks = [t for t in [self._flow, *self._background] if t is not None and not t.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.event is not None:  # a flow cancelled before it started never cleared its event
            await self._clear(self.event)

    def busy(self) -> bool:
        return self.event is not None

    # ---- text -----------------------------------------------------------------
    def say(self, key: str, **values: Any) -> str:
        pool = self.content.lines[key]
        if key == "fail" and self.cfg.teasing:
            pool = pool + self.content.lines["fail_teasing"]
        filled = {k: "" for k in PLACEHOLDERS} | {"crew": self.content.crew, "one": self.content.one,
                                                  "streamer": self.content.streamer}
        filled.update({k: str(v) for k, v in values.items()})
        if values.get("count") == 1:
            filled["crew"] = self.content.one  # "1 Gumi", not "1 Gumis"
        text = self.rng.choice(pool).format_map(filled)
        return text[:1].upper() + text[1:]  # "{item} ..." lines start with "a ..."

    def pick_loot(self, odds_key: str) -> Loot:
        weights = LOOT_ODDS[odds_key]
        present = [(r, w) for r, w in zip(RARITIES, weights) if any(i.rarity == r for i in self.content.loot)]
        rarity = self.rng.choices([r for r, _ in present], weights=[w for _, w in present], k=1)[0]
        return self.rng.choice([i for i in self.content.loot if i.rarity == rarity])

    @staticmethod
    def chance_for(difficulty: int, crew: int) -> float:
        if crew <= 0:
            return 0.0
        return min(MAX_SUCCESS, BASE_SUCCESS[difficulty] + PER_MEMBER * (crew - 1))

    # ---- starting events ----------------------------------------------------
    async def start_heist(self, target: Target | None = None) -> bool:
        """False if an event is already running."""
        if self.busy():
            return False
        target = target or self.rng.choice(self.content.targets)
        ev = Event(uuid.uuid4().hex[:12], "heist", time.time() + self.cfg.join_s, self.cfg.join_s,
                   self.say("open", target=target.name), target=target)
        self.event = ev
        log.info("Chat game: heist on %s (difficulty %d)", target.name, target.difficulty)
        await self._publish()
        self._flow = asyncio.create_task(self._heist_flow(ev))
        return True

    async def start_steal(self, prize: Loot | None = None) -> bool:
        """False if an event is already running."""
        if self.busy():
            return False
        prize = prize or self.pick_loot("steal")
        ev = Event(uuid.uuid4().hex[:12], "steal", time.time() + self.cfg.steal_s, self.cfg.steal_s,
                   self.say("steal_open", item=prize.name), prize=prize)
        self.event = ev
        log.info("Chat game: steal race for %s (%s)", prize.name, prize.rarity)
        await self._publish()
        self._flow = asyncio.create_task(self._steal_flow(ev))
        return True

    # ---- event flows (one task per event, never on the chat path) -----------
    async def _heist_flow(self, ev: Event) -> None:
        try:
            await asyncio.sleep(max(0.0, ev.ends_at - time.time()))
            ev.phase = "running"
            if ev.crew:
                ev.beats = [self.say("plan", count=len(ev.crew)), self.say("sneak", count=len(ev.crew))]
                await self._publish()
                await asyncio.sleep(BEAT_S * len(ev.beats))
            await self._resolve_heist(ev)
            await self._save(ev)
            await asyncio.sleep(RESULT_SHOWN_S)
        finally:
            await self._clear(ev)

    async def _resolve_heist(self, ev: Event) -> None:
        assert ev.target is not None
        count = len(ev.crew)
        values = {"count": count, "target": ev.target.name}
        if count == 0:
            ev.outcome, ev.result = "solo", self.say("solo", **values)
        elif self.rng.random() < self.chance_for(ev.target.difficulty, count):
            ev.outcome, ev.result = "success", self.say("success", **values)
            ev.payouts = [(m, self.pick_loot(str(ev.target.difficulty))) for m in ev.crew]
        else:
            ev.outcome, ev.result = "fail", self.say("fail", **values)
        ev.phase = "done"
        log.info("Chat game: %s with %d crew", ev.outcome, count)
        await self._publish()

    async def _steal_flow(self, ev: Event) -> None:
        assert ev.prize is not None
        try:
            try:
                await asyncio.wait_for(ev.claimed.wait(), timeout=max(0.0, ev.ends_at - time.time()))
            except TimeoutError:
                pass
            if ev.winner is not None:
                ev.outcome = "won"
                ev.result = self.say("steal_won", name=ev.winner.name, item=ev.prize.name)
                ev.payouts = [(ev.winner, ev.prize)]
            else:
                ev.outcome, ev.result = "nobody", self.say("steal_nobody", item=ev.prize.name)
            ev.phase = "done"
            log.info("Chat game: steal race %s", ev.outcome)
            await self._publish()
            await self._save(ev)
            await asyncio.sleep(RESULT_SHOWN_S)
        finally:
            await self._clear(ev)

    async def _save(self, ev: Event) -> None:
        if not ev.payouts:
            return
        rows = [(m.platform, m.user_id, m.name, loot) for m, loot in ev.payouts]
        try:
            await asyncio.to_thread(self.db.record, rows, ev.id)
        except sqlite3.Error as exc:
            log.error('Chat game: %d stolen items could not be saved to "%s": %s', len(rows), self.db.path, exc)

    async def _clear(self, ev: Event) -> None:
        if self.event is ev:
            self.event = None
            try:
                await self._publish()
            except Exception:  # noqa: BLE001 - shutting down; pages reload state on reconnect
                log.debug("could not publish the cleared game state", exc_info=True)

    # ---- chat ---------------------------------------------------------------------
    async def on_item(self, item: ChatItem) -> None:
        """Runs before every chat line is shown, so it only checks the command and hands any
        slow work to a background task: the game can never hold up chat."""
        if item.kind != "chat" or item.shared or self.hub.is_hidden(item):
            return  # bots, hidden users and Shared Chat partner viewers can't play
        if self.hub.settings.overlay_audience == "subs" and not audience_ok(item):
            return  # the overlay is subs-only, so the game is too
        text = next((f["v"] for f in item.fragments if f["t"] == "text" and f["v"].strip()), "")
        word = text.strip().split(maxsplit=1)[0].lower() if text.strip() else ""
        if not word.startswith("!"):
            return
        ev = self.event
        open_now = ev is not None and ev.phase == "join" and time.time() <= ev.ends_at
        who = (item.platform, item.user_id)
        if word == self.cfg.heist_command.lower():
            if not open_now or ev is None or ev.kind != "heist" or who in ev.joined:
                return
            assert ev.target is not None
            ev.joined.add(who)
            ev.crew.append(Member(item.author, item.platform, item.user_id))
            ev.chance = self.chance_for(ev.target.difficulty, len(ev.crew))
            self._publish_soon()
        elif word == self.cfg.steal_command.lower():
            if not open_now or ev is None or ev.kind != "steal" or ev.winner is not None:
                return
            ev.winner = Member(item.author, item.platform, item.user_id)  # decided as the line arrives
            ev.claimed.set()
        elif word == self.cfg.stash_command.lower():
            self._spawn_task(self._stash(item))

    def _spawn_task(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._background.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("Chat game task failed: %r", task.exception())

    async def settle(self) -> None:
        """Wait for queued updates and !stash lookups (tests)."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    def _publish_soon(self) -> None:
        if self._publish_pending:
            return
        self._publish_pending = True

        async def later() -> None:
            await asyncio.sleep(PUBLISH_EVERY_S)
            self._publish_pending = False
            await self._publish()

        self._spawn_task(later())

    async def _stash(self, item: ChatItem) -> None:
        who = (item.platform, item.user_id)
        now = time.monotonic()
        if now - self._stash_last.get(who, -STASH_COOLDOWN_S) < STASH_COOLDOWN_S:
            return
        self._stash_last[who] = now
        if len(self._stash_last) > 2000:  # forget cooldowns that have run out
            self._stash_last = {k: t for k, t in self._stash_last.items() if now - t < STASH_COOLDOWN_S}
        try:
            total, rare, legendary = await asyncio.to_thread(self.db.summary, item.platform, item.user_id)
        except sqlite3.Error as exc:
            log.error('Chat game: could not read "%s" for %s: %s', self.db.path, item.author, exc)
            return
        rank = self.content.rank_for(total)
        card = self.content.card
        if total:
            extras = [f"{n} {label}" for n, label in ((rare, "rare"), (legendary, "legendary")) if n]
            text = f"{item.author} ({rank}): {total} {card.item_one if total == 1 else card.item_many} {card.stash_verb}"
            text += (", " + ", ".join(extras)) if extras else ""
        else:
            text = f"{item.author} ({rank}): {self.card_text(card.stash_empty, command=self.cfg.heist_command)}"
        await self.hub.announce({"op": "game_toast", "text": text, "platform": item.platform})

    # ---- dock -------------------------------------------------------------------------
    async def _dock_heist(self, _: dict[str, Any], reply: Reply) -> None:
        if not await self.start_heist():
            await reply({"op": "game_busy", "which": "heist"})

    async def _dock_steal(self, _: dict[str, Any], reply: Reply) -> None:
        if not await self.start_steal():
            await reply({"op": "game_busy", "which": "steal"})

    # ---- state for the pages -----------------------------------------------------------
    async def _publish(self) -> None:
        state = self._state()
        await self.hub.set_extra("game", state, broadcast={"op": "game", "game": state})

    def card_text(self, text: str, command: str = "{command}") -> str:
        """Fill {crew}, {one} and {streamer}; {command} stays for the page to draw as a chip."""
        c = self.content
        return text.format_map({"crew": c.crew, "one": c.one, "streamer": c.streamer, "command": command})

    def card_json(self) -> dict[str, Any]:
        card = self.content.card
        out: dict[str, Any] = {}
        for name in CardStyle.__slots__:  # type: ignore[attr-defined]
            value = getattr(card, name)
            out[name] = self.card_text(value) if isinstance(value, str) and name not in CARD_COLORS else value
        out["heat"] = [self.card_text(h) for h in card.heat]
        if self.content.emblem_path is not None:
            stamp = int(self.content.emblem_path.stat().st_mtime)
            out["emblem"], out["emblem_url"] = "image", f"/game/emblem?v={stamp}"
        return out

    def _state(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "event": self.event.to_json() if self.event else None,
            "commands": {"heist": self.cfg.heist_command, "steal": self.cfg.steal_command,
                         "stash": self.cfg.stash_command},
            "crew": {"name": self.content.crew, "one": self.content.one},
            "card": self._card,
        }
