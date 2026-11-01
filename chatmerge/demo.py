"""Offline demo feed for positioning the overlay in OBS. Every item here is fake."""
from __future__ import annotations

import asyncio
import itertools
import uuid

from .hub import Hub
from .models import ChatItem, emote_frag, text_frag

STAR = "/static/demo_star.svg"
HAT = "/static/demo_hat.svg"  # stands in for a zero-width emote like BTTV TopHat

SCRIPT: list[dict] = [
    {"platform": "twitch", "author": "NightOwl_88", "text": "first time catching the stream live, hi everyone"},
    {"platform": "youtube", "author": "@orbitalnoodle", "text": "audio is perfect tonight", "roles": ["member"]},
    {"platform": "twitch", "author": "comet_kid", "text": "LET'S GO", "emote": True},
    {"platform": "youtube", "author": "@quietstarlight", "kind": "superchat", "amount": "$10.00",
     "headline": "Super Chat $10.00", "text": "for the snack fund, keep going", "tier": 3},
    {"platform": "twitch", "author": "Lunarbyte", "text": "the boss is going to cheese you again isn't it"},
    {"platform": "twitch", "author": "pixelmoth", "kind": "sub",
     "headline": "pixelmoth subscribed at Tier 1. They've subscribed for 7 months!", "text": "seven months already"},
    {"platform": "youtube", "author": "@driftwoodfm", "text": "scheduling posts while offline is how arguments start"},
    {"platform": "twitch", "author": "StarchyBean", "kind": "bits", "headline": "Cheered 500 bits", "amount": "500",
     "text": "Cheer500 for the redemption arc"},
    {"platform": "twitch", "author": "Moderato", "text": "reminder: be nice in chat", "roles": ["mod"]},
    {"platform": "youtube", "author": "@velvetcosmos", "kind": "member", "headline": "New member: Stargazer"},
    {"platform": "twitch", "author": "HollowSun", "kind": "raid",
     "headline": "42 raiders from HollowSun have joined!", "amount": "42"},
    {"platform": "twitch", "author": "glimmerfox", "text": "that was so clean", "emote": True},
    {"platform": "twitch", "author": "Raccomi3", "text": "@kanekolumi you have to try the new map"},
    {"platform": "youtube", "author": "@Lemons2", "text": "happy stream day @Lumi!!"},
    {"platform": "twitch", "author": "Kaneko4", "text": "@Raccomi3 the cave map is way better",
     "reply_to": {"name": "Raccomi3", "text": "@kanekolumi you have to try the new map"}},
    {"platform": "twitch", "author": "NewStar7", "text": "hi! just found this stream", "first_time": True},
    {"platform": "twitch", "author": "Orbit8", "text": "hello from the other chat", "shared": True},
    {"platform": "twitch", "author": "Moderato", "kind": "announcement", "headline": "Announcement", "accent": "purple",
     "text": "karaoke requests open after this song!", "roles": ["mod"]},
    {"platform": "twitch", "author": "glimmerfox", "kind": "milestone",
     "headline": "glimmerfox watched 25 consecutive streams and sparked a watch streak!", "text": "25 in a row!"},
    # Hidden from chat by default (!commands); they play Gliese Heist when [game] is on.
    {"platform": "twitch", "author": "Kaneko4", "text": "!heist"},
    {"platform": "youtube", "author": "@Lemons2", "text": "!heist"},
    {"platform": "twitch", "author": "Raccomi3", "text": "!steal"},
    {"platform": "twitch", "author": "Lumi1", "text": "!stash"},
    # How 7TV / BTTV / FFZ extras look: a zero-width hat stacked on an emote, and p! s! (party, shake)
    {"platform": "twitch", "author": "Kaneko4", "frags": [
        text_frag("hat on "), emote_frag("starPog", STAR), {"t": "emote", "name": "TopHat", "url": HAT, "zw": True}]},
    {"platform": "twitch", "author": "Raccomi3", "frags": [
        text_frag("party time "), {"t": "emote", "name": "starPog", "url": STAR, "fx": ["party", "shake"]}]},
]


def _item(spec: dict) -> ChatItem:
    frags = list(spec["frags"]) if spec.get("frags") else [text_frag(spec["text"])] if spec.get("text") else []
    if spec.get("emote"):
        frags = frags + [text_frag(" "), emote_frag("starPog", STAR)]
    return ChatItem(
        id=uuid.uuid4().hex,
        platform=spec["platform"],
        kind=spec.get("kind", "chat"),
        author=spec["author"],
        user_id=spec["author"].lower(),
        fragments=frags,
        roles=spec.get("roles", []),
        headline=spec.get("headline"),
        amount=spec.get("amount"),
        tier=spec.get("tier"),
        reply_to=spec.get("reply_to"),
        first_time=spec.get("first_time", False),
        shared=spec.get("shared", False),
        accent=spec.get("accent"),
    )


async def run_demo(hub: Hub, interval_s: float = 1.6, count: int | None = None) -> None:
    await hub.set_status("twitch", "demo feed")
    await hub.set_status("youtube", "demo feed")
    specs = itertools.cycle(SCRIPT) if count is None else iter(SCRIPT[:count])
    for spec in specs:
        await hub.add(_item(spec))
        await asyncio.sleep(interval_s)
