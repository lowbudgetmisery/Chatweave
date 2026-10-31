"""Offline self-tests. No network, no credentials."""
from __future__ import annotations

import asyncio
import json
import unittest

from chatmerge import protogen
from chatmerge.hub import Hub
from chatmerge.models import ChatItem, text_frag
from chatmerge.settings import OverlaySettings
from chatmerge.seventv import SevenTV, parse_emote_set
from chatmerge.twitch_irc import (
    item_from_privmsg,
    item_from_usernotice,
    parse_line,
    roles_from_badges,
    split_emotes,
)
from chatmerge.youtube_live import map_message

PRIVMSG = (
    "@badge-info=subscriber/8;badges=moderator/1,subscriber/6;color=#1E90FF;display-name=Comet_Kid;"
    "emotes=25:0-4,10-14;id=b34ccfc7-4977-403a-8a94-33c6bac34fb8;mod=1;user-id=1337 "
    ":comet_kid!comet_kid@comet_kid.tmi.twitch.tv PRIVMSG #somechannel :Kappa hi! Kappa glorp"
)
BITS = (
    "@badges=;bits=100;color=;display-name=Bean;emotes=;id=abc;user-id=42 "
    ":bean!bean@bean.tmi.twitch.tv PRIVMSG #somechannel :Cheer100 nice"
)
RESUB = (
    "@badges=subscriber/6;display-name=PixelMoth;emotes=;id=n1;login=pixelmoth;msg-id=resub;"
    "msg-param-cumulative-months=7;system-msg=PixelMoth\\ssubscribed\\sat\\sTier\\s1.\\sThey've\\ssubscribed\\sfor\\s7\\smonths!;"
    "user-id=77 :tmi.twitch.tv USERNOTICE #somechannel :seven months already"
)
RAID = (
    "@badges=;display-name=HollowSun;emotes=;id=r1;login=hollowsun;msg-id=raid;msg-param-viewerCount=42;"
    "system-msg=42\\sraiders\\sfrom\\sHollowSun\\shave\\sjoined!;user-id=88 :tmi.twitch.tv USERNOTICE #somechannel"
)

SEVENTV_SET = {
    "emotes": [
        {
            "name": "glorp",
            "flags": 0,
            "data": {"host": {"url": "//cdn.7tv.app/emote/01J94DZYDR0008SGMD5K4B9HFW",
                              "files": [{"name": "1x.webp"}, {"name": "2x.webp"}]}},
        },
        {"name": "broken", "data": {}},
    ]
}


class TwitchTests(unittest.TestCase):
    def test_parse_privmsg_with_emotes_and_roles(self) -> None:
        msg = parse_line(PRIVMSG)
        self.assertEqual(msg.command, "PRIVMSG")
        item = item_from_privmsg(msg)
        self.assertEqual(item.author, "Comet_Kid")
        self.assertEqual(item.roles, ["mod", "member"])
        self.assertEqual(item.kind, "chat")
        kinds = [f["t"] for f in item.fragments]
        self.assertEqual(kinds, ["emote", "text", "emote", "text"])
        self.assertEqual(item.fragments[0]["name"], "Kappa")
        self.assertIn("/emoticons/v2/25/", item.fragments[0]["url"])
        self.assertEqual(item.fragments[1]["v"], " hi! ")

    def test_emote_indexes_are_code_points(self) -> None:
        frags = split_emotes("\U0001F600 Kappa", "25:2-6")
        self.assertEqual([f["t"] for f in frags], ["text", "emote"])
        self.assertEqual(frags[1]["name"], "Kappa")

    def test_bits(self) -> None:
        item = item_from_privmsg(parse_line(BITS))
        self.assertEqual(item.kind, "bits")
        self.assertEqual(item.headline, "Cheered 100 bits")

    def test_resub_unescapes_system_msg(self) -> None:
        item = item_from_usernotice(parse_line(RESUB))
        assert item is not None
        self.assertEqual(item.kind, "sub")
        self.assertEqual(item.headline, "PixelMoth subscribed at Tier 1. They've subscribed for 7 months!")
        self.assertEqual(item.fragments, [text_frag("seven months already")])

    def test_raid_without_message_has_no_body(self) -> None:
        item = item_from_usernotice(parse_line(RAID))
        assert item is not None
        self.assertEqual(item.kind, "raid")
        self.assertEqual(item.amount, "42")
        self.assertEqual(item.fragments, [])

    def test_unknown_notice_ignored(self) -> None:
        line = "@msg-id=announcement;display-name=X;id=z :tmi.twitch.tv USERNOTICE #c :hello"
        self.assertIsNone(item_from_usernotice(parse_line(line)))

    def test_badges(self) -> None:
        self.assertEqual(roles_from_badges("broadcaster/1,subscriber/0"), ["broadcaster", "member"])
        self.assertEqual(roles_from_badges(""), [])


