"""Record raw chat while running, and replay it later through the exact same code paths.

A recording is JSON Lines: one header line, then one line per raw Twitch IRC line or
YouTube LiveChatMessage (protobuf bytes, base64). Recordings hold real viewers' names and
messages: keep them on this PC (the recordings folder is never zipped or committed).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

log = logging.getLogger("chatmerge.recorder")

FORMAT = "chatmerge-recording"
FORMAT_VERSION = 1


class RecordingError(ValueError):
    pass


class Recorder:
    def __init__(self, path: Path, app_version: str, twitch_channel: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._t0 = time.monotonic()
        self._fh: IO[str] = path.open("x", encoding="utf-8", buffering=1)  # "x": never overwrite
        self.count = 0
        self._write({
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": app_version,
            "twitch_channel": twitch_channel,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    @staticmethod
    def new_path(folder: Path) -> Path:
        return folder / f"chat_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.jsonl"

    def _write(self, record: dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def twitch(self, line: str) -> None:
        self._write({"t": round(time.monotonic() - self._t0, 3), "src": "twitch", "line": line})
        self.count += 1

    def youtube(self, raw: bytes) -> None:
        self._write({"t": round(time.monotonic() - self._t0, 3), "src": "youtube",
                     "pb": base64.b64encode(raw).decode("ascii")})
        self.count += 1

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()
            log.info('recording saved: "%s" (%d lines)', self.path, self.count)


@dataclass(slots=True)
class Entry:
    t: float
    src: str
    data: str | bytes


def read_recording(path: Path) -> tuple[dict[str, Any], Iterator[Entry]]:
    """(header, entries). Raises RecordingError with the line number on bad input."""
    if not path.is_file():
        raise RecordingError(f'Recording not found: "{path}"')
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise RecordingError(f'Recording is empty: "{path}"')
    try:
        header = json.loads(lines[0])
    except ValueError as exc:
        raise RecordingError(f'"{path}" line 1 is not JSON: {exc}') from exc
    if header.get("format") != FORMAT:
        raise RecordingError(f'"{path}" is not a chatmerge recording')
    if header.get("format_version") != FORMAT_VERSION:
        raise RecordingError(f'"{path}" is recording format {header.get("format_version")}; expected {FORMAT_VERSION}')

    def entries() -> Iterator[Entry]:
        for number, raw in enumerate(lines[1:], start=2):
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
                if rec["src"] == "twitch":
                    yield Entry(float(rec["t"]), "twitch", str(rec["line"]))
                elif rec["src"] == "youtube":
                    yield Entry(float(rec["t"]), "youtube", base64.b64decode(rec["pb"], validate=True))
                else:
                    raise RecordingError(f'"{path}" line {number}: unknown source {rec["src"]!r}')
            except (ValueError, KeyError, TypeError) as exc:
                raise RecordingError(f'"{path}" line {number}: {exc}') from exc

    return header, entries()


async def replay(
    entries: Iterator[Entry],
    on_twitch: Callable[[str], Awaitable[None]],
    on_youtube: Callable[[bytes], Awaitable[None]],
    speed: float = 1.0,
) -> int:
    """Feed entries with their original timing divided by speed (0 = as fast as possible)."""
    start = time.monotonic()
    count = 0
    for entry in entries:
        if speed > 0:
            due = start + entry.t / speed
            delay = due - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        if entry.src == "twitch":
            await on_twitch(str(entry.data))
        else:
            await on_youtube(bytes(entry.data))
        count += 1
    return count
