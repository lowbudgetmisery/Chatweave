"""Anonymous, read-only Twitch chat over the IRC websocket (no login, no mod rights)."""
from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field

import aiohttp

from .hub import Hub
from .models import ChatItem, Kind, emote_frag, text_frag
from .recorder import Recorder
from .seventv import SevenTV

log = logging.getLogger("chatmerge.twitch")

IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
EMOTE_URL = "https://static-cdn.jtvnw.net/emoticons/v2/{id}/default/dark/2.0"
SEVENTV_REFRESH_S = 15 * 60

_TAG_ESCAPES = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}

# USERNOTICE msg-id values we surface, mapped to overlay kinds.
NOTICE_KINDS: dict[str, Kind] = {
    "sub": "sub",
    "resub": "sub",
    "subgift": "gift",
    "submysterygift": "gift",
    "giftpaidupgrade": "sub",
    "anongiftpaidupgrade": "sub",
    "primepaidupgrade": "sub",
    "raid": "raid",
    "announcement": "announcement",  # seen live Oct 2026; not in Twitch's documented msg-id list
    "viewermilestone": "milestone",
}
ANNOUNCEMENT_COLORS = frozenset({"blue", "green", "orange", "purple", "primary"})


@dataclass(slots=True)
class IrcMessage:
    tags: dict[str, str] = field(default_factory=dict)
    prefix: str = ""
    command: str = ""
    params: list[str] = field(default_factory=list)

    @property
    def trailing(self) -> str:
        return self.params[-1] if self.params else ""


def _unescape_tag(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(_TAG_ESCAPES.get(value[i + 1], value[i + 1]))
            i += 2
            continue
        if ch == "\\":
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_line(line: str) -> IrcMessage:
    msg = IrcMessage()
    rest = line
    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for pair in raw_tags.split(";"):
            key, _, value = pair.partition("=")
            msg.tags[key] = _unescape_tag(value)
    if rest.startswith(":"):
        msg.prefix, _, rest = rest[1:].partition(" ")
    if " :" in rest:
        head, _, trailing = rest.partition(" :")
        parts = head.split()
        parts.append(trailing)
    else:
        parts = rest.split()
    if parts:
        msg.command = parts[0]
        msg.params = parts[1:]
    return msg


def split_emotes(text: str, emotes_tag: str) -> list[dict[str, str]]:
    """Turn Twitch's emotes tag (id:start-end,start-end/...) into text/emote fragments."""
    if not emotes_tag:
        return [text_frag(text)] if text else []
    spans: list[tuple[int, int, str]] = []
    for group in emotes_tag.split("/"):
        emote_id, _, ranges = group.partition(":")
        for rng in ranges.split(","):
            start_s, _, end_s = rng.partition("-")
            if start_s.isdigit() and end_s.isdigit():
                spans.append((int(start_s), int(end_s), emote_id))
    spans.sort()
    chars = list(text)  # Twitch indexes by code point
    frags: list[dict[str, str]] = []
    cursor = 0
    for start, end, emote_id in spans:
        if start < cursor or end >= len(chars):
            continue
        if start > cursor:
            frags.append(text_frag("".join(chars[cursor:start])))
        frags.append(emote_frag("".join(chars[start : end + 1]), EMOTE_URL.format(id=emote_id)))
        cursor = end + 1
    if cursor < len(chars):
        frags.append(text_frag("".join(chars[cursor:])))
    return frags


def roles_from_badges(badges: str) -> list[str]:
    names = {b.partition("/")[0] for b in badges.split(",") if b}
    roles: list[str] = []
    for badge, role in (
        ("broadcaster", "broadcaster"),
        ("moderator", "mod"),
        ("vip", "vip"),
        ("subscriber", "member"),
        ("founder", "member"),
    ):
        if badge in names and role not in roles:
            roles.append(role)
    return roles


REPLY_PREVIEW_CHARS = 80


def shared_from_partner(tags: dict[str, str]) -> bool:
    """Shared Chat: the line started in another channel's chat (source room differs from this room)."""
    source, room = tags.get("source-room-id"), tags.get("room-id")
    return bool(source and room and source != room)


def apply_twitch_extras(item: ChatItem, tags: dict[str, str]) -> None:
    """Reply context, first-time chatter, and Shared Chat origin. All optional tags."""
    parent = tags.get("reply-parent-display-name") or tags.get("reply-parent-user-login")
    if parent:
        body = tags.get("reply-parent-msg-body", "")
        if len(body) > REPLY_PREVIEW_CHARS:
            body = body[: REPLY_PREVIEW_CHARS - 1].rstrip() + "\u2026"
        item.reply_to = {"name": parent, "text": body}
    # Not in Twitch's documented tag table, but sent today; missing tag just means "not first".
    item.first_time = tags.get("first-msg") == "1"
    if shared_from_partner(tags):
        item.shared = True
        item.source_id = tags.get("source-id") or None


def describe_room_modes(modes: dict[str, str]) -> str:
    """Restrictive Twitch chat modes that are on, e.g. "sub-only, slow 30s". Empty = normal chat."""
    on: list[str] = []
    if modes.get("subs-only") == "1":
        on.append("sub-only")
    if modes.get("emote-only") == "1":
        on.append("emote-only")
    followers = modes.get("followers-only", "-1")
    if followers.lstrip("-").isdigit() and int(followers) >= 0:
        on.append("followers-only" if int(followers) == 0 else f"followers-only ({followers} min)")
    slow = modes.get("slow", "0")
    if slow.isdigit() and int(slow) > 0:
        on.append(f"slow {slow}s")
    if modes.get("r9k") == "1":
        on.append("unique-chat")
    return ", ".join(on)


def notice_type(tags: dict[str, str]) -> str:
    """USERNOTICE type; Shared Chat wraps partner events as sharedchatnotice + source-msg-id."""
    msg_id = tags.get("msg-id", "")
    if msg_id == "sharedchatnotice":
        return tags.get("source-msg-id", "")
    return msg_id


def clearchat_action(tags: dict[str, str]) -> str:
    """Twitch sends ban-duration only for timeouts; a permanent ban has none."""
    return "timeout" if tags.get("ban-duration") else "ban"


def item_from_privmsg(msg: IrcMessage) -> ChatItem:
    tags = msg.tags
    text = msg.trailing
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]
    login = msg.prefix.partition("!")[0]
    bits = tags.get("bits", "")
    item = ChatItem(
        id=tags.get("id", ""),
        platform="twitch",
        kind="bits" if bits.isdigit() and int(bits) > 0 else "chat",
        author=tags.get("display-name") or login,
        user_id=tags.get("user-id", login),
        fragments=split_emotes(text, tags.get("emotes", "")),
        roles=roles_from_badges(tags.get("badges", "")),
        color=tags.get("color") or None,
    )
    if item.kind == "bits":
        count = int(bits)
        item.headline = f"Cheered {count:,} bit{'s' if count != 1 else ''}"
        item.amount = f"{count:,}"
    apply_twitch_extras(item, tags)
    return item


