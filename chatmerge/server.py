"""Local web server: serves the overlay page and pushes chat over a websocket."""
from __future__ import annotations

import logging
from pathlib import Path

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


def build_app(hub: Hub, emotes_dir: Path | None = None) -> web.Application:
    app = web.Application(middlewares=[_no_stale_files])

    async def index(_: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB_DIR / "overlay.html", headers={"Cache-Control": "no-store"})

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"ok": True, "version": __version__, "status": hub.status})

    async def ws_handler(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)
        await hub.attach(ws)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await hub.handle_client(msg.data)  # dock buttons: Hide, Clear overlay
                elif msg.type == WSMsgType.ERROR:
                    log.warning("overlay websocket error: %s", ws.exception())
        finally:
            await hub.detach(ws)
        return ws

    app.router.add_get("/", index)
    app.router.add_get("/events", index)  # same page; overlay.js switches to events-only mode
    app.router.add_get("/health", health)
    app.router.add_get("/ws", ws_handler)
    app.router.add_static("/static/", WEB_DIR, show_index=False)
    if emotes_dir is not None:
        emotes_dir.mkdir(exist_ok=True)
        app.router.add_static("/custom/", emotes_dir, show_index=False)  # YouTube channel emoji images
    return app
