"""Local web server: serves the overlay page and pushes chat over a websocket."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from . import __version__
from .hub import Hub

log = logging.getLogger("chatmerge.server")
WEB_DIR = Path(__file__).resolve().parent / "web"


@web.middleware
async def _no_stale_files(request: web.Request, handler):  # type: ignore[no-untyped-def]
    """Make browsers (and OBS) re-check page files every load, so updates show up
    without anyone needing to clear a cache."""
    response = await handler(request)
    if request.path.startswith(("/static/", "/custom/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _local_only(port: int):  # type: ignore[no-untyped-def]
    """Only answer requests addressed to this PC by name and port. Blocks DNS-rebinding
    tricks where a website points its own domain at 127.0.0.1 to reach the overlay."""
    allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @web.middleware
    async def middleware(request: web.Request, handler):  # type: ignore[no-untyped-def]
        if request.host.lower() not in allowed:
            log.warning("refused a request for host %r", request.host)
            raise web.HTTPForbidden(text="chatmerge only answers on 127.0.0.1")
        return await handler(request)

    return middleware


def build_app(hub: Hub, emotes_dir: Path | None = None, port: int = 8787) -> web.Application:
    app = web.Application(middlewares=[_local_only(port), _no_stale_files])
    # Pages served by this app (OBS sources, the dock, a browser preview). Any other website
    # open in a browser is refused, so it can't press the dock's buttons or read chat.
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB_DIR / "overlay.html", headers={"Cache-Control": "no-store"})

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True, "version": __version__, "status": hub.status})

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        origin = request.headers.get("Origin")
        if origin is not None and origin.lower() not in allowed_origins:
            log.warning("refused a websocket from origin %r", origin)
            raise web.HTTPForbidden(text="chatmerge only accepts its own pages")
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)

        async def reply(message: dict[str, Any]) -> None:
            if not ws.closed:
                await ws.send_str(json.dumps(message))

        await hub.attach(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await hub.handle_client(msg.data, reply)  # dock: Hide, Hide user, Clear, Settings
                elif msg.type == WSMsgType.ERROR:
                    log.warning("overlay websocket error: %s", ws.exception())
        finally:
            await hub.detach(ws)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/events", index)  # same page; overlay.js switches to events-only mode

    async def game_page(_: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB_DIR / "game.html", headers={"Cache-Control": "no-store"})

    app.router.add_get("/game", game_page)  # Gliese Heist browser source
    app.router.add_get("/health", health)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", WEB_DIR, show_index=False)
    if emotes_dir is not None:
        emotes_dir.mkdir(exist_ok=True)
        app.router.add_static("/custom/", emotes_dir, show_index=False)  # YouTube channel emoji images
    return app
