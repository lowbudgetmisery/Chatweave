"""Starcatch game logic. Offline; random rolls are seeded or forced."""
from __future__ import annotations

import asyncio
import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from chatmerge.game import SPECIES, GameConfig, Starcatch, StarDB
from chatmerge.hub import Hub
from chatmerge.models import ChatItem, text_frag
from chatmerge.settings import OverlaySettings


class FakeWS:
    closed = False

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_str(self, text: str) -> None:
        self.sent.append(json.loads(text))


class AlwaysRoll(random.Random):
    """random() returns a fixed value so catch results are predictable."""

    def __init__(self, value: float) -> None:
        super().__init__(1)
        self.value = value

    def random(self) -> float:  # type: ignore[override]
        return self.value


def line(author: str, text: str, platform: str = "twitch") -> ChatItem:
    return ChatItem(id=f"{author}{text}", platform=platform, kind="chat", author=author,  # type: ignore[arg-type]
                    user_id=author.lower(), fragments=[text_frag(text)])


class GameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = StarDB(Path(self.tmp.name) / "data" / "starcatch.db")

    def tearDown(self) -> None:
        self.db.close()
        self.tmp.cleanup()

    def test_catch_rules_and_persistence(self) -> None:
        hub = Hub(settings=OverlaySettings(hide_users=["Troll9"]))
        ws = FakeWS()
        game = Starcatch(hub, GameConfig(enabled=True), self.db, rng=AlwaysRoll(0.1))  # 0.1 < every catch chance
        common = next(s for s in SPECIES if s.rarity == "common")

        async def run() -> None:
            await hub.attach(ws)  # type: ignore[arg-type]
            await game.start()
            await hub.add(line("Early", "!catch"))                 # nothing out yet: ignored
            self.assertTrue(await game.spawn_now(common))
            self.assertFalse(await game.spawn_now())              # one at a time
            await hub.add(line("Lumi1", "!catch"))
            await hub.add(line("Lumi1", "!CATCH again"))          # second throw ignored
            await hub.add(line("Lemons2", "!catch", "youtube"))
            await hub.add(line("Troll9", "!catch"))               # hidden users can't play
            await hub.add(line("Raccomi3", "catch"))              # not the command
            assert game.spawn is not None
            await game.finish(game.spawn)
            await hub.add(line("Late", "!catch"))                 # after the window: ignored
            await hub.add(line("Lumi1", "!stars"))
            await hub.add(line("Lumi1", "!stars"))                # cooldown: no second toast

        asyncio.run(run())
        spawn = game.spawn
        assert spawn is not None
        self.assertEqual([c["name"] for c in spawn.catchers], ["Lumi1", "Lemons2"])
        self.assertTrue(spawn.over)
        self.assertEqual(self.db.summary("twitch", "lumi1"), (1, 1))
        self.assertEqual(self.db.summary("youtube", "lemons2"), (1, 1))
        self.assertEqual(self.db.summary("twitch", "troll9"), (0, 0))
        toasts = [m for m in ws.sent if m["op"] == "game_toast"]
        self.assertEqual([t["text"] for t in toasts], [f"Lumi1: 1 caught, 1 of {len(SPECIES)} kinds"])
        # the !commands never showed in chat; plain "catch" is ordinary chat
        self.assertEqual([m["author"] for m in hub.recent()], ["Raccomi3"])

        # survives a restart: a new connection to the same file sees the catches
        again = StarDB(self.db.path)
        self.assertEqual(again.summary("twitch", "lumi1"), (1, 1))
        again.close()

    def test_misses_and_dock_spawn(self) -> None:
        hub = Hub()
        game = Starcatch(hub, GameConfig(enabled=True), self.db, rng=AlwaysRoll(0.99))  # always misses
        replies: list[dict] = []

        async def reply(m: dict) -> None:
            replies.append(m)

        async def run() -> None:
            await hub.handle_client('{"op":"game_spawn"}', reply)
            await hub.handle_client('{"op":"game_spawn"}', reply)  # busy
            await hub.add(line("A", "!catch"))
            await hub.add(line("B", "!catch"))

        asyncio.run(run())
        assert game.spawn is not None
        self.assertEqual((game.spawn.catchers, game.spawn.misses), ([], 2))
        self.assertEqual(replies, [{"op": "game_busy"}])

    def test_rarity_mix(self) -> None:
        game = Starcatch(Hub(), GameConfig(enabled=True), self.db, rng=random.Random(7))
        counts = Counter(game.pick().rarity for _ in range(20000))
        self.assertAlmostEqual(counts["common"] / 20000, 0.70, delta=0.02)
        self.assertAlmostEqual(counts["rare"] / 20000, 0.25, delta=0.02)
        self.assertAlmostEqual(counts["legendary"] / 20000, 0.05, delta=0.01)

    def test_config(self) -> None:
        from chatmerge import config

        self.assertEqual(GameConfig().validate(), [])
        self.assertEqual(len(GameConfig(catch_command="catch", spawn_min_s=10, window_s=5).validate()), 3)
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "a.toml"
            good.write_text('[twitch]\nchannel = "x"\n[youtube]\nenabled = false\n'
                            '[game]\nenabled = true\nspawn_min_minutes = 2\nspawn_max_minutes = 3\n', encoding="utf-8")
            cfg = config.load(good)
            self.assertEqual((cfg.game.enabled, cfg.game.spawn_min_s, cfg.game.spawn_max_s), (True, 120.0, 180.0))
            for bad in ('enabled = "yes"', "spawn_min_minutes = true", 'catch_command = "!catch"\ndex_command = "!CATCH"'):
                path = Path(tmp) / "b.toml"
                path.write_text(f'[twitch]\nchannel = "x"\n[youtube]\nenabled = false\n[game]\n{bad}\n', encoding="utf-8")
                with self.assertRaises(config.ConfigError, msg=bad):
                    config.load(path)


if __name__ == "__main__":
    unittest.main()
