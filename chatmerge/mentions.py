"""Find @mentions of the streamer so those lines can stand out."""
from __future__ import annotations

import re
from collections.abc import Iterable

from .models import ChatItem, text_frag


def build_pattern(names: Iterable[str]) -> re.Pattern[str] | None:
    """Match @name for any listed name, case-insensitive, as a whole handle.

    '@Lumi!' and "@lumi's" match 'lumi'; '@lumi_fan' and '@Lumi1' do not.
    """
    cleaned = sorted({n.strip().lstrip("@").lower() for n in names if n.strip().lstrip("@")}, key=len, reverse=True)
    if not cleaned:
        return None
    alternation = "|".join(re.escape(n) for n in cleaned)
    return re.compile(rf"(?<![\w@])@(?:{alternation})(?!\w)", re.IGNORECASE)


def apply(item: ChatItem, pattern: re.Pattern[str] | None) -> bool:
    """Split matching @names into 'mention' fragments. Returns True if any matched."""
    if pattern is None:
        return False
    found = False
    out: list[dict[str, str]] = []
    for frag in item.fragments:
        if frag["t"] != "text":
            out.append(frag)
            continue
        text = frag["v"]
        cursor = 0
        for match in pattern.finditer(text):
            found = True
            if match.start() > cursor:
                out.append(text_frag(text[cursor : match.start()]))
            out.append({"t": "mention", "v": match.group(0)})
            cursor = match.end()
        if cursor < len(text):
            out.append(text_frag(text[cursor:]))
    if found:
        item.fragments = out
    return found
