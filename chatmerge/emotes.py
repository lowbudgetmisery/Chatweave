"""Third-party Twitch emotes: 7TV, BetterTTV and FrankerFaceZ (global and channel sets).

Also the extras those add-ons use to change how emotes look:
  zero-width   7TV zero-width emotes, BTTV's fixed overlay list (cvMask, TopHat...) and
               visible FFZ modifier emotes stack on top of the emote before them.
  BTTV         prefix modifiers before an emote: w! h! v! z! c! l! r! p! s!
               suffix modifiers after one: ffzW ffzX ffzY ffzCursed
  FFZ          effect emotes (modifier_flags) after an emote, including supporter-only
               sets that only work for the people FFZ lists.
The effects become an "fx" list on the emote fragment; overlay.js draws them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from .models import emote_frag, text_frag

log = logging.getLogger("chatmerge.emotes")

SEVENTV_GLOBAL = "https://7tv.io/v3/emote-sets/global"
SEVENTV_USER = "https://7tv.io/v3/users/twitch/{twitch_id}"
BTTV_GLOBAL = "https://api.betterttv.net/3/cached/emotes/global"
BTTV_USER = "https://api.betterttv.net/3/cached/users/twitch/{twitch_id}"
BTTV_CDN = "https://cdn.betterttv.net/emote/{id}/2x.webp"
FFZ_GLOBAL = "https://api.frankerfacez.com/v1/set/global"
FFZ_ROOM = "https://api.frankerfacez.com/v1/room/id/{twitch_id}"

# Image hosts we accept from the APIs (overlay.js has the same list).
IMAGE_HOSTS = ("https://cdn.7tv.app/", "https://cdn.betterttv.net/", "https://cdn.frankerfacez.com/")

SEVENTV_ZERO_WIDTH = 1  # active-emote flag in a 7TV emote set
# BTTV has no flag for these; BTTV and Chatterino use this fixed list.
BTTV_ZERO_WIDTH = frozenset({"SoSnowy", "IceCold", "SantaHat", "TopHat", "ReinDeer", "CandyCane", "cvMask", "cvHazmat"})
# BTTV's emote modifiers (from BetterTTV's own chat module). Prefixes go before the emote.
BTTV_PREFIX = {"w!": "wide", "h!": "flip-h", "v!": "flip-v", "z!": "zero-space", "c!": "cursed",
               "l!": "rotate-left", "r!": "rotate-right", "p!": "party", "s!": "shake"}
# FFZ modifier_flags bits (FrankerFaceZ src/modules/chat/emotes.js, in declaration order).
FFZ_HIDDEN = 1
FFZ_FLAGS = {2: "flip-h", 4: "flip-v", 8: "wide", 128: "spin", 2048: "rainbow", 4096: "hyper-red",
             8192: "hyper-shake", 16384: "cursed", 32768: "jam", 65536: "bounce", 131072: "zero-space"}
# Suffix words BTTV understands even when FFZ data isn't loaded.
BTTV_SUFFIX = {"ffzW": "wide", "ffzX": "flip-h", "ffzY": "flip-v", "ffzCursed": "cursed"}

_TOKEN_SPLIT = re.compile(r"(\s+)")


@dataclass(frozen=True, slots=True)
class Emote:
    name: str
    url: str
    provider: str                      # 7tv / bttv / ffz
    zero_width: bool = False
    effects: tuple[str, ...] = ()      # FFZ modifier emotes: what they do to the emote before them
    hidden: bool = False               # FFZ modifier that isn't drawn itself
    users: frozenset[str] | None = None  # FFZ supporter sets: only these logins can use it

    @property
    def modifier(self) -> bool:
        return bool(self.effects) or self.hidden


def _https(url: Any) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    url = "https:" + url if url.startswith("//") else url
    return url if url.startswith(IMAGE_HOSTS) else None


# ---- parsers (pure: tested against the real API shapes) -----------------------------
def parse_7tv_set(payload: dict[str, Any]) -> dict[str, Emote]:
    """A 7TV emote set: name (the alias used in chat) -> 2x WEBP."""
    out: dict[str, Emote] = {}
    for entry in payload.get("emotes") or []:
        name = entry.get("name")
        host = (entry.get("data") or {}).get("host") or {}
        base = _https(host.get("url"))
        if not name or not base:
            continue
        files = {f.get("name") for f in host.get("files") or []}
        file_name = "2x.webp" if "2x.webp" in files else "1x.webp"
        zero = bool((entry.get("flags") or 0) & SEVENTV_ZERO_WIDTH)
        out[name] = Emote(name, f"{base}/{file_name}", "7tv", zero_width=zero)
    return out


def parse_bttv_list(entries: list[dict[str, Any]]) -> dict[str, Emote]:
    out: dict[str, Emote] = {}
    for entry in entries or []:
        code, emote_id = entry.get("code"), entry.get("id")
        if not code or not emote_id or entry.get("modifier") or not re.fullmatch(r"[0-9a-f]{24}", str(emote_id)):
            continue  # modifier entries (w!, h!...) are handled as text modifiers, not images
        out[code] = Emote(code, BTTV_CDN.format(id=emote_id), "bttv", zero_width=code in BTTV_ZERO_WIDTH)
    return out


def parse_bttv_user(payload: dict[str, Any]) -> dict[str, Emote]:
    """A channel's BTTV emotes: its own plus the shared ones it added. Own wins on a clash."""
    shared = parse_bttv_list(payload.get("sharedEmotes") or [])
    return shared | parse_bttv_list(payload.get("channelEmotes") or [])


