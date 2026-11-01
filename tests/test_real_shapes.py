"""Replay real Twitch and YouTube message shapes (recorded live Oct 2026, every name, id and text
replaced) through the same code the app runs. Catches parsing changes that synthetic tests miss."""
from __future__ import annotations

import asyncio
import logging
import unittest
from collections import Counter
from pathlib import Path

from chatmerge import protogen
from chatmerge.hub import Hub
from chatmerge.recorder import read_recording, replay
from chatmerge.twitch_irc import TwitchChat
from chatmerge.youtube_live import YouTubeDispatcher

FIXTURE = Path(__file__).parent / "fixtures" / "real_shapes_2026-10-31.jsonl"


class FakeEmotes:
    async def load_global(self) -> None:
        return None

    async def load_channel(self, _: str) -> None:
        return None

    def expand(self, fragments: list, login: str = "") -> list:
        return fragments


class NoSocket:
    async def send_str(self, _: str) -> None:
        return None


class RealShapesTests(unittest.TestCase):
    def test_replay(self) -> None:
        header, entries = read_recording(FIXTURE)
        pb2, _ = protogen.load()
        hub = Hub()
        errors: list[str] = []

        class Catch(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if record.levelno >= logging.ERROR:
                    errors.append(record.getMessage())

        handler = Catch()
        logging.getLogger().addHandler(handler)

        async def run() -> int:
            twitch = TwitchChat(header["twitch_channel"], hub, None, FakeEmotes())  # type: ignore[arg-type]
            youtube = YouTubeDispatcher(hub, pb2)

            async def on_twitch(line: str) -> None:
                await twitch.handle_line(NoSocket(), line)  # type: ignore[arg-type]

            async def on_youtube(raw: bytes) -> None:
                msg = pb2.LiveChatMessage()
                msg.ParseFromString(raw)
                await youtube.dispatch(msg)

            return await replay(entries, on_twitch, on_youtube, speed=0)

        try:
            count = asyncio.run(run())
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertEqual(count, 21)
        self.assertEqual(errors, [])
        shown = hub.recent()
        twitch = [m for m in shown if m["platform"] == "twitch"]
        youtube = [m for m in shown if m["platform"] == "youtube"]

        # Twitch: plain chat, replies, a first-time chatter, an announcement and a watch streak
        self.assertEqual(Counter(m["kind"] for m in twitch), {"chat": 6, "announcement": 1, "milestone": 1})
        self.assertEqual(sum(1 for m in twitch if m["reply_to"]), 2)
        self.assertEqual(sum(1 for m in twitch if m["first_time"]), 1)
        announcement = next(m for m in twitch if m["kind"] == "announcement")
        self.assertEqual((announcement["headline"], announcement["accent"]), ("Announcement", "blue"))
        self.assertIn("mod", announcement["roles"])
        streak = next(m for m in twitch if m["kind"] == "milestone")
        self.assertIn("watched 50 consecutive streams", streak["headline"])
        self.assertNotIn("twitch chat", hub.status)  # normal room modes: no chip

        # YouTube: the repeated message shows once; a gifted membership's receipt is skipped
        self.assertEqual(Counter(m["kind"] for m in youtube), {"chat": 3, "superchat": 2, "gift": 2, "member": 2})
        self.assertEqual(len({m["id"] for m in youtube}), len(youtube))
        self.assertTrue(all(m["amount"] for m in youtube if m["kind"] == "superchat"))
        # support events window: YouTube paid/member events only; announcements and streaks stay in chat
        self.assertEqual(Counter(m["kind"] for m in hub.recent_events()), {"superchat": 2, "gift": 2, "member": 2})


if __name__ == "__main__":
    unittest.main()
