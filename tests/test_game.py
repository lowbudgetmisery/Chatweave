"""Gliese Heist game logic and heist.toml checks. Offline; random rolls are seeded or forced."""
from __future__ import annotations

import asyncio
import json
import random
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

from chatmerge import game as game_mod
from chatmerge.game import ContentError, GameConfig, GlieseHeist, LootDB, Loot, Target, load_content
from chatmerge.hub import Hub
from chatmerge.models import ChatItem, text_frag
from chatmerge.settings import OverlaySettings

ROOT = Path(__file__).resolve().parent.parent
CONTENT = load_content(ROOT / "heist.toml")


class FakeWS:
    closed = False

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_str(self, text: str) -> None:
        self.sent.append(json.loads(text))


class AlwaysRoll(random.Random):
    """random() returns a fixed value so heist results are predictable."""

    def __init__(self, value: float) -> None:
        super().__init__(1)
        self.value = value

    def random(self) -> float:  # type: ignore[override]
        return self.value


def line(author: str, text: str, platform: str = "twitch", **extra: object) -> ChatItem:
    item = ChatItem(id=f"{author}{text}", platform=platform, kind="chat", author=author,  # type: ignore[arg-type]
                    user_id=author.lower(), fragments=[text_frag(text)])
    for key, value in extra.items():
        setattr(item, key, value)
    return item


FAST = mock.patch.multiple(game_mod, BEAT_S=0.01, RESULT_SHOWN_S=0.01, PUBLISH_EVERY_S=0.01)
EASY = Target("the last slice of pizza", 1)


class GameTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = LootDB(Path(self.tmp.name) / "data" / "heist.db")
        FAST.start()

    def tearDown(self) -> None:
        FAST.stop()
        self.db.close()
        self.tmp.cleanup()

    def make(self, hub: Hub, roll: float, **cfg: object) -> GlieseHeist:
        settings = {"enabled": True, "join_s": 0.15, "steal_s": 0.15} | cfg
        return GlieseHeist(hub, GameConfig(**settings), self.db, CONTENT, rng=AlwaysRoll(roll))  # type: ignore[arg-type]

    def test_heist_success_rules_and_persistence(self) -> None:
        hub = Hub(settings=OverlaySettings(hide_users=["Troll9"]))
        ws = FakeWS()
        game = self.make(hub, 0.1)  # 0.1 is under every success chance
        seen: list[dict] = []

        async def run() -> None:
            await hub.attach(ws)  # type: ignore[arg-type]
            await game.start()
            await hub.add(line("Early", "!heist"))                  # nothing running: ignored
            self.assertTrue(await game.start_heist(EASY))
            self.assertFalse(await game.start_heist())             # one event at a time
            self.assertFalse(await game.start_steal())
            await hub.add(line("Lumi1", "!heist"))
            await hub.add(line("Lumi1", "!HEIST again"))           # joins once
            await hub.add(line("Lemons2", "!heist", "youtube"))
            await hub.add(line("Troll9", "!heist"))                # hidden users can't play
            await hub.add(line("Partner5", "!heist", shared=True))  # Shared Chat partner viewers can't
            await hub.add(line("Raccomi3", "heist"))               # not the command
            await hub.add(line("Kaneko4", "!steal"))               # wrong event: ignored
            await game.settle()
            assert game.event is not None
            seen.append(game.event.to_json())
            assert game._flow is not None
            await game._flow
            await hub.add(line("Late", "!heist"))                  # after the event: ignored

        asyncio.run(run())
        self.assertEqual(seen[0]["crew_count"], 2)
        self.assertEqual([m["name"] for m in seen[0]["crew_recent"]], ["Lumi1", "Lemons2"])
        self.assertAlmostEqual(seen[0]["chance"], 0.73)  # easy 70% + 3 points for the second Gumi
        self.assertIsNone(game.event)                          # cleared after the result
        results = [m["game"]["event"] for m in ws.sent if m["op"] == "game" and m["game"]["event"]]
        done = results[-1]
        self.assertEqual((done["phase"], done["outcome"], done["payout_count"]), ("done", "success", 2))
        self.assertIn(done["result"], [_cap(t.format_map(_values(CONTENT, 2, EASY.name))) for t in CONTENT.lines["success"]])
        self.assertEqual(len(next(r for r in results if r["phase"] == "running")["beats"]), 2)
        self.assertEqual(self.db.summary("twitch", "lumi1")[0], 1)
        self.assertEqual(self.db.summary("youtube", "lemons2")[0], 1)
        self.assertEqual(self.db.summary("twitch", "troll9")[0], 0)
        # the !commands never showed in chat; plain "heist" is ordinary chat
        self.assertEqual([m["author"] for m in hub.recent()], ["Raccomi3"])
        again = LootDB(self.db.path)  # survives a restart
        self.assertEqual(again.summary("twitch", "lumi1")[0], 1)
        again.close()

    def test_heist_fail_teasing_and_solo(self) -> None:
        for teasing in (True, False):
            hub = Hub()
            game = self.make(hub, 0.99, teasing=teasing)  # 0.99 is over every success chance

            async def run() -> None:
                await game.start_heist(EASY)
                await hub.add(line("Lumi1", "!heist"))
                assert game._flow is not None
                await game._flow

            captured: list[dict] = []
            real = game._publish

            async def spy() -> None:
                if game.event is not None:
                    captured.append(game.event.to_json())
                await real()

            game._publish = spy  # type: ignore[method-assign]
            asyncio.run(run())
            done = captured[-1]
            self.assertEqual((done["outcome"], done["payout_count"]), ("fail", 0))
            pool = CONTENT.lines["fail"] + (CONTENT.lines["fail_teasing"] if teasing else ())
            # one joined, so {crew} reads as the singular
            self.assertIn(done["result"], [_cap(t.format_map(_values(CONTENT, 1, EASY.name))) for t in pool])
        self.assertEqual(self.db.summary("twitch", "lumi1")[0], 0)

        hub = Hub()
        game = self.make(hub, 0.1)
        outcomes: list[str] = []

        async def solo() -> None:
            await game.start_heist(EASY)
            assert game._flow is not None
            outcomes.append("started")
            await asyncio.sleep(0.2)
            await game._flow

        real_resolve = game._resolve_heist

        async def spy_resolve(ev):  # type: ignore[no-untyped-def]
            await real_resolve(ev)
            outcomes.append(ev.outcome)
            self.assertEqual(ev.beats, [])  # nobody joined: no story beats, straight to the result

        game._resolve_heist = spy_resolve  # type: ignore[method-assign]
        asyncio.run(solo())
        self.assertEqual(outcomes, ["started", "solo"])

    def test_steal_race(self) -> None:
        hub = Hub()
        game = self.make(hub, 0.1)
        prize = Loot("a golden spatula", "rare")
        winners: list[object] = []

        async def run() -> None:
            await game.start_steal(prize)
            await hub.add(line("Lumi1", "!heist"))                  # wrong event: ignored
            await hub.add(line("Kaneko4", "!steal", "youtube"))
            await hub.add(line("Lemons2", "!steal"))                # too late, someone took it
            assert game.event is not None and game._flow is not None
            winners.append(game.event.winner)
            await game._flow
            await game.start_steal(prize)                           # nobody types this time
            assert game._flow is not None
            await game._flow

        asyncio.run(run())
        self.assertEqual(getattr(winners[0], "name"), "Kaneko4")
        self.assertEqual(self.db.summary("youtube", "kaneko4"), (1, 1, 0))
        self.assertEqual(self.db.summary("twitch", "lemons2")[0], 0)

    def test_dock_buttons_and_busy(self) -> None:
        hub = Hub()
        game = self.make(hub, 0.1)
        replies: list[dict] = []

        async def reply(m: dict) -> None:
            replies.append(m)

        async def run() -> None:
            await hub.handle_client('{"op":"game_heist"}', reply)
            assert game.event is not None
            self.assertEqual(game.event.kind, "heist")
            await hub.handle_client('{"op":"game_steal"}', reply)
            await hub.handle_client('{"op":"game_heist"}', reply)
            await game.stop()  # shutdown mid-event: cancelled and cleared

        asyncio.run(run())
        self.assertEqual(replies, [{"op": "game_busy", "which": "steal"}, {"op": "game_busy", "which": "heist"}])
        self.assertIsNone(game.event)

    def test_stash_ranks_and_cooldown(self) -> None:
        hub = Hub()
        ws = FakeWS()
        game = self.make(hub, 0.1)
        rows = [("twitch", "lumi1", "Lumi1", Loot(f"item {i}", "common")) for i in range(11)]
        rows.append(("twitch", "lumi1", "Lumi1", Loot("a jar of bottled thunder", "rare")))
        self.db.record(rows, "e1")

        async def run() -> None:
            await hub.attach(ws)  # type: ignore[arg-type]
            await hub.add(line("Lumi1", "!stash"))
            await hub.add(line("Lumi1", "!stash"))   # cooldown: no second toast
            await hub.add(line("Newbie", "!stash"))
            await game.settle()

        asyncio.run(run())
        toasts = [m["text"] for m in ws.sent if m["op"] == "game_toast"]
        self.assertEqual(toasts, ["Lumi1 (Cat Burglar): 12 items stolen, 1 rare",
                                  "Newbie (Gumi Recruit): no loot yet. Type !heist when a heist starts!"])

    def test_busy_chat_is_batched_and_never_held_up(self) -> None:
        import time as _time

        hub = Hub()
        ws = FakeWS()
        game = self.make(hub, 0.1, join_s=5.0)
        took: list[float] = []

        async def run() -> None:
            await hub.attach(ws)  # type: ignore[arg-type]
            await game.start_heist(EASY)
            ws.sent.clear()
            start = _time.monotonic()
            for i in range(300):
                await hub.add(line(f"Gumi{i}", "!heist", "twitch" if i % 2 else "youtube"))
            took.append(_time.monotonic() - start)
            await game.settle()
            await game.stop()

        asyncio.run(run())
        self.assertLess(took[0], 1.0)
        updates = [m for m in ws.sent if m["op"] == "game" and m["game"]["event"]]
        self.assertLessEqual(len(updates), 3)  # 300 joins, a handful of updates
        self.assertEqual(updates[-1]["game"]["event"]["crew_count"], 300)
        self.assertEqual(len(updates[-1]["game"]["event"]["crew_recent"]), game_mod.SHOWN_NAMES)

    def test_subs_only_overlay_means_subs_only_game(self) -> None:
        hub = Hub(settings=OverlaySettings(overlay_audience="subs"))
        game = self.make(hub, 0.1)

        async def run() -> None:
            await game.start_heist(EASY)
            await hub.add(line("Viewer", "!heist"))
            await hub.add(line("Subby", "!heist", roles=["member"]))
            await game.settle()
            assert game.event is not None
            self.assertEqual([m.name for m in game.event.crew], ["Subby"])
            await game.stop()

        asyncio.run(run())

    def test_loot_odds(self) -> None:
        game = GlieseHeist(Hub(), GameConfig(enabled=True), self.db, CONTENT, rng=random.Random(7))
        for key, expected in (("1", (0.80, 0.18, 0.02)), ("3", (0.50, 0.38, 0.12))):
            counts = Counter(game.pick_loot(key).rarity for _ in range(20000))
            for rarity, share in zip(("common", "rare", "legendary"), expected):
                self.assertAlmostEqual(counts[rarity] / 20000, share, delta=0.015, msg=(key, rarity))
        self.assertEqual(GlieseHeist.chance_for(3, 0), 0.0)
        self.assertAlmostEqual(GlieseHeist.chance_for(3, 1), 0.30)
        self.assertEqual(GlieseHeist.chance_for(1, 500), game_mod.MAX_SUCCESS)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _values(content, count: int, target: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    values = {k: "" for k in game_mod.PLACEHOLDERS}
    values.update(crew=content.one if count == 1 else content.crew, one=content.one, count=str(count), target=target)
    return values


class ContentAndConfigTests(unittest.TestCase):
    def test_shipped_content(self) -> None:
        self.assertEqual(CONTENT.crew, "Gumis")
        self.assertEqual({t.difficulty for t in CONTENT.targets}, {1, 2, 3})
        self.assertEqual({i.rarity for i in CONTENT.loot}, {"common", "rare", "legendary"})
        self.assertEqual((CONTENT.rank_for(0), CONTENT.rank_for(10), CONTENT.rank_for(1000)),
                         ("Gumi Recruit", "Cat Burglar", "Lumi's Favorite Gumi"))

    def test_content_problems_are_specific(self) -> None:
        good = (ROOT / "heist.toml").read_text(encoding="utf-8")
        cases = {
            "uses {gumis}": good.replace("Clean getaway! {count}", "Clean getaway! {gumis}"),
            "stray {": good.replace("Too slow! Lumi", "Too slow! { Lumi"),
            "rarity must be": good.replace('rarity = "legendary" }', 'rarity = "mythic" }', 1),
            "difficulty must be": good.replace("difficulty = 3 }", "difficulty = 4 }", 1),
            "lowest first": good.replace("{ at = 10,", "{ at = 2,"),
            "found a [crew] header": good.replace('crew = "Gumis"', '[crew]\ncrew = "Gumis"'),
            "[lines] solo must be": good.replace("solo = [", "solos = ["),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for expected, text in cases.items():
                path = Path(tmp) / "heist.toml"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ContentError, msg=expected) as ctx:
                    load_content(path)
                self.assertIn(expected, str(ctx.exception))
            bom = Path(tmp) / "bom.toml"
            bom.write_bytes(b"\xef\xbb\xbf" + good.encode("utf-8"))  # saved by Notepad
            self.assertEqual(load_content(bom).crew, "Gumis")

    def test_config(self) -> None:
        from chatmerge import config

        self.assertEqual(GameConfig().validate(), [])
        bad = GameConfig(heist_command="heist", steal_command="!STASH", event_min_s=10, join_s=5, steal_s=500)
        self.assertEqual(len(bad.validate()), 5)
        head = '[twitch]\nchannel = "x"\n[youtube]\nenabled = false\n[game]\n'
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "heist.toml").write_text((ROOT / "heist.toml").read_text(encoding="utf-8"), encoding="utf-8")
            good = Path(tmp) / "a.toml"
            good.write_text(head + "enabled = true\nevent_min_minutes = 2\nevent_max_minutes = 3\nteasing = false\n",
                            encoding="utf-8")
            cfg = config.load(good)
            self.assertEqual((cfg.game.enabled, cfg.game.event_min_s, cfg.game.event_max_s, cfg.game.teasing),
                             (True, 120.0, 180.0, False))
            assert cfg.game.content is not None  # heist.toml found next to the config file
            self.assertEqual(cfg.game.content.crew, "Gumis")
            off = Path(tmp) / "off.toml"
            off.write_text(head + 'enabled = false\ncontent_file = "missing.toml"\n', encoding="utf-8")
            self.assertIsNone(config.load(off).game.content)  # off: the text file isn't needed
            for bad_line in ('enabled = "yes"', "event_min_minutes = true", "teasing = 1",
                             'heist_command = "!go"\nsteal_command = "!GO"',
                             'enabled = true\ncontent_file = "missing.toml"'):
                path = Path(tmp) / "b.toml"
                path.write_text(head + bad_line + "\n", encoding="utf-8")
                with self.assertRaises(config.ConfigError, msg=bad_line):
                    config.load(path)


if __name__ == "__main__":
    unittest.main()