def _ffz_emote(entry: dict[str, Any], users: frozenset[str] | None) -> Emote | None:
    name = entry.get("name")
    animated = entry.get("animated") if isinstance(entry.get("animated"), dict) else None
    urls = animated or entry.get("urls") or {}
    url = _https(urls.get("2") or urls.get("1"))
    if not name or not url:
        return None
    flags = int(entry.get("modifier_flags") or 0) if entry.get("modifier") else 0
    effects = tuple(fx for bit, fx in FFZ_FLAGS.items() if flags & bit)
    hidden = bool(entry.get("modifier")) and bool(flags & FFZ_HIDDEN)
    # A modifier that's drawn (not hidden) sits on top of the emote before it, like zero-width.
    zero = bool(entry.get("modifier")) and not hidden
    return Emote(name, url, "ffz", zero_width=zero, effects=effects, hidden=hidden, users=users)


def parse_ffz_sets(payload: dict[str, Any], open_sets: list[Any] | None = None) -> dict[str, Emote]:
    """FFZ global (default_sets open to everyone, others only for the users FFZ lists) or a
    room (every set open)."""
    sets = payload.get("sets") or {}
    restricted = payload.get("users") or {}
    open_ids = {str(s) for s in (open_sets if open_sets is not None else sets.keys())}
    out: dict[str, Emote] = {}
    for set_id, body in sets.items():
        set_id = str(set_id)
        if set_id in open_ids:
            users = None
        elif set_id in restricted:
            users = frozenset(str(u).lower() for u in restricted[set_id] or [])
        else:
            continue
        for entry in (body or {}).get("emoticons") or []:
            emote = _ffz_emote(entry, users)
            if emote is not None and emote.name not in out:
                out[emote.name] = emote
    return out


def parse_ffz_global(payload: dict[str, Any]) -> dict[str, Emote]:
    return parse_ffz_sets(payload, payload.get("default_sets") or [])


def parse_ffz_room(payload: dict[str, Any]) -> dict[str, Emote]:
    return parse_ffz_sets(payload)


# ---- the provider used by the Twitch reader --------------------------------------------
@dataclass(slots=True)
class _Tables:
    global_: dict[str, dict[str, Emote]] = field(default_factory=dict)   # provider -> name -> emote
    channel: dict[str, dict[str, Emote]] = field(default_factory=dict)