class SevenTVTests(unittest.TestCase):
    def test_parse_and_expand(self) -> None:
        emotes = parse_emote_set(SEVENTV_SET)
        self.assertEqual(emotes, {"glorp": "https://cdn.7tv.app/emote/01J94DZYDR0008SGMD5K4B9HFW/2x.webp"})
        stv = SevenTV(session=None)  # type: ignore[arg-type]
        stv._global = emotes
        out = stv.expand([text_frag("hi glorp  there"), {"t": "emote", "name": "Kappa", "url": "u"}])
        self.assertEqual([f["t"] for f in out], ["text", "emote", "text", "emote"])
        self.assertEqual(out[0]["v"], "hi ")
        self.assertEqual(out[2]["v"], "  there")


class YouTubeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pb2, _ = protogen.load()

    def _msg(self, **snippet):  # type: ignore[no-untyped-def]
        pb2 = self.pb2
        msg = pb2.LiveChatMessage(id="yt1")
        msg.author_details.display_name = "@quietstarlight"
        msg.author_details.channel_id = "UC123"
        msg.author_details.is_chat_sponsor = True
        msg.snippet.published_at = "2026-10-30T22:00:00.5Z"
        for key, value in snippet.items():
            setattr(msg.snippet, key, value)
        return msg

    def test_text_message(self) -> None:
        T = self.pb2.LiveChatMessageSnippet.TypeWrapper
        msg = self._msg(type=T.TEXT_MESSAGE_EVENT)
        msg.snippet.text_message_details.message_text = "audio is perfect"
        action, item = map_message(self.pb2, msg)
        self.assertEqual(action, "add")
        self.assertEqual(item.fragments, [text_frag("audio is perfect")])
        self.assertEqual(item.roles, ["member"])

    def test_super_chat(self) -> None:
        T = self.pb2.LiveChatMessageSnippet.TypeWrapper
        msg = self._msg(type=T.SUPER_CHAT_EVENT)
        msg.snippet.super_chat_details.amount_display_string = "$10.00"
        msg.snippet.super_chat_details.user_comment = "snack fund"
        msg.snippet.super_chat_details.tier = 3
        action, item = map_message(self.pb2, msg)
        self.assertEqual((action, item.kind, item.amount, item.tier), ("add", "superchat", "$10.00", 3))

    def test_ban_and_tombstone_and_end(self) -> None:
        T = self.pb2.LiveChatMessageSnippet.TypeWrapper
        ban = self._msg(type=T.USER_BANNED_EVENT)
        ban.snippet.user_banned_details.banned_user_details.channel_id = "UCbad"
        self.assertEqual(map_message(self.pb2, ban), ("ban", "UCbad"))
        ban.snippet.user_banned_details.ban_type = self.pb2.LiveChatUserBannedMessageDetails.BanTypeWrapper.TEMPORARY
        self.assertEqual(map_message(self.pb2, ban), ("timeout", "UCbad"))
        self.assertEqual(map_message(self.pb2, self._msg(type=T.TOMBSTONE)), ("delete", "yt1"))
        self.assertEqual(map_message(self.pb2, self._msg(type=T.CHAT_ENDED_EVENT)), ("ended", None))

    def test_gift_receipts_are_skipped(self) -> None:
        T = self.pb2.LiveChatMessageSnippet.TypeWrapper
        self.assertEqual(map_message(self.pb2, self._msg(type=T.GIFT_MEMBERSHIP_RECEIVED_EVENT)), ("skip", None))


class FakeWS:
    closed = False

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_str(self, text: str) -> None:
        self.sent.append(json.loads(text))


