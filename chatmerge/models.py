"""Shared data model for merged chat items."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Platform = Literal["twitch", "youtube", "demo"]
Kind = Literal[
    "chat",
    "sub",
    "gift",
    "bits",
    "raid",
    "superchat",
    "supersticker",
    "member",
    "announcement",  # Twitch /announce (mods and bots): highlighted chat line, not a support event
    "milestone",     # Twitch watch streak: highlighted chat line, not a support event
]

# Kinds that render as highlighted event lines in the overlay.
EVENT_KINDS: frozenset[str] = frozenset(
    {"sub", "gift", "bits", "raid", "superchat", "supersticker", "member"}
)


def text_frag(value: str) -> dict[str, str]:
    return {"t": "text", "v": value}


def emote_frag(name: str, url: str) -> dict[str, str]:
    return {"t": "emote", "name": name, "url": url}


@dataclass(slots=True)
class ChatItem:
    """One line in the merged feed. Serialised to the overlay as JSON."""

    id: str
    platform: Platform
    kind: Kind
    author: str
    user_id: str
    fragments: list[dict[str, str]] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    color: str | None = None
    headline: str | None = None
    amount: str | None = None
    tier: int | None = None
    mention: bool = False  # line @mentions the streamer
    ts: float = field(default_factory=time.time)
    # Twitch extras (None/False on YouTube)
    reply_to: dict[str, str] | None = None  # {"name": who is being answered, "text": their message, shortened}
    first_time: bool = False                # first message ever in this channel
    shared: bool = False                    # came from a partner channel during Twitch Shared Chat
    source_id: str | None = None            # Shared Chat: the message id in the partner channel
    accent: str | None = None               # announcement color: blue, green, orange, purple or primary

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["event"] = self.kind in EVENT_KINDS
        return data
