"""7TV, BTTV and FFZ: parsing the real API shapes (checked Oct 2026), lookup order, zero-width
stacking and BTTV/FFZ emote modifiers. Offline."""
from __future__ import annotations

import asyncio
import unittest
from typing import Any

from chatmerge.emotes import (
    Emote,
    ThirdPartyEmotes,
    parse_7tv_set,
    parse_bttv_list,
    parse_bttv_user,
    parse_ffz_global,
    parse_ffz_room,
)
from chatmerge.models import emote_frag, text_frag

SEVENTV_SET = {"emotes": [
    {"name": "glorp", "flags": 0,
     "data": {"flags": 0, "host": {"url": "//cdn.7tv.app/emote/01J94DZYDR0008SGMD5K4B9HFW",
                                   "files": [{"name": "1x.webp"}, {"name": "2x.webp"}]}}},
    {"name": "RainTime", "flags": 1,
     "data": {"flags": 256, "host": {"url": "//cdn.7tv.app/emote/01RAIN", "files": [{"name": "2x.webp"}]}}},
    {"name": "broken", "data": {}},
    {"name": "elsewhere", "data": {"host": {"url": "//evil.example/emote/1", "files": []}}},
]}
BTTV_GLOBAL = [
    {"id": "54fa8f1401e468494b85b537", "code": ":tf:", "imageType": "png", "animated": False,
     "userId": "5561169bd6b9d206222a8c19", "modifier": False},
    {"id": "5e76d399d6581c3724c0f0b8", "code": "cvMask", "imageType": "png", "animated": False,
     "userId": "54ee2465b822020506c52a52", "modifier": False},
    {"id": "64b9c4a3a5e7ae1bd3fbb1d0", "code": "h!", "imageType": "png", "animated": False,
     "userId": "5561169bd6b9d206222a8c19", "modifier": True},
]
BTTV_USER = {
    "id": "x", "bots": [], "avatar": "",
    "channelEmotes": [{"id": "63777dd4b9076d0aaebc5aa4", "code": "LumiHat", "imageType": "png", "animated": False,
                       "userId": "6198bf472e25bbb9d8baa113", "width": 43, "height": 28}],
    "sharedEmotes": [{"id": "54fcdfdc01abde735115deae", "code": "RIP", "imageType": "png", "animated": False,
                      "user": {"id": "54f988078edd5fcd455f2276", "name": "onslaught", "displayName": "Onslaught",
                               "providerId": "59206825"}},
                     {"id": "aaaaaaaaaaaaaaaaaaaaaaaa", "code": "LumiHat", "imageType": "png", "animated": False}],
}


def ffz_emote(eid: int, name: str, modifier: bool = False, flags: int = 0, animated: bool = False) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": eid, "name": name, "height": 28, "width": 28, "public": True, "hidden": False,
                             "modifier": modifier, "modifier_flags": flags, "offset": None, "margins": None,
                             "urls": {"1": f"https://cdn.frankerfacez.com/emote/{eid}/1",
                                      "2": f"https://cdn.frankerfacez.com/emote/{eid}/2",
                                      "4": f"https://cdn.frankerfacez.com/emote/{eid}/4"}}
    if animated:
        entry["animated"] = {k: f"https://cdn.frankerfacez.com/emote/{eid}/animated/{k}.webp" for k in ("1", "2", "4")}
    return entry


FFZ_GLOBAL = {
    "default_sets": [3, 1539687],
    "sets": {
        "3": {"id": 3, "title": "Global Emotes", "emoticons": [ffz_emote(25927, "CatBag")]},
        "1539687": {"id": 1539687, "title": "Emote Effects", "emoticons": [
            ffz_emote(720507, "ffzW", True, 1 | 8), ffz_emote(720508, "ffzX", True, 1 | 2),
            ffz_emote(720509, "ffzY", True, 1 | 4), ffz_emote(720510, "ffzCursed", True, 1 | 16384)]},
        "1532818": {"id": 1532818, "title": "Subwoofer Emote Effects", "emoticons": [
            ffz_emote(720400, "ffzHyper", True, 12289), ffz_emote(720401, "ffzRainbow", True, 2049)]},
    },
    "users": {"1532818": ["SubwooferFan"]},
}
FFZ_ROOM = {
    "room": {"_id": 1293562, "twitch_id": 694413391, "id": "kanekolumi", "set": 1293584},
    "sets": {"1293584": {"id": 1293584, "title": "Channel: KanekoLumi", "emoticons": [
        ffz_emote(47399, "YouDied"), ffz_emote(157576, "monkaMEGA"), ffz_emote(1, "PartyCat", animated=True)]}},
}