class ThirdPartyEmotes:
    """Loads and applies 7TV, BTTV and FFZ emotes. Lookup order follows what viewers expect:
    channel emotes before global ones; on a name clash 7TV, then BTTV, then FFZ."""

    ORDER = ("7tv", "bttv", "ffz")

    def __init__(self, session: aiohttp.ClientSession | None) -> None:
        self._session = session
        self._t = _Tables()

    @property
    def count(self) -> int:
        return sum(len(v) for v in self._t.global_.values()) + sum(len(v) for v in self._t.channel.values())

    def counts(self) -> dict[str, int]:
        return {p: len(self._t.channel.get(p, {})) + len(self._t.global_.get(p, {})) for p in self.ORDER}

    # loading ---------------------------------------------------------------------
    async def load_global(self) -> None:
        for provider, url, parse in (("7tv", SEVENTV_GLOBAL, parse_7tv_set), ("bttv", BTTV_GLOBAL, None),
                                     ("ffz", FFZ_GLOBAL, parse_ffz_global)):
            data = await self._get_json(provider, url)
            if data is None:
                continue  # keep what we had: one add-on being down never blanks the others
            table = parse_bttv_list(data) if provider == "bttv" else parse(data)  # type: ignore[misc]
            self._t.global_[provider] = table
            log.info("%s global emotes loaded: %d", provider.upper(), len(table))

    async def load_channel(self, twitch_id: str) -> None:
        for provider, url in (("7tv", SEVENTV_USER), ("bttv", BTTV_USER), ("ffz", FFZ_ROOM)):
            data = await self._get_json(provider, url.format(twitch_id=twitch_id), missing_ok=True)
            if data is None:
                continue
            if data == {}:
                table: dict[str, Emote] = {}  # the channel doesn't use this add-on
            elif provider == "7tv":
                table = parse_7tv_set(data.get("emote_set") or {})
            elif provider == "bttv":
                table = parse_bttv_user(data)
            else:
                table = parse_ffz_room(data)
            self._t.channel[provider] = table
            log.info("%s channel emotes loaded: %d", provider.upper(), len(table))

    async def _get_json(self, provider: str, url: str, missing_ok: bool = False) -> Any:
        """Parsed JSON; {} when the channel has no account there (404); None on failure."""
        if self._session is None:
            return None
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 404 and missing_ok:
                    log.info("%s: no emotes set up for this channel", provider.upper())
                    return {}
                if resp.status != 200:
                    log.error("%s request failed: HTTP %s for %s", provider.upper(), resp.status, url)
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            log.error("%s request error for %s: %s", provider.upper(), url, exc)
            return None

    # applying ----------------------------------------------------------------------
    def lookup(self, word: str, login: str = "") -> Emote | None:
        for scope in (self._t.channel, self._t.global_):
            for provider in self.ORDER:
                emote = scope.get(provider, {}).get(word)
                if emote is not None and (emote.users is None or login.lower() in emote.users):
                    return emote
        return None

    def expand(self, fragments: list[dict[str, Any]], login: str = "") -> list[dict[str, Any]]:
        """Swap emote words for images, stack zero-width emotes, and apply BTTV/FFZ modifiers.
        Twitch's own emotes (already image fragments) can be modified and stacked on too."""
        tokens: list[dict[str, Any]] = []  # text runs, spaces and emotes, in order
        for frag in fragments:
            if frag["t"] != "text":
                tokens.append(dict(frag))
                continue
            for part in _TOKEN_SPLIT.split(frag["v"]):
                if part:
                    tokens.append(text_frag(part))

        out: list[dict[str, Any]] = []
        pending: list[str] = []         # prefix effects waiting for the next emote
        pending_text: list[dict[str, Any]] = []  # the prefix words themselves, if no emote follows
        for tok in tokens:
            if tok["t"] == "text" and tok["v"].isspace():
                (pending_text if pending else out).append(tok)
                continue
            word = tok.get("v", "") if tok["t"] == "text" else None
            if word in BTTV_PREFIX:
                pending.append(BTTV_PREFIX[word])
                pending_text.append(tok)
                continue
            emote = self.lookup(word, login) if word else None
            if word is not None and emote is None and word in BTTV_SUFFIX:
                emote = Emote(word, "", "bttv", effects=(BTTV_SUFFIX[word],), hidden=True)
            if emote is not None and emote.modifier:
                target = _last_emote(out)
                if target is not None:
                    _add_fx(target, emote.effects)
                    if not emote.hidden:
                        out.append(_emote_frag(emote, zero=True))
                    _drop_trailing_space(out, target)
                    continue
                if emote.hidden:
                    emote = None  # nothing to modify: leave the word as text
            if tok["t"] == "emote" or emote is not None:
                frag = tok if tok["t"] == "emote" else _emote_frag(emote)  # type: ignore[arg-type]
                if pending:
                    _add_fx(frag, tuple(pending))
                    pending, pending_text = [], []
                if "zero-space" in frag.get("fx", ()):
                    _drop_trailing_space(out, None)  # z!: no gap before this emote
                if frag.get("zw") and _last_emote(out) is not None:
                    _drop_trailing_space(out, None)
                elif frag.get("zw"):
                    frag.pop("zw")  # nothing to sit on: show it as a normal emote
                out.append(frag)
                continue
            out.extend(pending_text)  # modifiers with no emote after them stay as typed
            pending, pending_text = [], []
            out.append(tok)
        out.extend(pending_text)
        return _merge_text(out)


def _emote_frag(emote: Emote, zero: bool | None = None) -> dict[str, Any]:
    frag: dict[str, Any] = emote_frag(emote.name, emote.url)
    if zero if zero is not None else emote.zero_width:
        frag["zw"] = True
    return frag


def _last_emote(out: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The base emote right before this point, ignoring spaces and any zero-width emotes
    stacked on it (modifiers change the base, and new overlays join the same stack)."""
    for frag in reversed(out):
        if frag["t"] == "text" and frag["v"].isspace():
            continue
        if frag["t"] != "emote":
            return None
        if frag.get("zw"):
            continue
        return frag
    return None


def _add_fx(frag: dict[str, Any], effects: tuple[str, ...]) -> None:
    if effects:
        frag["fx"] = list(dict.fromkeys([*frag.get("fx", []), *effects]))


def _drop_trailing_space(out: list[dict[str, Any]], target: dict[str, Any] | None) -> None:
    while out and out[-1] is not target and out[-1]["t"] == "text" and out[-1]["v"].isspace():
        out.pop()


def _merge_text(frags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for frag in frags:
        if frag["t"] == "text" and merged and merged[-1]["t"] == "text":
            merged[-1] = text_frag(merged[-1]["v"] + frag["v"])
        else:
            merged.append(frag)
    return merged
