"""YouTube channel emojis arrive only as text codes like :_lumiHeart: (the API never
sends the image). Codes with a matching image file in the emotes folder render as
images; any other code becomes a tidy label instead of raw :_code: text."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from urllib.parse import quote

from .models import emote_frag, text_frag

log = logging.getLogger("chatmerge.emotes")

IMAGE_EXTS = (".png", ".gif", ".webp", ".jpg", ".jpeg", ".avif")
# :name: where name starts with a letter or underscore, so times like 10:30:45 never match.
# No lookbehind: YouTube glues codes straight onto words ("best:_nimiPls:").
CODE = re.compile(r":([A-Za-z_][A-Za-z0-9_-]*):")
URL_PREFIX = "/custom/"


class CustomEmotes:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self._files: dict[str, str] = {}
        self._lower: dict[str, str] = {}
        self._mtime: float | None = None

    def _refresh(self) -> None:
        """Rescan only when the folder changed (adding or removing a file updates its mtime)."""
        try:
            mtime = self.folder.stat().st_mtime
        except FileNotFoundError:
            self._files, self._lower, self._mtime = {}, {}, None
            return
        if mtime == self._mtime:
            return
        files: dict[str, str] = {}
        for path in self.folder.iterdir():
            if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
                files.setdefault(path.stem, path.name)
        self._files = files
        self._lower = {stem.lower(): name for stem, name in files.items()}
        self._mtime = mtime
        log.info('custom emotes loaded from "%s": %d', self.folder, len(files))

    def lookup(self, code: str) -> str | None:
        self._refresh()
        name = self._files.get(code) or self._lower.get(code.lower())
        return URL_PREFIX + quote(name) if name else None

    def expand(self, fragments: list[dict[str, str]]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for frag in fragments:
            if frag["t"] != "text":
                out.append(frag)
                continue
            text = frag["v"]
            cursor = 0
            for match in CODE.finditer(text):
                if match.start() > cursor:
                    out.append(text_frag(text[cursor : match.start()]))
                code = match.group(1)
                url = self.lookup(code)
                out.append(emote_frag(code, url) if url else {"t": "ytemoji", "v": code.lstrip("_")})
                cursor = match.end()
            if cursor < len(text):
                out.append(text_frag(text[cursor:]))
        return out