def view(frags: list[dict[str, Any]]) -> list[tuple]:
    return [(f["t"], f.get("v") or f.get("name"), tuple(f.get("fx", ())), bool(f.get("zw"))) for f in frags]


class ParseTests(unittest.TestCase):
    def test_7tv(self) -> None:
        table = parse_7tv_set(SEVENTV_SET)
        self.assertEqual(sorted(table), ["RainTime", "glorp"])  # broken and off-CDN entries dropped
        self.assertEqual(table["glorp"].url, "https://cdn.7tv.app/emote/01J94DZYDR0008SGMD5K4B9HFW/2x.webp")
        self.assertEqual((table["glorp"].zero_width, table["RainTime"].zero_width), (False, True))

    def test_bttv(self) -> None:
        table = parse_bttv_list(BTTV_GLOBAL)
        self.assertEqual(sorted(table), [":tf:", "cvMask"])  # the h! modifier entry is text, not an image
        self.assertEqual(table[":tf:"].url, "https://cdn.betterttv.net/emote/54fa8f1401e468494b85b537/2x.webp")
        self.assertTrue(table["cvMask"].zero_width)
        user = parse_bttv_user(BTTV_USER)
        self.assertEqual(sorted(user), ["LumiHat", "RIP"])
        self.assertIn("63777dd4b9076d0aaebc5aa4", user["LumiHat"].url)  # her own emote beats a shared one

    def test_ffz(self) -> None:
        table = parse_ffz_global(FFZ_GLOBAL)
        self.assertEqual(table["ffzW"].effects, ("wide",))
        self.assertTrue(table["ffzW"].hidden)
        self.assertEqual(table["ffzCursed"].effects, ("cursed",))
        self.assertEqual(table["ffzHyper"].effects, ("hyper-red", "hyper-shake"))
        self.assertEqual(table["ffzHyper"].users, frozenset({"subwooferfan"}))  # supporter-only set
        self.assertIsNone(table["CatBag"].users)
        room = parse_ffz_room(FFZ_ROOM)
        self.assertEqual(room["YouDied"].url, "https://cdn.frankerfacez.com/emote/47399/2")
        self.assertEqual(room["PartyCat"].url, "https://cdn.frankerfacez.com/emote/1/animated/2.webp")


