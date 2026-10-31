"""Everything the dock's Settings panel can change, in one validated object.

config.toml holds the defaults. The dock saves only what differs from them to
settings.json next to config.toml, so later edits to config.toml still apply to
anything the dock never touched. None of these settings touch Twitch or YouTube;
they only decide what the overlay shows.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

log = logging.getLogger("chatmerge.settings")

MODES = ("remove", "placeholder", "keep")
DOCK_HIDE_MODES = ("remove", "placeholder")
REMOVAL_STYLES = ("fade", "instant")
AUDIENCES = ("everyone", "subs")
# Platform mod actions plus the dock's buttons. dock_clear always removes.
ACTIONS = ("delete", "timeout", "ban", "clear", "dock_hide", "dock_clear")

MAX_LIST_ITEMS = 500
MAX_TEXT_LEN = 100


class SettingsError(ValueError):
    """One or more settings are invalid. str() is a readable list of problems."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(slots=True)
class OverlaySettings:
    # What the overlay does when a mod acts on Twitch or YouTube.
    on_delete: str = "remove"
    on_timeout: str = "remove"
    on_ban: str = "remove"
    on_clear: str = "remove"
    on_dock_hide: str = "remove"
    placeholder_text: str = "message removed"
    removal_style: str = "fade"
    # What never reaches the overlay.
    blocked_words: list[str] = field(default_factory=list)
    block_links: bool = False
    exempt_mods: bool = True
    hide_commands: bool = True
    hide_known_bots: bool = True
    hide_tip_bots: bool = False
    hide_users: list[str] = field(default_factory=list)
    # "subs": the on-stream chat shows only subs, members, VIPs, mods, you, and support events.
    overlay_audience: str = "everyone"

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
        problems: list[str] = []
        for name in ("on_delete", "on_timeout", "on_ban", "on_clear"):
            value = getattr(self, name)
            if value not in MODES:
                problems.append(f'{name} must be "remove", "placeholder" or "keep" (got {value!r})')
        if self.on_dock_hide not in DOCK_HIDE_MODES:
            problems.append(f'on_dock_hide must be "remove" or "placeholder" (got {self.on_dock_hide!r})')
        if self.removal_style not in REMOVAL_STYLES:
            problems.append(f'removal_style must be "fade" or "instant" (got {self.removal_style!r})')
        if self.overlay_audience not in AUDIENCES:
            problems.append(f'overlay_audience must be "everyone" or "subs" (got {self.overlay_audience!r})')
        if not self.placeholder_text.strip():
            problems.append("placeholder_text can't be empty")
        elif len(self.placeholder_text) > MAX_TEXT_LEN:
            problems.append(f"placeholder_text is longer than {MAX_TEXT_LEN} characters")
        for name in ("blocked_words", "hide_users"):
            items = getattr(self, name)
            if len(items) > MAX_LIST_ITEMS:
                problems.append(f"{name} has more than {MAX_LIST_ITEMS} entries")
            if any(len(x) > MAX_TEXT_LEN for x in items):
                problems.append(f"{name} has an entry longer than {MAX_TEXT_LEN} characters")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def client_settings(self) -> dict[str, Any]:
        """Sent to every page in hello/resync. Nothing private lives here."""
        return self.to_dict()


_FIELD_TYPES: dict[str, type] = {f.name: f.type for f in fields(OverlaySettings)}  # type: ignore[misc]


def _coerce(name: str, value: Any) -> Any:
    """Strict type check for one incoming value. Raises SettingsError."""
    kind = _FIELD_TYPES[name]
    if kind in ("bool", bool):
        if not isinstance(value, bool):
            raise SettingsError([f"{name} must be true or false (got {value!r})"])
        return value
    if kind in ("str", str):
        if not isinstance(value, str):
            raise SettingsError([f"{name} must be text (got {value!r})"])
        return value.strip() if name != "placeholder_text" else value
    # list[str]
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise SettingsError([f"{name} must be a list of text (got {value!r})"])
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in value:
        item = raw.strip()
        if name == "hide_users":
            item = item.lstrip("@")
        key = item.lower()
        if item and key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned


def apply_changes(base: OverlaySettings, changes: dict[str, Any]) -> OverlaySettings:
    """New settings = base + changes. Unknown keys, wrong types or bad values raise SettingsError."""
    if not isinstance(changes, dict):
        raise SettingsError(["settings must be an object"])
    unknown = sorted(set(changes) - set(_FIELD_TYPES))
    if unknown:
        raise SettingsError([f"unknown setting {k!r}" for k in unknown])
    problems: list[str] = []
    coerced: dict[str, Any] = {}
    for name, value in changes.items():
        try:
            coerced[name] = _coerce(name, value)
        except SettingsError as exc:
            problems.extend(exc.problems)
    if problems:
        raise SettingsError(problems)
    result = replace(base, **coerced)
    problems = result.validate()
    if problems:
        raise SettingsError(problems)
    return result


def diff(base: OverlaySettings, current: OverlaySettings) -> dict[str, Any]:
    a, b = base.to_dict(), current.to_dict()
    return {k: v for k, v in b.items() if a[k] != v}


class SettingsStore:
    """settings.json: only the values the dock changed away from config.toml."""

    def __init__(self, path: Path, base: OverlaySettings) -> None:
        self.path = path
        self.base = base

    def load(self) -> OverlaySettings:
        if not self.path.is_file():
            return self.base
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))  # Notepad may add a BOM
        except (OSError, ValueError) as exc:
            raise SettingsError(
                [f'Could not read "{self.path}": {exc}. Fix it, or rename it to reset the dock settings.']
            ) from exc
        try:
            return apply_changes(self.base, raw)
        except SettingsError as exc:
            raise SettingsError(
                [f'"{self.path}": {p}. Fix it, or rename the file to reset the dock settings.' for p in exc.problems]
            ) from exc

    def save(self, current: OverlaySettings) -> None:
        changes = diff(self.base, current)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(changes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: a crash mid-save never leaves a half-written file
        log.info('dock settings saved to "%s" (%d changed from config.toml)', self.path, len(changes))
