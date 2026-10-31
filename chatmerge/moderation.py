"""Overlay-side moderation settings: what happens when a mod acts on Twitch or YouTube,
blocked words/links, and the dock's Hide / Clear overlay buttons. Nothing here touches
Twitch or YouTube themselves; it only changes what the overlay shows."""
from __future__ import annotations

import re
from collections.abc import Iterable

from .models import ChatItem
from .settings import ACTIONS, MODES, REMOVAL_STYLES, OverlaySettings  # noqa: F401 (re-exported)

LINK_RE = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[\w-]+\.(?:com|net|org|gg|tv|ly|io|me|xyz|co|link|live|app|shop|info|biz|ru|cn)\b(?:/\S*)?",
    re.IGNORECASE,
)

# Kept for older imports; the settings now live in settings.py.
ModerationSettings = OverlaySettings


def build_block_pattern(words: Iterable[str]) -> re.Pattern[str] | None:
    """Whole-word, case-insensitive. Entries with spaces match as phrases."""
    cleaned = sorted({w.strip().lower() for w in words if w.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in cleaned) + r")(?!\w)", re.IGNORECASE)


def message_text(item: ChatItem) -> str:
    return "".join(f.get("v", "") for f in item.fragments if f["t"] in ("text", "mention"))


class BlockFilter:
    def __init__(self, settings: OverlaySettings) -> None:
        self.pattern = build_block_pattern(settings.blocked_words)
        self.block_links = settings.block_links
        self.exempt_mods = settings.exempt_mods

    @property
    def active(self) -> bool:
        return self.pattern is not None or self.block_links

    def is_blocked(self, item: ChatItem) -> bool:
        if not self.active:
            return False
        if self.exempt_mods and ({"broadcaster", "mod"} & set(item.roles)):
            return False
        text = message_text(item)
        if self.pattern is not None and self.pattern.search(text):
            return True
        return bool(self.block_links and LINK_RE.search(text))
