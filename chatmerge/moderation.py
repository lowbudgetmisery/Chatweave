"""Overlay-side moderation settings: what happens when a mod acts on Twitch or YouTube,
blocked words/links, and the dock's Hide / Clear overlay buttons. Nothing here touches
Twitch or YouTube themselves; it only changes what the overlay shows."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from .models import ChatItem

MODES = ("remove", "placeholder", "keep")
# Platform actions and the dock's buttons. "dock_clear" always removes.
ACTIONS = ("delete", "timeout", "ban", "clear", "dock_hide", "dock_clear")
REMOVAL_STYLES = ("fade", "instant")

LINK_RE = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[\w-]+\.(?:com|net|org|gg|tv|ly|io|me|xyz|co|link|live|app|shop|info|biz|ru|cn)\b(?:/\S*)?",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ModerationSettings:
    on_delete: str = "remove"
    on_timeout: str = "remove"
    on_ban: str = "remove"
    on_clear: str = "remove"
    on_dock_hide: str = "remove"
    placeholder_text: str = "message removed"
    removal_style: str = "fade"
    blocked_words: list[str] = field(default_factory=list)
    block_links: bool = False
    exempt_mods: bool = True

    def mode_for(self, action: str) -> str:
        if action == "dock_clear":
            return "remove"
        return {
            "delete": self.on_delete,
            "timeout": self.on_timeout,
            "ban": self.on_ban,
            "clear": self.on_clear,
            "dock_hide": self.on_dock_hide,
        }[action]

    def validate(self) -> list[str]:
        """Human-readable problems; empty when valid."""
        problems = []
        for name in ("on_delete", "on_timeout", "on_ban", "on_clear"):
            value = getattr(self, name)
            if value not in MODES:
                problems.append(f'[moderation] {name} must be "remove", "placeholder" or "keep" (got {value!r})')
        if self.on_dock_hide not in ("remove", "placeholder"):
            problems.append(f'[moderation] on_dock_hide must be "remove" or "placeholder" (got {self.on_dock_hide!r})')
        if self.removal_style not in REMOVAL_STYLES:
            problems.append(f'[moderation] removal_style must be "fade" or "instant" (got {self.removal_style!r})')
        if not self.placeholder_text.strip():
            problems.append("[moderation] placeholder_text can't be empty")
        return problems

    def client_settings(self) -> dict[str, str]:
        return {"removal_style": self.removal_style, "placeholder_text": self.placeholder_text}


def build_block_pattern(words: Iterable[str]) -> re.Pattern[str] | None:
    """Whole-word, case-insensitive. Entries with spaces match as phrases."""
    cleaned = sorted({w.strip().lower() for w in words if w.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(w) for w in cleaned) + r")(?!\w)", re.IGNORECASE)


def message_text(item: ChatItem) -> str:
    return "".join(f.get("v", "") for f in item.fragments if f["t"] in ("text", "mention"))


class BlockFilter:
    def __init__(self, settings: ModerationSettings) -> None:
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