def item_from_usernotice(msg: IrcMessage) -> ChatItem | None:
    tags = msg.tags
    kind = NOTICE_KINDS.get(notice_type(tags))
    if kind is None:
        return None
    login = tags.get("login", "")
    item = ChatItem(
        id=tags.get("id", ""),
        platform="twitch",
        kind=kind,
        author=tags.get("display-name") or login,
        user_id=tags.get("user-id", login),
        fragments=split_emotes(msg.trailing, tags.get("emotes", "")) if msg.params[1:] else [],
        roles=roles_from_badges(tags.get("badges", "")),
        color=tags.get("color") or None,
        headline=tags.get("system-msg") or None,
        amount=tags.get("msg-param-viewerCount") or tags.get("msg-param-mass-gift-count") or None,
    )
    if kind == "announcement":
        item.headline = "Announcement"  # Twitch sends an empty system-msg for these
        color = tags.get("msg-param-color", "").lower()
        item.accent = color if color in ANNOUNCEMENT_COLORS else "primary"
    apply_twitch_extras(item, tags)
    item.reply_to = None  # notices are never replies
    item.first_time = False
    return item


class TwitchChat:
    def __init__(
        self,
        channel: str,
        hub: Hub,
        session: aiohttp.ClientSession,
        seventv: SevenTV,
        recorder: Recorder | None = None,
    ) -> None:
        self.channel = channel.lower().lstrip("#")
        self.hub = hub
        self.session = session
        self.seventv = seventv
        self._room_id: str | None = None
        self._mystery_gift_ids: set[str] = set()
        self._modes: dict[str, str] = {}
        self.recorder = recorder

    async def run(self) -> None:
        await self.seventv.load_global()
        refresher = asyncio.create_task(self._refresh_7tv_loop())
        backoff = 1.0
        try:
            while True:
                try:
                    await self._session_once()
                    backoff = 1.0
                except (aiohttp.ClientError, TimeoutError, ConnectionError) as exc:
                    await self.hub.set_status("twitch", f"disconnected: {exc}")
                await self.hub.set_status("twitch", f"reconnecting in {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        finally:
            refresher.cancel()

    async def _refresh_7tv_loop(self) -> None:
        while True:
            await asyncio.sleep(SEVENTV_REFRESH_S)
            if self._room_id:
                await self.seventv.load_channel(self._room_id)

    async def _session_once(self) -> None:
        await self.hub.set_status("twitch", f"connecting to #{self.channel}")
        async with self.session.ws_connect(IRC_URL, heartbeat=None, timeout=aiohttp.ClientWSTimeout(ws_receive=360)) as ws:
            nick = f"justinfan{random.randint(10000, 99999)}"
            await ws.send_str("CAP REQ :twitch.tv/tags twitch.tv/commands")
            await ws.send_str(f"NICK {nick}")
            await ws.send_str(f"JOIN #{self.channel}")
            async for frame in ws:
                if frame.type == aiohttp.WSMsgType.TEXT:
                    for line in frame.data.split("\r\n"):
                        if not line:
                            continue
                        if await self.handle_line(ws, line) is False:
                            return
                elif frame.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        await self.hub.set_status("twitch", "connection closed")

    async def handle_line(self, ws: aiohttp.ClientWebSocketResponse, line: str) -> bool:
        """One raw IRC line (live or replayed). False = the server asked us to reconnect."""
        if self.recorder is not None:
            self.recorder.twitch(line)
        return await self._handle(ws, parse_line(line))

    async def _handle(self, ws: aiohttp.ClientWebSocketResponse, msg: IrcMessage) -> bool:
        cmd = msg.command
        if cmd == "PING":
            await ws.send_str(f"PONG :{msg.trailing}")
        elif cmd == "RECONNECT":
            await self.hub.set_status("twitch", "server asked to reconnect")
            return False
        elif cmd == "ROOMSTATE" and msg.params and msg.params[0].lstrip("#").lower() == self.channel:
            await self._roomstate(msg.tags)  # only our own channel, never a collab partner's
        elif cmd == "NOTICE" and "Login" in msg.trailing:
            await self.hub.set_status("twitch", f"notice: {msg.trailing}")
        elif cmd == "PRIVMSG":
            item = item_from_privmsg(msg)
            item.fragments = self.seventv.expand(item.fragments)
            await self.hub.add(item)
        elif cmd == "USERNOTICE":
            await self._handle_usernotice(msg)
        elif cmd == "CLEARMSG" and msg.tags.get("target-msg-id"):
            await self.hub.moderate("twitch", "delete", ids=[msg.tags["target-msg-id"]])
        elif cmd == "CLEARCHAT":
            target = msg.tags.get("target-user-id")
            if target:
                await self.hub.moderate("twitch", clearchat_action(msg.tags), user_id=target)
            elif len(msg.params) == 1:
                await self.hub.moderate("twitch", "clear")
        return True

    async def _roomstate(self, tags: dict[str, str]) -> None:
        """First ROOMSTATE after JOIN has every mode; later ones carry only what changed."""
        room = tags.get("room-id")
        if room and room != self._room_id:
            self._room_id = room
            self._modes = {}
            await self.hub.set_status("twitch", f"joined #{self.channel}")
            asyncio.create_task(self.seventv.load_channel(room))
        for key in ("subs-only", "emote-only", "followers-only", "slow", "r9k"):
            if key in tags:
                self._modes[key] = tags[key]
        await self.hub.set_status("twitch chat", describe_room_modes(self._modes))  # empty = hidden

    async def _handle_usernotice(self, msg: IrcMessage) -> None:
        msg_id = notice_type(msg.tags)
        gift_batch = msg.tags.get("msg-param-community-gift-id")
        if msg_id == "submysterygift" and gift_batch:
            self._mystery_gift_ids.add(gift_batch)
            if len(self._mystery_gift_ids) > 200:
                self._mystery_gift_ids.pop()
        elif msg_id == "subgift" and gift_batch in self._mystery_gift_ids:
            return  # individual recipients of a mass gift: the summary line already showed
        item = item_from_usernotice(msg)
        if item is not None:
            item.fragments = self.seventv.expand(item.fragments)
            await self.hub.add(item)