class ExpandTests(unittest.TestCase):
    def setUp(self) -> None:
        e = ThirdPartyEmotes(None)
        e._t.global_ = {"7tv": parse_7tv_set(SEVENTV_SET), "bttv": parse_bttv_list(BTTV_GLOBAL),
                        "ffz": parse_ffz_global(FFZ_GLOBAL)}
        e._t.channel = {"bttv": parse_bttv_user(BTTV_USER), "ffz": parse_ffz_room(FFZ_ROOM),
                        "7tv": {"RIP": Emote("RIP", "https://cdn.7tv.app/emote/RIP7/2x.webp", "7tv")}}
        self.e = e

    def run_text(self, text: str, login: str = "") -> list[tuple]:
        return view(self.e.expand([text_frag(text)], login))

    def test_all_three_and_priority(self) -> None:
        self.assertEqual(self.run_text("glorp LumiHat YouDied CatBag hi"), [
            ("emote", "glorp", (), False), ("text", " ", (), False), ("emote", "LumiHat", (), False),
            ("text", " ", (), False), ("emote", "YouDied", (), False), ("text", " ", (), False),
            ("emote", "CatBag", (), False), ("text", " hi", (), False)])
        rip = self.e.expand([text_frag("RIP")])[0]
        self.assertTrue(rip["url"].startswith("https://cdn.7tv.app/"))  # 7TV channel beats BTTV channel
        self.assertEqual(self.run_text("glorpy :tf:x"), [("text", "glorpy :tf:x", (), False)])  # whole words only

    def test_zero_width(self) -> None:
        self.assertEqual(self.run_text("LumiHat cvMask RainTime ok"), [
            ("emote", "LumiHat", (), False), ("emote", "cvMask", (), True), ("emote", "RainTime", (), True),
            ("text", " ok", (), False)])
        self.assertEqual(self.run_text("cvMask first"), [("emote", "cvMask", (), False), ("text", " first", (), False)])
        self.assertEqual(self.run_text("hi cvMask"), [("text", "hi ", (), False), ("emote", "cvMask", (), False)])

    def test_bttv_prefix_modifiers(self) -> None:
        self.assertEqual(self.run_text("h! w! LumiHat yes"), [
            ("emote", "LumiHat", ("flip-h", "wide"), False), ("text", " yes", (), False)])
        self.assertEqual(self.run_text("glorp z! glorp"), [
            ("emote", "glorp", (), False), ("emote", "glorp", ("zero-space",), False)])
        self.assertEqual(self.run_text("h! hello"), [("text", "h! hello", (), False)])  # no emote: stays text
        self.assertEqual(self.run_text("so h!"), [("text", "so h!", (), False)])

    def test_ffz_suffix_modifiers_and_supporter_sets(self) -> None:
        self.assertEqual(self.run_text("YouDied ffzW ffzY lol"), [
            ("emote", "YouDied", ("wide", "flip-v"), False), ("text", " lol", (), False)])
        self.assertEqual(self.run_text("ffzW alone"), [("text", "ffzW alone", (), False)])
        # ffzHyper is in a supporter set: only works for the people FFZ lists
        self.assertEqual(self.run_text("glorp ffzHyper"), [("emote", "glorp", (), False), ("text", " ffzHyper", (), False)])
        self.assertEqual(self.run_text("glorp ffzHyper", login="subwooferfan"),
                         [("emote", "glorp", ("hyper-red", "hyper-shake"), False)])
        # modifiers change the base emote, not an overlay stacked on it
        self.assertEqual(self.run_text("LumiHat RainTime ffzX"), [
            ("emote", "LumiHat", ("flip-h",), False), ("emote", "RainTime", (), True)])

    def test_twitch_emotes_take_part(self) -> None:
        frags = [emote_frag("Kappa", "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/2.0"),
                 text_frag(" ffzX cvMask and Kappa")]
        self.assertEqual(view(self.e.expand(frags)), [
            ("emote", "Kappa", ("flip-h",), False), ("emote", "cvMask", (), True), ("text", " and Kappa", (), False)])

    def test_bttv_suffix_words_without_ffz_data(self) -> None:
        e = ThirdPartyEmotes(None)
        e._t.global_ = {"bttv": parse_bttv_list(BTTV_GLOBAL)}
        self.assertEqual(view(e.expand([text_frag(":tf: ffzCursed")])), [("emote", ":tf:", ("cursed",), False)])

    def test_one_addon_down_keeps_the_others(self) -> None:
        e = ThirdPartyEmotes(None)
        e._t.global_ = {"bttv": parse_bttv_list(BTTV_GLOBAL)}

        async def fail(provider: str, url: str, missing_ok: bool = False) -> None:
            return None

        e._get_json = fail  # type: ignore[method-assign]
        asyncio.run(e.load_global())
        self.assertEqual(e.counts()["bttv"], 2)  # a failed reload never wipes the last good list

    def test_channel_without_an_account(self) -> None:
        e = ThirdPartyEmotes(None)
        e._t.channel = {"ffz": parse_ffz_room(FFZ_ROOM)}

        async def missing(provider: str, url: str, missing_ok: bool = False) -> dict:
            return {}

        e._get_json = missing  # type: ignore[method-assign]
        asyncio.run(e.load_channel("1"))
        self.assertEqual(e.counts(), {"7tv": 0, "bttv": 0, "ffz": 0})  # 404: that add-on isn't used there


if __name__ == "__main__":
    unittest.main()
