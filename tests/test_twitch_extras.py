"""1.8: Twitch replies, first-time chatters, Shared Chat, chat modes; YouTube members-only mode."""
from __future__ import annotations

import asyncio
import json
import unittest

from chatmerge import protogen
from chatmerge.hub import Hub
from chatmerge.settings import OverlaySettings
from chatmerge.twitch_irc import TwitchChat, describe_room_modes, item_from_privmsg, item_from_usernotice, parse_line

CH = "#kanekolumi"
REPLY = ("@badges=;display-name=Kaneko4;emotes=;first-msg=0;id=r1;reply-parent-display-name=Raccomi3;"
         "reply-parent-msg-body=you\\shave\\sto\\stry\\sthe\\snew\\smap;reply-parent-msg-id=p1;reply-parent-user-id=3;"
         f"reply-parent-user-login=raccomi3;room-id=100;user-id=4 :kaneko4!k@k.tmi.twitch.tv PRIVMSG {CH} :@Raccomi3 cave map is better")
FIRST = f"@badges=;display-name=NewStar7;emotes=;first-msg=1;id=f1;room-id=100;user-id=7 :newstar7!n@n.tmi.twitch.tv PRIVMSG {CH} :hi!"
PARTNER = ("@badges=;display-name=Orbit8;emotes=;id=local1;room-id=100;source-id=s1;source-room-id=200;user-id=8 "
           f":orbit8!o@o.tmi.twitch.tv PRIVMSG {CH} :hello from the other chat")
OWN_IN_SESSION = ("@badges=;display-name=Lumi1;emotes=;id=own1;room-id=100;source-id=own1;source-room-id=100;user-id=1 "
                  f":lumi1!l@l.tmi.twitch.tv PRIVMSG {CH} :still my chat")
PARTNER_SUB = ("@badges=;display-name=Orbit8;emotes=;id=n9;login=orbit8;msg-id=sharedchatnotice;source-msg-id=sub;"
               f"room-id=100;source-room-id=200;system-msg=Orbit8\\ssubscribed;user-id=8 :tmi.twitch.tv USERNOTICE {CH}")
OWN_SUB = ("@badges=;display-name=Halo5;emotes=;id=n1;login=halo5;msg-id=sub;room-id=100;"
           f"system-msg=Halo5\\ssubscribed;user-id=5 :tmi.twitch.tv USERNOTICE {CH}")


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


class ParseTests(unittest.TestCase):
    def test_reply(self) -> None:
        item = item_from_privmsg(parse_line(REPLY))
        self.assertEqual(item.reply_to, {"name": "Raccomi3", "text": "you have to try the new map"})
        self.assertFalse(item.first_time)
        self.assertFalse(item.shared)

    def test_long_reply_is_shortened(self) -> None:
        line = REPLY.replace("you\\shave\\sto\\stry\\sthe\\snew\\smap", "\\s".join(["word"] * 40))
        text = item_from_privmsg(parse_line(line)).reply_to["text"]  # type: ignore[index]
        self.assertLessEqual(len(text), 80)
        self.assertTrue(text.endswith("…"))

    def test_first_message(self) -> None:
        self.assertTrue(item_from_privmsg(parse_line(FIRST)).first_time)

    def test_shared_chat_lines(self) -> None:
        partner = item_from_privmsg(parse_line(PARTNER))
        self.assertTrue(partner.shared)
        self.assertEqual(partner.source_id, "s1")
        own = item_from_privmsg(parse_line(OWN_IN_SESSION))
        self.assertFalse(own.shared)  # her own chat during a collab session

    def test_shared_chat_notice(self) -> None:
        item = item_from_usernotice(parse_line(PARTNER_SUB))
        assert item is not None
        self.assertEqual((item.kind, item.shared, item.headline), ("sub", True, "Orbit8 subscribed"))

    def test_mode_text(self) -> None:
        self.assertEqual(describe_room_modes({"subs-only": "0", "slow": "0", "followers-only": "-1"}), "")
        self.assertEqual(describe_room_modes({"subs-only": "1", "emote-only": "1", "followers-only": "10",
                                              "slow": "30", "r9k": "1"}),
                         "sub-only, emote-only, followers-only (10 min), slow 30s, unique-chat")
        self.assertEqual(describe_room_modes({"followers-only": "0"}), "followers-only")


