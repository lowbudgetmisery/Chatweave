"""7TV emotes: global set plus the Twitch channel's active set (7TV REST v3)."""
from __future__ import annotations

import logging
import re
from typing import Any

import aiohttp

from .models import emote_frag, text_frag

log = logging.getLogger("chatmerge.7tv")

GLOBAL_URL = "https://7tv.io/v3/emote-sets/global"
USER_URL = "https://7tv.io/v3/users/twitch/{twitch_id}"
_TOKEN_SPLIT = re.compile(r"(\s+)")


def parse_emote_set(payload: dict[str, Any]) -> dict[str, str]:
    """Map emote name (the alias used in chat) to a 2x WEBP URL."""
    out: dict[str, str] = {}
    for emote in payload.get("emotes") or []:
        name = emote.get("name")
        host = (emote.get("data") or {}).get("host") or {}
        base = host.get("url")
        if not name or not base:
            continue
        files = {f.get("name") for f in host.get("files") or []}
        file_name = "2x.webp" if "2x.webp" in files else "1x.webp"
        url = base if base.startswith("http") else "https:" + base
        out[name] = f"{url}/{file_name}"
    return out


class SevenTV:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._global: dict[str, str] = {}
        self._channel: dict[str, str] = {}

    @property
    def count(self) -> int:
        return len(self._global) + len(self._channel)

    async def load_global(self) -> None:
        data = await self._get_json(GLOBAL_URL)
        if data is not None:
            self._global = parse_emote_set(data)
            log.info("7TV global emotes loaded: %d", len(self._global))

    async def load_channel(self, twitch_id: str) -> None:
        data = await self._get_json(USER_URL.format(twitch_id=twitch_id))
        if data is None:
            return
        emote_set = data.get("emote_set") or {}
        self._channel = parse_emote_set(emote_set)
        log.info("7TV channel emotes loaded: %d", len(self._channel))

    async def _get_json(self, url: str) -> dict[str, Any] | None:
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 404:
                    log.warning("7TV has no emote set at %s (404)", url)
                    return None
                if resp.status != 200:
                    log.error("7TV request failed: HTTP %s for %s", resp.status, url)
                    return None
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.error("7TV request error for %s: %s", url, exc)
            return None

    def lookup(self, word: str) -> str | None:
        return self._channel.get(word) or self._global.get(word)

    def expand(self, fragments: list[dict[str, str]]) -> list[dict[str, str]]:
        """Split text fragments on whitespace and swap 7TV emote words for images."""
        if not self.count:
            return fragments
        out: list[dict[str, str]] = []
        for frag in fragments:
            if frag["t"] != "text":
                out.append(frag)
                continue
            buffer = ""
            for token in _TOKEN_SPLIT.split(frag["v"]):
                url = self.lookup(token) if token and not token.isspace() else None
                if url:
                    if buffer:
                        out.append(text_frag(buffer))
                        buffer = ""
                    out.append(emote_frag(token, url))
                else:
                    buffer += token
            if buffer:
                out.append(text_frag(buffer))
        return out
