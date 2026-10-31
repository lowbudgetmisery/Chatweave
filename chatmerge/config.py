"""config.toml loading with loud, specific errors."""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .settings import OverlaySettings, SettingsError, apply_changes

# Packaged .exe: user files (config.toml, secrets, emotes, settings.json, logs) sit next to the exe.
ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"


class ConfigError(RuntimeError):
    pass


@dataclass(slots=True)
class Config:
    host: str
    port: int
    backlog: int
    twitch_enabled: bool
    twitch_channel: str
    youtube_enabled: bool
    secrets_dir: Path
    client_secret: Path
    token: Path
    detect_interval_s: float
    quota_per_day: int
    hours_per_day: float
    mention_names: list[str]
    emotes_dir: Path
    overlay: OverlaySettings  # config.toml defaults; settings.json (dock) overrides
    settings_path: Path
    logs_dir: Path
    root: Path


def _secrets_dir(raw: str) -> Path:
    """Empty = the 'secrets' folder next to this app."""
    if raw:
        path = Path(os.path.expandvars(raw)).expanduser()
        return path if path.is_absolute() else ROOT / path
    return ROOT / "secrets"


def load(path: Path = DEFAULT_CONFIG, demo: bool = False) -> Config:
    """demo=True skips the Twitch and YouTube requirements (the demo feed needs neither)."""
    if not path.is_file():
        raise ConfigError(f'Config file not found: "{path}"')
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    server = raw.get("server", {})
    display = raw.get("display", {})
    twitch = raw.get("twitch", {})
    youtube = raw.get("youtube", {})
    mod = raw.get("moderation", {})
    filters = raw.get("filters", {})

    twitch_enabled = bool(twitch.get("enabled", True))
    channel = str(twitch.get("channel", "")).strip().lstrip("#").lower()
    if twitch_enabled and not channel and not demo:
        raise ConfigError(f'[twitch] channel is empty in "{path}". Put your Twitch login name there.')

    youtube_enabled = bool(youtube.get("enabled", True)) and not demo
    secrets = _secrets_dir(str(youtube.get("secrets_dir", "")).strip()) if youtube_enabled else ROOT
    cfg = Config(
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 8787)),
        backlog=int(server.get("backlog", 50)),
        twitch_enabled=twitch_enabled,
        twitch_channel=channel,
        youtube_enabled=youtube_enabled,
        secrets_dir=secrets,
        client_secret=secrets / str(youtube.get("client_secret_file", "client_secret.json")),
        token=secrets / str(youtube.get("token_file", "youtube_token.json")),
        detect_interval_s=float(youtube.get("detect_interval_s", 30)),
        quota_per_day=int(youtube.get("quota_per_day", 10000)),
        hours_per_day=float(youtube.get("hours_per_day", 8)),
        mention_names=[str(n) for n in display.get("mention_names", [channel] if channel else [])],
        emotes_dir=ROOT / "emotes",
        overlay=_overlay_settings(path, display, mod, filters),
        settings_path=ROOT / "settings.json",
        logs_dir=ROOT / "logs",
        root=ROOT,
    )
    if not demo and not (cfg.twitch_enabled or cfg.youtube_enabled):
        raise ConfigError("Both Twitch and YouTube are disabled in config.toml; nothing to show.")
    if not 100 <= cfg.quota_per_day <= 10_000_000:
        raise ConfigError(f"[youtube] quota_per_day must be at least 100 (got {cfg.quota_per_day}).")
    if not 0.5 <= cfg.hours_per_day <= 24:
        raise ConfigError(f"[youtube] hours_per_day must be between 0.5 and 24 (got {cfg.hours_per_day}).")
    if cfg.host not in ("127.0.0.1", "localhost"):
        raise ConfigError(f"server.host must stay 127.0.0.1 (got {cfg.host!r}); the overlay is local only.")
    return cfg


_OVERLAY_KEYS = {
    "moderation": ("on_delete", "on_timeout", "on_ban", "on_clear", "on_dock_hide", "placeholder_text",
                   "removal_style", "blocked_words", "block_links", "exempt_mods"),
    "filters": ("hide_commands", "hide_known_bots", "hide_tip_bots", "overlay_audience"),
    "display": ("hide_users",),
}


def _overlay_settings(path: Path, display: dict, mod: dict, filters: dict) -> OverlaySettings:
    """Strictly typed: a typo like blocked_words = "spoiler" (not a list) stops with a clear error."""
    sections = {"moderation": mod, "filters": filters, "display": display}
    changes = {}
    for section, keys in _OVERLAY_KEYS.items():
        for key in keys:
            if key in sections[section]:
                changes[key] = sections[section][key]
    try:
        return apply_changes(OverlaySettings(), changes)
    except SettingsError as exc:
        raise ConfigError(f'in "{path}": ' + "; ".join(exc.problems)) from exc
