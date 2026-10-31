"""config.toml loading with loud, specific errors."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .moderation import ModerationSettings

ROOT = Path(__file__).resolve().parent.parent
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
    hide_users: list[str]
    emotes_dir: Path
    moderation: ModerationSettings


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
        hide_users=[str(n) for n in display.get("hide_users", [])],
        emotes_dir=ROOT / "emotes",
        moderation=ModerationSettings(
            on_delete=str(mod.get("on_delete", "remove")),
            on_timeout=str(mod.get("on_timeout", "remove")),
            on_ban=str(mod.get("on_ban", "remove")),
            on_clear=str(mod.get("on_clear", "remove")),
            on_dock_hide=str(mod.get("on_dock_hide", "remove")),
            placeholder_text=str(mod.get("placeholder_text", "message removed")),
            removal_style=str(mod.get("removal_style", "fade")),
            blocked_words=[str(w) for w in mod.get("blocked_words", [])],
            block_links=bool(mod.get("block_links", False)),
            exempt_mods=bool(mod.get("exempt_mods", True)),
        ),
    )
    if not demo and not (cfg.twitch_enabled or cfg.youtube_enabled):
        raise ConfigError("Both Twitch and YouTube are disabled in config.toml; nothing to show.")
    problems = cfg.moderation.validate()
    if problems:
        raise ConfigError("; ".join(problems))
    if not 100 <= cfg.quota_per_day <= 10_000_000:
        raise ConfigError(f"[youtube] quota_per_day must be at least 100 (got {cfg.quota_per_day}).")
    if not 0.5 <= cfg.hours_per_day <= 24:
        raise ConfigError(f"[youtube] hours_per_day must be between 0.5 and 24 (got {cfg.hours_per_day}).")
    if cfg.host not in ("127.0.0.1", "localhost"):
        raise ConfigError(f"server.host must stay 127.0.0.1 (got {cfg.host!r}); the overlay is local only.")
    return cfg
