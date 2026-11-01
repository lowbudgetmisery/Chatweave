"""v1.7: line filters, dock settings, server security, record/replay. Offline, no credentials."""
from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

from chatmerge import protogen
from chatmerge.filters import LineFilter, audience_ok, is_command
from chatmerge.hub import Hub
from chatmerge.models import ChatItem, emote_frag, text_frag
from chatmerge.server import build_app
from chatmerge.settings import OverlaySettings, SettingsError, SettingsStore, apply_changes, diff


class FakeWS:
    closed = False

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_str(self, text: str) -> None:
        self.sent.append(json.loads(text))


def chat(i: str, author: str = "viewer", text: str = "hello", kind: str = "chat",
         roles: list[str] | None = None, platform: str = "twitch") -> ChatItem:
    return ChatItem(id=i, platform=platform, kind=kind, author=author, user_id=author.lower(),  # type: ignore[arg-type]
                    fragments=[text_frag(text)] if text else [], roles=roles or [],
                    headline="Super Chat $5.00" if kind != "chat" else None)


class FilterTests(unittest.TestCase):
    def test_commands(self) -> None:
        self.assertTrue(is_command(chat("1", text="!catch")))
        self.assertTrue(is_command(chat("1", text="  !Discord please")))
        self.assertFalse(is_command(chat("1", text="wow!")))
        self.assertFalse(is_command(chat("1", text="! nope")))
        self.assertFalse(is_command(chat("1", text="!!!")))
        self.assertFalse(is_command(chat("1", text="!catch", kind="superchat")))  # paid lines always show
        emote_first = chat("1", text="")
        emote_first.fragments = [emote_frag("Kappa", "https://static-cdn.jtvnw.net/x"), text_frag(" !catch")]
        self.assertFalse(is_command(emote_first))

    def test_bot_lists_and_toggles(self) -> None:
        default = LineFilter(OverlaySettings())
        self.assertEqual(default.drop_reason(chat("1", author="Nightbot")), "hidden user or bot")
        self.assertIsNone(default.drop_reason(chat("1", author="StreamElements")))  # tip bots kept by default
        self.assertEqual(default.drop_reason(chat("1", text="!catch")), "!command")
        everything = LineFilter(OverlaySettings(hide_tip_bots=True, hide_users=["@Troll9"]))
        self.assertIsNotNone(everything.drop_reason(chat("1", author="streamelements")))
        self.assertIsNotNone(everything.drop_reason(chat("1", author="@troll9", platform="youtube")))
        off = LineFilter(OverlaySettings(hide_known_bots=False, hide_commands=False))
        self.assertIsNone(off.drop_reason(chat("1", author="Nightbot", text="!discord")))

    def test_audience(self) -> None:
        self.assertFalse(audience_ok(chat("1")))
        for role in ("member", "vip", "mod", "broadcaster"):
            self.assertTrue(audience_ok(chat("1", roles=[role])), role)
        self.assertTrue(audience_ok(chat("1", kind="raid")))

    def test_hub_hooks_see_commands_before_filters(self) -> None:
        seen: list[str] = []

        async def hook(item: ChatItem) -> None:
            seen.append(item.fragments[0]["v"])

        async def broken(_: ChatItem) -> None:
            raise RuntimeError("feature bug")

        async def run() -> Hub:
            hub = Hub()
            hub.add_hook(broken)  # must not stop chat
            hub.add_hook(hook)
            await hub.add(chat("1", text="!catch"))
            await hub.add(chat("2", text="hi", roles=["member"]))
            return hub

        with self.assertLogs("chatmerge.hub", level="ERROR"):
            hub = asyncio.run(run())
        self.assertEqual(seen, ["!catch", "hi"])
        self.assertEqual([(m["id"], m["sub_ok"]) for m in hub.recent()], [("2", True)])