class HubTests(unittest.TestCase):
    def test_add_remove_and_backlog(self) -> None:
        async def scenario() -> FakeWS:
            hub = Hub(backlog=3)
            ws = FakeWS()
            await hub.attach(ws)  # type: ignore[arg-type]
            for i in range(4):
                await hub.add(ChatItem(id=str(i), platform="twitch", kind="chat", author="a", user_id=f"u{i % 2}"))
            await hub.moderate("twitch", "ban", user_id="u1")
            self.assertEqual([m["id"] for m in hub.recent()], ["2"])
            return ws

        ws = asyncio.run(scenario())
        self.assertEqual(ws.sent[0]["op"], "hello")
        self.assertIn("recent_events", ws.sent[0])
        self.assertEqual(ws.sent[-1], {"op": "moderate", "mode": "remove", "platform": "twitch", "user_id": "u1"})


class EventBacklogTests(unittest.TestCase):
    def test_events_survive_chat_flood_and_bans(self) -> None:
        async def scenario() -> Hub:
            hub = Hub(backlog=5)
            await hub.add(ChatItem(id="sc", platform="youtube", kind="superchat", author="a", user_id="fan"))
            await hub.add(ChatItem(id="raid", platform="twitch", kind="raid", author="b", user_id="raider"))
            for i in range(30):  # busy chat pushes events out of the chat backlog
                await hub.add(ChatItem(id=f"c{i}", platform="twitch", kind="chat", author="c", user_id="u"))
            await hub.moderate("twitch", "timeout", user_id="raider")
            return hub

        hub = asyncio.run(scenario())
        self.assertNotIn("sc", [m["id"] for m in hub.recent()])
        self.assertEqual([m["id"] for m in hub.recent_events()], ["sc"])


class MentionTests(unittest.TestCase):
    def setUp(self) -> None:
        from chatmerge import mentions
        self.m = mentions
        self.pat = mentions.build_pattern(["kanekolumi", "Lumi"])

    def _item(self, text: str) -> ChatItem:
        return ChatItem(id="1", platform="twitch", kind="chat", author="a", user_id="a", fragments=[text_frag(text)])

    def test_matches_whole_handles_any_case(self) -> None:
        for text in ("@KanekoLumi hi", "hi @lumi!", "@LUMI's stream", "yo @kanekolumi"):
            self.assertTrue(self.m.apply(self._item(text), self.pat), text)

    def test_ignores_other_handles_and_bare_names(self) -> None:
        for text in ("@Lumi1 hi", "@lumi_fan", "lumi is great", "mail me at x@lumi.com"):
            self.assertFalse(self.m.apply(self._item(text), self.pat), text)

    def test_splits_mention_fragment(self) -> None:
        it = self._item("happy stream day @Lumi!!")
        self.assertTrue(self.m.apply(it, self.pat))
        self.assertEqual(it.fragments, [text_frag("happy stream day "), {"t": "mention", "v": "@Lumi"}, text_frag("!!")])

    def test_hub_flags_line(self) -> None:
        async def run() -> Hub:
            hub = Hub(mention_pattern=self.pat)
            await hub.add(self._item("@kanekolumi look"))
            await hub.add(self._item("no mention here"))
            return hub
        hub = asyncio.run(run())
        self.assertEqual([m["mention"] for m in hub.recent()], [True, False])


class YouTubeTestModeTests(unittest.TestCase):
    def test_parse_video_id(self) -> None:
        from chatmerge.youtube_live import parse_video_id
        vid = "dQw4w9WgXcQ"
        for url in (vid, f"https://www.youtube.com/watch?v={vid}", f"https://youtube.com/watch?v={vid}&t=30s",
                    f"https://youtu.be/{vid}?si=abc", f"youtu.be/{vid}", f"https://www.youtube.com/live/{vid}?feature=share",
                    f"https://m.youtube.com/watch?v={vid}", f"https://www.youtube.com/shorts/{vid}"):
            self.assertEqual(parse_video_id(url), vid, url)
        for bad in ("https://www.youtube.com/@KanekoLumi/live", "https://twitch.tv/kanekolumi", "hello", "",
                    "https://www.youtube.com/watch?v=short"):
            self.assertIsNone(parse_video_id(bad), bad)

    def test_chat_id_extraction(self) -> None:
        from chatmerge.youtube_live import chat_id_from_broadcasts, chat_id_from_videos
        live = {"items": [{"snippet": {"title": "Big stream"}, "liveStreamingDetails": {"activeLiveChatId": "CHAT1"}}]}
        ended = {"items": [{"snippet": {"title": "Old VOD"}, "liveStreamingDetails": {"actualEndTime": "x"}}]}
        self.assertEqual(chat_id_from_videos(live), ("CHAT1", "Big stream"))
        self.assertEqual(chat_id_from_videos(ended), (None, "Old VOD"))
        self.assertEqual(chat_id_from_videos({"items": []}), (None, ""))
        self.assertEqual(chat_id_from_broadcasts({"items": [{"snippet": {"liveChatId": "C2", "title": "Me"}}]}), ("C2", "Me"))


class CustomEmoteAndHideTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        from chatmerge.custom_emotes import CustomEmotes
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        (folder / "_lumiHeart.png").write_bytes(b"x")
        (folder / "notes.txt").write_text("ignore me")
        self.ce = CustomEmotes(folder)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_known_code_becomes_image_unknown_becomes_label(self) -> None:
        out = self.ce.expand([text_frag("hi :_lumiHeart::_nimiHaha: ok")])
        self.assertEqual(out, [text_frag("hi "), {"t": "emote", "name": "_lumiHeart", "url": "/custom/_lumiHeart.png"},
                               {"t": "ytemoji", "v": "nimiHaha"}, text_frag(" ok")])

    def test_code_glued_to_word(self) -> None:
        out = self.ce.expand([text_frag("bro I tried my best:_nimiPls: lol")])
        self.assertEqual(out, [text_frag("bro I tried my best"), {"t": "ytemoji", "v": "nimiPls"}, text_frag(" lol")])

    def test_case_insensitive_and_times_untouched(self) -> None:
        out = self.ce.expand([text_frag(":_LUMIHEART: at 10:30:45, re: hi")])
        self.assertEqual(out[0]["url"], "/custom/_lumiHeart.png")
        self.assertEqual(out[1], text_frag(" at 10:30:45, re: hi"))

    def test_hub_hides_bots_and_only_converts_youtube(self) -> None:
        async def run() -> Hub:
            hub = Hub(settings=OverlaySettings(hide_users=["Nightbot", "@StreamBot"]), custom_emotes=self.ce)
            for author, platform, text in (("nightbot", "twitch", "!discord"), ("@streambot", "youtube", "hi"),
                                           ("viewer", "youtube", ":_lumiHeart:"), ("viewer2", "twitch", ":_lumiHeart:")):
                await hub.add(ChatItem(id=author + platform, platform=platform, kind="chat", author=author,
                                       user_id=author, fragments=[text_frag(text)]))
            return hub
        recent = asyncio.run(run()).recent()
        self.assertEqual([m["author"] for m in recent], ["viewer", "viewer2"])
        self.assertEqual(recent[0]["fragments"][0]["t"], "emote")
        self.assertEqual(recent[1]["fragments"], [text_frag(":_lumiHeart:")])


class PacingTests(unittest.TestCase):
    def test_cycle_lengths(self) -> None:
        from chatmerge.youtube_live import min_connection_cycle_s
        # 8 h on 10,000 units: ~15.2 s between connections (YouTube itself closes them every ~11 s)
        self.assertAlmostEqual(min_connection_cycle_s(10000, 8), 15.16, places=1)
        self.assertAlmostEqual(min_connection_cycle_s(10000, 12), 22.74, places=1)
        # whole-day budget used over the stated hours stays under the quota
        for hours in (4, 8, 12, 24):
            cycle = min_connection_cycle_s(10000, hours)
            self.assertLessEqual(5 * hours * 3600 / cycle, 10000)


