"""Entry point.

  python -m chatmerge run            merged Twitch + YouTube chat (default)
  python -m chatmerge demo           fake feed, for placing the overlay in OBS
  python -m chatmerge auth-youtube   one-time YouTube login (opens the browser)
  python -m chatmerge check          validate config, proto build and token presence
  python -m chatmerge replay FILE    play a recording (made with run --record) into the overlay
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import logging.handlers
import sys
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from . import config as config_mod
from . import __version__, mentions, protogen
from .custom_emotes import CustomEmotes
from .demo import run_demo
from .hub import Hub
from .recorder import Recorder, RecordingError, read_recording, replay
from .seventv import SevenTV
from .server import build_app
from .settings import SettingsError, SettingsStore

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


def _make_hub(cfg: config_mod.Config) -> Hub:
    store = SettingsStore(cfg.settings_path, cfg.overlay)
    try:
        settings = store.load()
    except SettingsError as exc:
        raise config_mod.ConfigError(str(exc)) from exc
    return Hub(
        backlog=cfg.backlog,
        mention_pattern=mentions.build_pattern(cfg.mention_names),
        custom_emotes=CustomEmotes(cfg.emotes_dir),
        settings=settings,
        store=store,
    )


class _NoSocket:
    """Stands in for the Twitch websocket during replay (PING replies go nowhere)."""

    async def send_str(self, _: str) -> None:
        return None


async def _serve(
    cfg: config_mod.Config,
    mode: str,
    test_video_id: str | None = None,
    record: bool = False,
    replay_file: Path | None = None,
    speed: float = 1.0,
) -> None:
    """mode: run (live chat), demo (fake feed) or replay (a recording)."""
    hub = _make_hub(cfg)
    runner = web.AppRunner(build_app(hub, emotes_dir=cfg.emotes_dir, port=cfg.port))
    await runner.setup()
    site = web.TCPSite(runner, cfg.host, cfg.port)
    await site.start()
    base = f"http://{cfg.host}:{cfg.port}/"
    print(f"chatmerge {__version__}")
    print(f"Overlay (OBS browser source): {base}")
    print(f"Events window (OBS browser source): {base}events")
    print(f"Dock (OBS custom browser dock): {base}?dock=1")
    if cfg.game.enabled:
        print(f"Starcatch game (OBS browser source): {base}game")
    print(f'Log file: "{cfg.logs_dir / "chatmerge.log"}"')

    recorder: Recorder | None = None
    if record:
        recorder = Recorder(Recorder.new_path(cfg.root / "recordings"), __version__, cfg.twitch_channel)
        print(f'RECORDING chat to "{recorder.path}" (real viewers\' messages: keep it private)')
    print("Press Ctrl+C to stop.")

    tasks: list[asyncio.Task[None]] = []
    star_db = None
    if cfg.game.enabled:
        from .game import StarDB, Starcatch

        star_db = StarDB(cfg.data_dir / "starcatch.db")
        game = Starcatch(hub, cfg.game, star_db)
        tasks.append(asyncio.create_task(_supervise("game", hub, game.run)))
    try:
        async with aiohttp.ClientSession() as session:
            if mode == "demo":
                tasks.append(asyncio.create_task(run_demo(hub)))
            elif mode == "replay":
                assert replay_file is not None
                tasks.append(asyncio.create_task(_replay(hub, session, replay_file, speed)))
            else:
                if cfg.twitch_enabled:
                    from .twitch_irc import TwitchChat

                    twitch = TwitchChat(cfg.twitch_channel, hub, session, SevenTV(session), recorder=recorder)
                    tasks.append(asyncio.create_task(_supervise("twitch", hub, twitch.run)))
                if cfg.youtube_enabled:
                    from .youtube_live import YouTubeChat

                    youtube = YouTubeChat(
                        hub, session, cfg.token, cfg.detect_interval_s, test_video_id=test_video_id,
                        quota_per_day=cfg.quota_per_day, hours_per_day=cfg.hours_per_day, recorder=recorder,
                    )
                    tasks.append(asyncio.create_task(_supervise("youtube", hub, youtube.run)))
            try:
                await asyncio.gather(*tasks)
                if mode == "replay":
                    print("Replay finished. The overlay stays up; press Ctrl+C to stop.")
                    await asyncio.Event().wait()
            finally:
                for task in tasks:
                    task.cancel()
                await runner.cleanup()
    finally:
        if recorder is not None:
            recorder.close()
        if star_db is not None:
            star_db.close()


async def _replay(hub: Hub, session: aiohttp.ClientSession, path: Path, speed: float) -> None:
    from .twitch_irc import TwitchChat

    header, entries = read_recording(path)
    channel = str(header.get("twitch_channel") or "replay")
    pace = "as fast as possible" if speed == 0 else f"at {speed:g}x speed"
    await hub.set_status("replay", f'playing "{path.name}" {pace} (recorded {header.get("started", "?")})')
    seventv = SevenTV(session)
    await seventv.load_global()
    twitch = TwitchChat(channel, hub, session, seventv)
    sock = _NoSocket()
    youtube: Any = None  # (pb2, dispatcher), built on the first YouTube line

    async def on_twitch(line: str) -> None:
        await twitch.handle_line(sock, line)  # type: ignore[arg-type]

    async def on_youtube(raw: bytes) -> None:
        nonlocal youtube
        if youtube is None:
            from .youtube_live import YouTubeDispatcher

            pb2, _ = protogen.load()
            youtube = (pb2, YouTubeDispatcher(hub, pb2))
        pb2, dispatcher = youtube
        msg = pb2.LiveChatMessage()
        msg.ParseFromString(raw)
        await dispatcher.dispatch(msg)

    count = await replay(entries, on_twitch, on_youtube, speed)
    await hub.set_status("replay", f'finished "{path.name}" ({count} lines)')


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _add_log_file(logs_dir: Path) -> None:
    """logs/chatmerge.log, kept to about 6 MB (3 files). Send it along when reporting a problem."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        logs_dir / "chatmerge.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)


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
    parser.add_argument("command", nargs="?", default="run", choices=["run", "demo", "auth-youtube", "check", "replay"])
    parser.add_argument("file", nargs="?", default="", help="replay: the recording to play")
    parser.add_argument("--config", default=str(config_mod.DEFAULT_CONFIG))
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--youtube-url", default="", help="TEST MODE: read this public live stream's chat instead of your own")
    parser.add_argument("--record", action="store_true", help="run: also save raw chat to the recordings folder")
    parser.add_argument("--speed", type=float, default=1.0, help="replay: 2 = twice as fast, 0 = all at once")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    if sys.platform == "win32":
        # grpc.aio needs a selector loop on Windows; aiohttp works on either.
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        cfg = config_mod.load(config_mod.Path(args.config), demo=args.command in ("demo", "replay"))
        _add_log_file(cfg.logs_dir)
        log.info("chatmerge %s starting: %s", __version__, args.command)
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
        replay_file: Path | None = None
        if args.command == "replay":
            if not args.file:
                raise config_mod.ConfigError("replay needs a recording file: python -m chatmerge replay recordings\\chat_....jsonl")
            replay_file = Path(args.file).expanduser().resolve()
            read_recording(replay_file)  # fail now, before the server starts, if the file is bad
            if not 0 <= args.speed <= 100:
                raise config_mod.ConfigError(f"--speed must be between 0 and 100 (got {args.speed})")
        if args.record and args.command != "run":
            raise config_mod.ConfigError("--record only works with the run command")
        asyncio.run(_serve(cfg, args.command, test_video_id=test_video_id, record=args.record,
                           replay_file=replay_file, speed=args.speed))
        return 0
    except (config_mod.ConfigError, RecordingError) as exc:
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