class SettingsTests(unittest.TestCase):
    def test_strict_types_and_cleanup(self) -> None:
        base = OverlaySettings()
        new = apply_changes(base, {"blocked_words": [" Spoiler ", "spoiler", ""], "hide_users": ["@Troll9"],
                                   "overlay_audience": "subs"})
        self.assertEqual(new.blocked_words, ["Spoiler"])
        self.assertEqual(new.hide_users, ["Troll9"])
        self.assertEqual(diff(base, new), {"blocked_words": ["Spoiler"], "hide_users": ["Troll9"], "overlay_audience": "subs"})
        for bad in ({"hide_commands": "yes"}, {"blocked_words": "spoiler"}, {"on_ban": "explode"},
                    {"nope": 1}, {"placeholder_text": "x" * 101}, {"hide_users": [1]}):
            with self.assertRaises(SettingsError, msg=str(bad)):
                apply_changes(base, bad)
        with self.assertRaises(SettingsError):
            apply_changes(base, ["not", "a", "dict"])  # type: ignore[arg-type]

    def test_store_saves_only_changes_and_survives_config_edits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            store = SettingsStore(path, OverlaySettings())
            self.assertEqual(store.load(), OverlaySettings())  # no file yet
            path.write_bytes(b'\xef\xbb\xbf{"hide_tip_bots": true}')  # saved by Notepad with a BOM
            self.assertTrue(store.load().hide_tip_bots)
            path.unlink()
            store.save(apply_changes(OverlaySettings(), {"hide_tip_bots": True}))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"hide_tip_bots": True})
            # config.toml later changes a value the dock never touched: it still applies
            later = SettingsStore(path, OverlaySettings(removal_style="instant"))
            loaded = later.load()
            self.assertTrue(loaded.hide_tip_bots)
            self.assertEqual(loaded.removal_style, "instant")
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(SettingsError) as ctx:
                later.load()
            self.assertIn("settings.json", str(ctx.exception))

    def test_config_reads_filters_and_rejects_wrong_types(self) -> None:
        from chatmerge import config

        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "a.toml"
            good.write_text('[twitch]\nchannel = "x"\n[youtube]\nenabled = false\n'
                            '[filters]\noverlay_audience = "subs"\nhide_commands = false\n'
                            '[display]\nhide_users = ["@Bot"]\n', encoding="utf-8")
            cfg = config.load(good)
            self.assertEqual((cfg.overlay.overlay_audience, cfg.overlay.hide_commands, cfg.overlay.hide_users),
                             ("subs", False, ["Bot"]))
            old_style = Path(tmp) / "b.toml"  # 1.6 config: no [filters] at all
            old_style.write_text('[twitch]\nchannel = "x"\n[youtube]\nenabled = false\n', encoding="utf-8")
            self.assertEqual(config.load(old_style).overlay, OverlaySettings())
            bad = Path(tmp) / "c.toml"
            bad.write_text('[twitch]\nchannel = "x"\n[youtube]\nenabled = false\n[moderation]\nblocked_words = "spoiler"\n',
                           encoding="utf-8")
            with self.assertRaises(config.ConfigError):
                config.load(bad)


class DockSettingsTests(unittest.TestCase):
    def test_update_reset_errors_and_hide_user(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            base = OverlaySettings()
            hub = Hub(settings=base, store=SettingsStore(path, base))
            ws = FakeWS()
            replies: list[dict] = []

            async def reply(m: dict) -> None:
                replies.append(m)

            async def run() -> None:
                await hub.attach(ws)  # type: ignore[arg-type]
                await hub.add(chat("1", author="Troll9"))
                await hub.add(chat("2", author="Troll9", platform="youtube"))
                await hub.add(chat("3", author="Nice"))
                await hub.handle_client(json.dumps({"op": "update_settings", "settings": {"overlay_audience": "subs"}}), reply)
                await hub.handle_client(json.dumps({"op": "update_settings", "settings": {"on_ban": "nuke"}}), reply)
                await hub.handle_client(json.dumps({"op": "hide_user", "platform": "twitch", "user_id": "troll9",
                                                    "name": "Troll9"}), reply)
                await hub.add(chat("4", author="troll9", platform="youtube"))  # same name, other platform: hidden too
                await hub.handle_client(json.dumps({"op": "hide_user", "platform": "x", "user_id": "a", "name": "a"}), reply)
                await hub.handle_client(json.dumps({"op": "hide_user", "platform": "twitch", "user_id": "b",
                                                    "name": "b" * 150}), reply)  # too long to save

            asyncio.run(run())
            self.assertEqual(replies[0], {"op": "settings_saved"})
            self.assertEqual(replies[1]["op"], "settings_error")
            self.assertIn("on_ban", replies[1]["errors"][0])
            # hide_user success sends no reply (an open Settings panel must not think it saved);
            # malformed ignored; too-long name gets its own error op
            self.assertEqual(replies[2]["op"], "hide_user_error")
            self.assertEqual(len(replies), 3)
            self.assertEqual(hub.settings.overlay_audience, "subs")
            self.assertEqual(hub.settings.hide_users, ["Troll9"])
            # the name is hidden on both platforms, including lines already on screen
            self.assertEqual([m["id"] for m in hub.recent()], ["3"])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"hide_users": ["Troll9"], "overlay_audience": "subs"})
            self.assertIn("resync", [m["op"] for m in ws.sent])
            self.assertEqual(ws.sent[0]["bot_names"]["chat"][0], "botisimo")

            asyncio.run(hub.handle_client('{"op":"reset_settings"}', reply))
            self.assertEqual(hub.settings, base)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ServerSecurityTests(unittest.TestCase):
    def test_host_and_origin_checks(self) -> None:
        async def run() -> dict[str, int | str]:
            port = _free_port()
            runner = web.AppRunner(build_app(Hub(), port=port))
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            base = f"http://127.0.0.1:{port}"
            out: dict[str, int | str] = {}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{base}/health") as r:
                        out["health"] = r.status
                    async with session.get(f"{base}/health", headers={"Host": f"evil.example:{port}"}) as r:
                        out["rebind"] = r.status
                    for label, origin in (("own", base), ("evil", "https://evil.example")):
                        try:
                            async with session.ws_connect(f"{base}/ws", headers={"Origin": origin}) as ws:
                                out[label] = (await ws.receive_json())["op"]
                        except aiohttp.WSServerHandshakeError as exc:
                            out[label] = exc.status
            finally:
                await runner.cleanup()
            return out

        out = asyncio.run(run())
        self.assertEqual(out, {"health": 200, "rebind": 403, "own": "hello", "evil": 403})