class ModerationTests(unittest.TestCase):
    def _hub(self, **kw):  # type: ignore[no-untyped-def]
        hub = Hub(settings=OverlaySettings(**kw))
        ws = FakeWS()
        asyncio.run(hub.attach(ws))  # type: ignore[arg-type]
        return hub, ws

    def _chat(self, i, user="u", text="hello", kind="chat", roles=None, platform="twitch"):  # type: ignore[no-untyped-def]
        return ChatItem(id=i, platform=platform, kind=kind, author=user, user_id=user,
                        fragments=[text_frag(text)], roles=roles or [], headline="Super Chat $10.00" if kind != "chat" else None)

    def test_twitch_timeout_vs_ban(self) -> None:
        from chatmerge.twitch_irc import clearchat_action
        self.assertEqual(clearchat_action({"ban-duration": "600", "target-user-id": "1"}), "timeout")
        self.assertEqual(clearchat_action({"target-user-id": "1"}), "ban")

    def test_per_action_modes(self) -> None:
        hub, ws = self._hub(on_delete="placeholder", on_timeout="keep", on_ban="remove")
        async def run() -> None:
            for i, u in (("1", "a"), ("2", "b"), ("3", "c")):
                await hub.add(self._chat(i, u))
            self.assertEqual(await hub.moderate("twitch", "delete", ids=["1"]), "placeholder")
            self.assertEqual(await hub.moderate("twitch", "timeout", user_id="b"), "keep")
            self.assertEqual(await hub.moderate("twitch", "ban", user_id="c"), "remove")
        asyncio.run(run())
        recent = {m["id"]: m for m in hub.recent()}
        self.assertEqual(set(recent), {"1", "2"})          # ban removed "3"
        self.assertTrue(recent["1"]["removed"])            # delete -> placeholder
        self.assertEqual(recent["1"]["fragments"], [])
        self.assertNotIn("removed", recent["2"])           # timeout -> keep
        ops = [m for m in ws.sent if m["op"] == "moderate"]
        self.assertEqual([m["mode"] for m in ops], ["placeholder", "remove"])  # keep sends nothing
        self.assertEqual(ws.sent[0]["settings"]["removal_style"], "fade")
        self.assertEqual(ws.sent[0]["settings"]["placeholder_text"], "message removed")

    def test_dock_requests(self) -> None:
        hub, ws = self._hub(on_dock_hide="placeholder")
        async def run() -> None:
            await hub.add(self._chat("1", platform="youtube"))
            await hub.add(self._chat("2"))
            await hub.handle_client('{"op":"hide","platform":"youtube","id":"1"}')
            await hub.handle_client('{"op":"hide","platform":"evil","id":"2"}')   # rejected
            await hub.handle_client('not json')                                    # ignored
            await hub.handle_client('{"op":"clear_overlay"}')
        asyncio.run(run())
        ops = [m for m in ws.sent if m["op"] == "moderate"]
        self.assertEqual(ops[0], {"op": "moderate", "mode": "placeholder", "platform": "youtube", "ids": ["1"]})
        self.assertEqual(ops[1], {"op": "moderate", "mode": "remove", "platform": "*"})
        self.assertEqual(len(ops), 2)
        self.assertEqual(hub.recent(), [])

    def test_blocked_words_links_exemptions_and_paid_events(self) -> None:
        hub, _ = self._hub(blocked_words=["badword", "two words"], block_links=True)
        async def run() -> None:
            await hub.add(self._chat("1", text="this has BadWord in it"))
            await hub.add(self._chat("2", text="badwords is a different word"))
            await hub.add(self._chat("3", text="say two words here"))
            await hub.add(self._chat("4", text="go to example.com/x now"))
            await hub.add(self._chat("5", text="https://patreon.com/me", roles=["mod"]))
            await hub.add(self._chat("6", text="badword", kind="superchat", platform="youtube"))
        asyncio.run(run())
        recent = {m["id"]: m for m in hub.recent()}
        self.assertEqual(set(recent), {"2", "5", "6"})
        self.assertEqual(recent["6"]["fragments"], [])          # paid: text dropped
        self.assertEqual(recent["6"]["headline"], "Super Chat $10.00")  # headline kept

    def test_config_validation(self) -> None:
        from chatmerge.moderation import ModerationSettings
        self.assertEqual(ModerationSettings().validate(), [])
        bad = ModerationSettings(on_ban="explode", on_dock_hide="keep", removal_style="slow", placeholder_text=" ")
        self.assertEqual(len(bad.validate()), 4)
        self.assertEqual(len(ModerationSettings(overlay_audience="vips").validate()), 1)


if __name__ == "__main__":
    unittest.main()
