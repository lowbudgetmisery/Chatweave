"""Which lines reach the overlay at all: known bots, !commands, hidden users, and the
sub-only audience check. Twitch and YouTube don't flag bots, so bots are matched by name."""
from __future__ import annotations

import re
from typing import Any

from .models import EVENT_KINDS, ChatItem
from .settings import OverlaySettings

# Command and timer bots. Matched against the display name, any capitalization.
CHAT_BOTS: frozenset[str] = frozenset(
    {"nightbot", "fossabot", "moobot", "wizebot", "sery_bot", "botisimo"}
)
# Bots that mostly announce tips, sound alerts and similar. Hiding them hides those announcements.
TIP_BOTS: frozenset[str] = frozenset({"streamelements", "streamlabs", "soundalerts"})

# Roles that can still talk when Twitch is in sub-only mode; the overlay mirrors that.
SUB_ROLES: frozenset[str] = frozenset({"member", "vip", "mod", "broadcaster"})

_COMMAND = re.compile(r"^\s*![A-Za-z0-9]")


def normalize_name(name: str) -> str:
    return name.strip().lstrip("@").lower()


def is_command(item: ChatItem) -> bool:
    """A chat line whose text starts with !word (like !catch or !discord)."""
    return _is_command(item.kind, item.fragments)


def _is_command(kind: str, fragments: list[dict[str, str]]) -> bool:
    if kind != "chat":
        return False
    for frag in fragments:
        if frag["t"] == "text":
            if frag["v"].strip():
                return bool(_COMMAND.match(frag["v"]))
            continue
        return False  # starts with an emote or mention
    return False


def audience_ok(item: ChatItem) -> bool:
    """True when the line may show in sub-only overlay mode."""
    return item.kind in EVENT_KINDS or bool(SUB_ROLES & set(item.roles))


class LineFilter:
    """Built from the current settings; rebuilt whenever the dock saves."""

    def __init__(self, settings: OverlaySettings) -> None:
        names = {normalize_name(n) for n in settings.hide_users if n.strip()}
        if settings.hide_known_bots:
            names |= CHAT_BOTS
        if settings.hide_tip_bots:
            names |= TIP_BOTS
        self.hidden_names = frozenset(names)
        self.hide_commands = settings.hide_commands
        self.hide_shared = settings.shared_chat == "hide"

    def drop_reason(self, item: ChatItem) -> str | None:
        """Why this line never reaches the overlay, or None to keep it."""
        return self._reason(item.author, item.kind, item.fragments, item.shared)

    def drops_payload(self, payload: dict[str, Any]) -> bool:
        """Same rules for a line already in the backlog (used when settings change)."""
        return self._reason(payload["author"], payload["kind"], payload["fragments"],
                            bool(payload.get("shared"))) is not None

    def _reason(self, author: str, kind: str, fragments: list[dict[str, str]], shared: bool) -> str | None:
        if shared and self.hide_shared:
            return "Shared Chat partner channel"
        if normalize_name(author) in self.hidden_names:
            return "hidden user or bot"
        if self.hide_commands and _is_command(kind, fragments):
            return "!command"
        return None