class RecorderFailureTests(unittest.TestCase):
    def test_write_failure_stops_recording_not_chat(self) -> None:
        from chatmerge.recorder import Recorder

        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(Path(tmp) / "r.jsonl", "test", "x")

            def disk_full(_: dict) -> None:
                raise OSError(28, "No space left on device")

            rec._write = disk_full  # type: ignore[method-assign]
            with self.assertLogs("chatmerge.recorder", level="ERROR"):
                rec.twitch("PING :tmi.twitch.tv")  # must not raise
            rec.twitch("second line")             # silently skipped after the failure
            self.assertTrue(rec.failed)
            rec.close()


class RecordReplayTests(unittest.TestCase):
    def test_round_trip_through_real_handlers(self) -> None:
        from chatmerge.recorder import Recorder, RecordingError, read_recording, replay
        from chatmerge.emotes import ThirdPartyEmotes
        from chatmerge.twitch_irc import TwitchChat
        from chatmerge.youtube_live import YouTubeDispatcher

        pb2, _ = protogen.load()
        T = pb2.LiveChatMessageSnippet.TypeWrapper
        yt = pb2.LiveChatMessage(id="yt1")
        yt.snippet.type = T.TEXT_MESSAGE_EVENT
        yt.snippet.text_message_details.message_text = "hi from youtube"
        yt.author_details.display_name = "@Lemons2"
        yt.author_details.channel_id = "UC1"
        tomb = pb2.LiveChatMessage(id="yt1")
        tomb.snippet.type = T.TOMBSTONE

        lines = [
            "@badges=;display-name=Raccomi3;emotes=;id=t1;user-id=3 :raccomi3!r@r.tmi.twitch.tv PRIVMSG #kanekolumi :hello lumi",
            "@badges=;display-name=Nightbot;emotes=;id=t2;user-id=9 :nightbot!n@n.tmi.twitch.tv PRIVMSG #kanekolumi :Follow!",
            "@badges=;display-name=Kaneko4;emotes=;id=t3;user-id=4 :kaneko4!k@k.tmi.twitch.tv PRIVMSG #kanekolumi :!catch",
            "@target-msg-id=t1 :tmi.twitch.tv CLEARMSG #kanekolumi :hello lumi",
            "PING :tmi.twitch.tv",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            rec = Recorder(Recorder.new_path(Path(tmp)), "test", "kanekolumi")
            for line in lines[:3]:
                rec.twitch(line)
            rec.youtube(yt.SerializeToString())
            for line in lines[3:]:
                rec.twitch(line)
            rec.youtube(tomb.SerializeToString())
            rec.close()
            with self.assertRaises(FileExistsError):  # never overwrites a recording
                rec.path.open("x")

            header, entries = read_recording(rec.path)
            self.assertEqual(header["twitch_channel"], "kanekolumi")

            async def run() -> tuple[Hub, list[str], int]:
                hub = Hub()
                seen: list[str] = []

                async def hook(item: ChatItem) -> None:
                    seen.append(item.author)

                hub.add_hook(hook)
                async with aiohttp.ClientSession() as session:
                    twitch = TwitchChat("kanekolumi", hub, session, ThirdPartyEmotes(session))

                    class NoSocket:
                        async def send_str(self, _: str) -> None:
                            return None

                    dispatcher = YouTubeDispatcher(hub, pb2)

                    async def on_twitch(line: str) -> None:
                        await twitch.handle_line(NoSocket(), line)  # type: ignore[arg-type]

                    async def on_youtube(raw: bytes) -> None:
                        msg = pb2.LiveChatMessage()
                        msg.ParseFromString(raw)
                        await dispatcher.dispatch(msg)

                    count = await replay(entries, on_twitch, on_youtube, speed=0)
                return hub, seen, count

            hub, seen, count = asyncio.run(run())
            self.assertEqual(count, 7)
            self.assertEqual(seen, ["Raccomi3", "Nightbot", "Kaneko4", "@Lemons2"])  # hooks see everything
            self.assertEqual(hub.recent(), [])  # bot + command filtered, t1 and yt1 deleted by mods

            bad = Path(tmp) / "bad.jsonl"
            bad.write_text('{"format": "chatmerge-recording", "format_version": 1}\n{"t": 0, "src": "irc"}\n', encoding="utf-8")
            _, bad_entries = read_recording(bad)
            with self.assertRaises(RecordingError):
                list(bad_entries)


if __name__ == "__main__":
    unittest.main()