class FlowTests(unittest.TestCase):
    def _run(self, lines: list[str], settings: OverlaySettings | None = None) -> tuple[Hub, list[dict]]:
        hub = Hub(settings=settings)
        sent: list[dict] = []

        class WS:
            closed = False

            async def send_str(self, text: str) -> None:
                sent.append(json.loads(text))

        async def go() -> None:
            await hub.attach(WS())  # type: ignore[arg-type]
            chat = TwitchChat("kanekolumi", hub, None, FakeEmotes())  # type: ignore[arg-type]
            for line in lines:
                await chat.handle_line(NoSocket(), line)  # type: ignore[arg-type]

        asyncio.run(go())
        return hub, sent

    def test_room_modes_in_status(self) -> None:
        join = f"@emote-only=0;followers-only=-1;r9k=0;room-id=100;slow=0;subs-only=0 :tmi.twitch.tv ROOMSTATE {CH}"
        hub, _ = self._run([join])
        self.assertNotIn("twitch chat", hub.status)
        hub, _ = self._run([
            join,
            f"@room-id=100;subs-only=1 :tmi.twitch.tv ROOMSTATE {CH}",
            f"@room-id=100;slow=30 :tmi.twitch.tv ROOMSTATE {CH}",
            "@room-id=200;emote-only=1 :tmi.twitch.tv ROOMSTATE #partnerchannel",  # collab partner: ignored
        ])
        self.assertEqual(hub.status["twitch chat"], "sub-only, slow 30s")
        self.assertEqual(hub.status["twitch"], "joined #kanekolumi")
        hub, _ = self._run([join, f"@room-id=100;subs-only=1 :tmi.twitch.tv ROOMSTATE {CH}",
                            f"@room-id=100;subs-only=0 :tmi.twitch.tv ROOMSTATE {CH}"])
        self.assertNotIn("twitch chat", hub.status)  # back to normal: entry removed

    def test_shared_chat_label_hide_events_and_deletes(self) -> None:
        hub, sent = self._run([PARTNER, OWN_IN_SESSION, PARTNER_SUB, OWN_SUB])
        self.assertEqual([m["id"] for m in hub.recent()], ["local1", "own1", "n9", "n1"])
        self.assertEqual([m["id"] for m in hub.recent_events()], ["n1"])  # partner's sub not in her events window
        # a delete that names the partner channel's id still removes the copy in her chat
        hub, sent = self._run([PARTNER, OWN_IN_SESSION, f"@target-msg-id=s1 :tmi.twitch.tv CLEARMSG {CH} :hello"])
        self.assertEqual([m["id"] for m in hub.recent()], ["own1"])
        hub, _ = self._run([PARTNER, OWN_IN_SESSION, PARTNER_SUB], OverlaySettings(shared_chat="hide"))
        self.assertEqual([m["id"] for m in hub.recent()], ["own1"])


class YouTubeMembersOnlyTests(unittest.TestCase):
    def test_mode_events(self) -> None:
        from chatmerge.youtube_live import YouTubeDispatcher, map_message

        pb2, _ = protogen.load()
        T = pb2.LiveChatMessageSnippet.TypeWrapper
        on = pb2.LiveChatMessage(id="m1")
        on.snippet.type = T.SPONSOR_ONLY_MODE_STARTED_EVENT
        off = pb2.LiveChatMessage(id="m2")
        off.snippet.type = T.SPONSOR_ONLY_MODE_ENDED_EVENT
        self.assertEqual(map_message(pb2, on), ("members_only", True))
        self.assertEqual(map_message(pb2, off), ("members_only", False))

        hub = Hub()
        dispatcher = YouTubeDispatcher(hub, pb2)
        asyncio.run(dispatcher.dispatch(on))
        self.assertEqual(hub.status["youtube chat"], "members-only")
        asyncio.run(dispatcher.dispatch(off))
        self.assertNotIn("youtube chat", hub.status)


class LookupMessageTests(unittest.TestCase):
    def test_plain_language(self) -> None:
        from chatmerge.youtube_live import lookup_failure_text

        text = lookup_failure_text(403, "liveStreamingNotEnabled")
        self.assertIn("YouTube Login.bat", text)
        self.assertIn("liveStreamingNotEnabled", text)  # the exact code stays for troubleshooting
        self.assertEqual(lookup_failure_text(500, "backendError"), "lookup failed: HTTP 500 backendError")


if __name__ == "__main__":
    unittest.main()
