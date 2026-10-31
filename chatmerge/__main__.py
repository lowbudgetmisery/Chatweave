"""Entry point.

  python -m chatmerge run            merged Twitch + YouTube chat (default)
  python -m chatmerge demo           fake feed, for placing the overlay in OBS
  python -m chatmerge auth-youtube   one-time YouTube login (opens the browser)
  python -m chatmerge check          validate config, proto build and token presence
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import traceback
from collections.abc import Awaitable, Callable

import aiohttp
from aiohttp import web

from . import config as config_mod
from . import __version__, mentions, protogen
from .custom_emotes import CustomEmotes
from .demo import run_demo
from .hub import Hub
from .seventv import SevenTV
from .server import build_app

log = logging.getLogger("chatmerge")


async def _supervise(name: str, hub: Hub, factory: Callable[[], Awaitable[None]]) -> None:
    """Keep a source alive; an unexpected crash is logged in full and restarted."""
    while True:
        try:
            await factory()
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - logged with traceback, then restarted
            log.error("%s crashed:\n%s", name, traceback.format_exc())
            await hub.set_status(name, f"crashed: {type(exc).__name__}: {exc} (restarting in 10s)")
            await asyncio.sleep(10)


async def _serve(cfg: config_mod.Config, demo: bool, test_video_id: str | None = None) -> None:
    hub = Hub(
        backlog=cfg.backlog,
        mention_pattern=mentions.build_pattern(cfg.mention_names),
        hide_users=cfg.hide_users,
        custom_emotes=CustomEmotes(cfg.emotes_dir),
        moderation=cfg.moderation,
    )
    runner = web.AppRunner(build_app(hub, emotes_dir=cfg.emotes_dir))
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    base = f"http://{cfg.host}:{cfg.port}/"
    print(f"chatmerge {__version__}")
    print(f"Overlay (OBS browser source): {base}")
    print(f"Events window (OBS browser source): {base}events")
    print(f"Dock (OBS custom browser dock): {base}?dock=1")
    print("Press Ctrl+C to stop.")

    tasks: list[asyncio.Task[None]] = []
    async with aiohttp.ClientSession() as session:
        if demo:
            tasks.append(asyncio.create_task(run_demo(hub)))
        else:
            if cfg.twitch_enabled:
                from .twitch_irc import TwitchChat

                twitch = TwitchChat(cfg.twitch_channel, hub, session, SevenTV(session))
                tasks.append(asyncio.create_task(_supervise("twitch", hub, twitch.run)))
            if cfg.youtube_enabled:
                from .youtube_live import YouTubeChat

                youtube = YouTubeChat(
                    hub, session, cfg.token, cfg.detect_interval_s, test_video_id=test_video_id,
                    quota_per_day=cfg.quota_per_day, hours_per_day=cfg.hours_per_day,
                )
                tasks.append(asyncio.create_task(_supervise("youtube", hub, youtube.run)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await runner.cleanup()


def _check(cfg: config_mod.Config) -> int:
    ok = True
    print(f"Twitch: {'#' + cfg.twitch_channel if cfg.twitch_enabled else 'disabled'}")
    if cfg.youtube_enabled:
        protogen.load()
        print(f'YouTube proto: built in "{protogen.GEN_DIR}"')
        for label, path in (("OAuth client", cfg.client_secret), ("Token", cfg.token)):
            exists = path.is_file()
            ok &= exists
            print(f'{label}: "{path}" {"OK" if exists else "MISSING"}')
    else:
        print("YouTube: disabled")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chatmerge")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "demo", "auth-youtube", "check"])
    parser.add_argument("--config", default=str(config_mod.DEFAULT_CONFIG))
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--youtube-url", default="", help="TEST MODE: read this public live stream's chat instead of your own")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if sys.platform == "win32":
        # grpc.aio needs a selector loop on Windows; aiohttp works on either.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        cfg = config_mod.load(config_mod.Path(args.config), demo=args.command == "demo")
        if args.command == "check":
            return _check(cfg)
        if args.command == "auth-youtube":
            from .youtube_live import run_auth

            run_auth(cfg.client_secret, cfg.token)
            return 0
        test_video_id: str | None = None
        if args.youtube_url:
            from .youtube_live import parse_video_id

            if args.command != "run":
                raise config_mod.ConfigError("--youtube-url only works with the run command")
            if not cfg.youtube_enabled:
                raise config_mod.ConfigError("YouTube is turned off in config.toml ([youtube] enabled = false); turn it on to test YouTube.")
            test_video_id = parse_video_id(args.youtube_url)
            if not test_video_id:
                raise config_mod.ConfigError(
                    f"Could not find a video ID in {args.youtube_url!r}. Open the live stream, click Share, "
                    "and paste that link (youtu.be/... or youtube.com/watch?v=...). Channel links like /@name/live don't work."
                )
            print(f"YouTube TEST MODE: reading the public chat of video {test_video_id} (not your own stream)")
        if args.command == "run" and cfg.youtube_enabled and not cfg.token.is_file():
            raise config_mod.ConfigError(
                f'YouTube login missing: "{cfg.token}". Double-click "YouTube Login.bat" first, '
                'or set [youtube] enabled = false in config.toml to run Twitch only.'
            )
        asyncio.run(_serve(cfg, demo=args.command == "demo", test_video_id=test_video_id))
        return 0
    except config_mod.ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
