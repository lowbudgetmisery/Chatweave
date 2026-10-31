"""YouTube live chat, read-only: OAuth (youtube.readonly), auto-detect your active
broadcast, then liveChatMessages.streamList over gRPC (one 5-unit call per connection)."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import aiohttp
import grpc

from . import protogen
from .config import ConfigError
from .hub import Hub
from .models import ChatItem, text_frag

log = logging.getLogger("chatmerge.youtube")

SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
BROADCASTS_URL = "https://www.googleapis.com/youtube/v3/liveBroadcasts"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
GRPC_TARGET = "dns:///youtube.googleapis.com:443"
QUOTA_STREAMLIST = 5
QUOTA_BROADCASTS_LIST = 1
QUOTA_VIDEOS_LIST = 1
HISTORY_GRACE_S = 30.0
RECHECK_AFTER_END_S = 10.0
# Measured Oct 2026: YouTube closes each streamList connection after ~10 s, so every
# ~11 s costs another 5 units (~1,600/hour). Pacing spaces reconnects out so the daily
# allowance covers the hours the streamer wants. Nothing is lost while waiting: the next
# connection resumes from the last page token.
LOOKUP_RESERVE = 0.95  # leave ~5% of the budget for the 1-unit "are you live" checks


def min_connection_cycle_s(quota_per_day: int, hours_per_day: float) -> float:
    """Minimum seconds between chat connections so streaming hours_per_day fits the quota."""
    units_per_hour = quota_per_day * LOOKUP_RESERVE / hours_per_day
    return QUOTA_STREAMLIST * 3600.0 / units_per_hour


class YouTubeConfigError(ConfigError):
    pass


# ---------------------------------------------------------------- auth --------
def run_auth(client_secret: Path, token_path: Path) -> None:
    """Interactive one-time login. Opens the browser and saves a refreshable token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not client_secret.is_file():
        raise YouTubeConfigError(f'OAuth client file not found: "{client_secret}"')
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    if not creds.refresh_token:
        raise YouTubeConfigError("Google returned no refresh token; run auth-youtube again.")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if token_path.exists():
        print(f'Replacing existing token file: "{token_path}"')
    token_path.write_text(creds.to_json(), encoding="utf-8")
    print(f'Saved YouTube token: "{token_path}"')


class TokenSource:
    def __init__(self, token_path: Path) -> None:
        from google.oauth2.credentials import Credentials

        if not token_path.is_file():
            raise YouTubeConfigError(
                f'YouTube login not found: "{token_path}". Double-click "YouTube Login.bat" first.'
            )
        self._path = token_path
        self._creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    async def get(self, force_refresh: bool = False) -> str:
        if force_refresh or not self._creds.valid:
            await asyncio.to_thread(self._refresh)
        return str(self._creds.token)

    def _refresh(self) -> None:
        from google.auth.transport.requests import Request

        self._creds.refresh(Request())
        self._path.write_text(self._creds.to_json(), encoding="utf-8")


# ------------------------------------------------------------- mapping --------
def _parse_ts(value: str) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        log.warning("unparseable published_at: %r", value)
        return None


def map_message(pb2: Any, msg: Any) -> tuple[str, Any]:
    """Map one LiveChatMessage to (action, payload).

    actions: add (ChatItem), delete (message id), timeout / ban (channel id), ended (None), skip (None)
    """
    T = pb2.LiveChatMessageSnippet.TypeWrapper
    snip = msg.snippet
    author = msg.author_details
    kind_type = snip.type

    if kind_type == T.CHAT_ENDED_EVENT:
        return "ended", None
    if kind_type == T.TOMBSTONE:
        return "delete", msg.id
    if kind_type == T.USER_BANNED_EVENT:
        details = snip.user_banned_details
        banned = details.banned_user_details.channel_id
        if not banned:
            return "skip", None
        temporary = details.ban_type == pb2.LiveChatUserBannedMessageDetails.BanTypeWrapper.TEMPORARY
        return ("timeout" if temporary else "ban"), banned

    roles: list[str] = []
    if author.is_chat_owner:
        roles.append("broadcaster")
    if author.is_chat_moderator:
        roles.append("mod")
    if author.is_chat_sponsor:
        roles.append("member")

    item = ChatItem(
        id=msg.id,
        platform="youtube",
        kind="chat",
        author=author.display_name or "YouTube viewer",
        user_id=author.channel_id or snip.author_channel_id,
        roles=roles,
        ts=_parse_ts(snip.published_at) or time.time(),
    )

    if kind_type == T.TEXT_MESSAGE_EVENT:
        text = snip.text_message_details.message_text or snip.display_message
        item.fragments = [text_frag(text)] if text else []
    elif kind_type == T.SUPER_CHAT_EVENT:
        d = snip.super_chat_details
        item.kind = "superchat"
        item.amount = d.amount_display_string
        item.tier = d.tier or None
        item.headline = f"Super Chat {d.amount_display_string}".strip()
        item.fragments = [text_frag(d.user_comment)] if d.user_comment else []
    elif kind_type == T.SUPER_STICKER_EVENT:
        d = snip.super_sticker_details
        item.kind = "supersticker"
        item.amount = d.amount_display_string
        item.tier = d.tier or None
        item.headline = f"Super Sticker {d.amount_display_string}".strip()
        alt = d.super_sticker_metadata.alt_text
        item.fragments = [text_frag(alt)] if alt else []
    elif kind_type == T.NEW_SPONSOR_EVENT:
        d = snip.new_sponsor_details
        item.kind = "member"
        level = d.member_level_name
        if d.is_upgrade and level:
            item.headline = f"Upgraded to {level}"
        else:
            item.headline = f"New member: {level}" if level else "New member"
    elif kind_type == T.MEMBER_MILESTONE_CHAT_EVENT:
        d = snip.member_milestone_chat_details
        item.kind = "member"
        months = d.member_month
        item.headline = f"Member for {months} month{'s' if months != 1 else ''}" if months else "Member milestone"
        item.fragments = [text_frag(d.user_comment)] if d.user_comment else []
    elif kind_type == T.MEMBERSHIP_GIFTING_EVENT:
        d = snip.membership_gifting_details
        count = d.gift_memberships_count
        item.kind = "gift"
        item.amount = str(count)
        item.headline = f"Gifted {count} membership{'s' if count != 1 else ''}"
    elif kind_type == T.GIFT_EVENT:
        d = snip.gift_details
        item.kind = "gift"
        combo = f" x{d.combo_count}" if d.combo_count > 1 else ""
        item.headline = f"Sent {d.gift_name or 'a gift'}{combo}"
    else:
        # GIFT_MEMBERSHIP_RECEIVED (one per recipient), polls, members-only mode, fan funding
        return "skip", None
    return "add", item


# ------------------------------------------------------- test-mode helpers --------
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_id(url: str) -> str | None:
    """Video ID from a YouTube link (watch?v=, youtu.be/, /live/, /shorts/) or a bare ID.

    Channel links like youtube.com/@name/live carry no video ID, so they return None.
    """
    text = url.strip()
    if _VIDEO_ID.match(text):
        return text
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host.endswith("youtu.be"):
        candidate = parts.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        candidate = (parse_qs(parts.query).get("v") or [""])[0]
        segments = [seg for seg in parts.path.split("/") if seg]
        if not candidate and len(segments) >= 2 and segments[0] in ("live", "shorts", "embed"):
            candidate = segments[1]
    else:
        return None
    return candidate if _VIDEO_ID.match(candidate or "") else None


def chat_id_from_videos(body: dict[str, Any]) -> tuple[str | None, str]:
    """(activeLiveChatId or None, title) from a videos.list response."""
    for item in body.get("items") or []:
        details = item.get("liveStreamingDetails") or {}
        title = (item.get("snippet") or {}).get("title", "")
        return details.get("activeLiveChatId"), title
    return None, ""


def chat_id_from_broadcasts(body: dict[str, Any]) -> tuple[str | None, str]:
    """(liveChatId or None, title) from a liveBroadcasts.list response."""
    for item in body.get("items") or []:
        snippet = item.get("snippet") or {}
        if snippet.get("liveChatId"):
            return snippet["liveChatId"], snippet.get("title", "")
    return None, ""


# -------------------------------------------------------------- client --------
class YouTubeChat:
    def __init__(
        self,
        hub: Hub,
        session: aiohttp.ClientSession,
        token_path: Path,
        detect_interval_s: float = 30.0,
        test_video_id: str | None = None,
        quota_per_day: int = 10000,
        hours_per_day: float = 8.0,
    ) -> None:
        self.hub = hub
        self.session = session
        self.tokens = TokenSource(token_path)
        self.detect_interval_s = detect_interval_s
        self.test_video_id = test_video_id
        self.quota_per_day = quota_per_day
        self.min_cycle_s = min_connection_cycle_s(quota_per_day, hours_per_day)
        self._last_open: float | None = None
        self.pb2, self.pb2_grpc = protogen.load()
        self._seen: deque[str] = deque(maxlen=4000)
        self._seen_set: set[str] = set()
        self.quota_used = 0
        self.connections = 0

    async def run(self) -> None:
        from google.auth.exceptions import RefreshError

        channel = grpc.aio.secure_channel(GRPC_TARGET, grpc.ssl_channel_credentials())
        stub = self.pb2_grpc.V3DataLiveChatMessageServiceStub(channel)
        try:
            while True:
                chat_id = await self._find_chat()
                await self._stream(stub, chat_id)
                ended = "TEST MODE: that video's chat closed, re-checking" if self.test_video_id else "chat closed, re-checking whether you are still live"
                await self.hub.set_status("youtube", ended)
                await asyncio.sleep(RECHECK_AFTER_END_S)
        except RefreshError as exc:
            # Expired or revoked login (Testing-mode Google apps expire logins after 7 days).
            log.error("YouTube token refresh failed: %s", exc)
            await self.hub.set_status(
                "youtube", 'login expired: close this window, double-click "YouTube Login.bat", then start again'
            )
        finally:
            await channel.close()

    # -- finding the live chat ----------------------------------------------
    async def _find_chat(self) -> str:
        """Own live broadcast (normal), or the given public video (test mode)."""
        if self.test_video_id:
            url = VIDEOS_URL
            params = {"part": "liveStreamingDetails,snippet", "id": self.test_video_id}
            extract = chat_id_from_videos
            waiting = f"TEST MODE: waiting for video {self.test_video_id} to be live with chat on"
            found = "TEST MODE: reading chat of"
            cost = QUOTA_VIDEOS_LIST
        else:
            url = BROADCASTS_URL
            params = {"part": "snippet", "broadcastStatus": "active", "broadcastType": "all"}
            extract = chat_id_from_broadcasts
            waiting = "waiting for you to go live"
            found = "live detected:"
            cost = QUOTA_BROADCASTS_LIST
        await self.hub.set_status("youtube", waiting)
        force = False
        while True:
            token = await self.tokens.get(force_refresh=force)
            force = False
            try:
                async with self.session.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as resp:
                    self.quota_used += cost
                    body = await resp.json(content_type=None)
                    if resp.status == 401:
                        force = True
                        continue
                    if resp.status != 200:
                        reason = _error_reason(body)
                        wait = 600.0 if reason == "quotaExceeded" else self.detect_interval_s
                        await self.hub.set_status("youtube", f"lookup failed: HTTP {resp.status} {reason}")
                        await asyncio.sleep(wait)
                        continue
            except (aiohttp.ClientError, TimeoutError) as exc:
                await self.hub.set_status("youtube", f"lookup error: {exc}")
                await asyncio.sleep(self.detect_interval_s)
                continue
            chat_id, title = extract(body)
            if chat_id:
                await self.hub.set_status("youtube", f"{found} {title}".strip())
                return str(chat_id)
            await self.hub.set_status("youtube", waiting)
            await asyncio.sleep(self.detect_interval_s)

    # -- streaming ----------------------------------------------------------
    async def _stream(self, stub: Any, chat_id: str) -> None:
        page_token: str | None = None
        history_cutoff = time.time() - HISTORY_GRACE_S
        backoff = 1.0
        force_refresh = False
        while True:
            if self._last_open is not None:
                # pace reconnects to the daily quota budget (at least 1 s apart)
                wait = max(1.0, self._last_open + self.min_cycle_s - time.monotonic())
                await asyncio.sleep(wait)
            token = await self.tokens.get(force_refresh=force_refresh)
            force_refresh = False
            request = self.pb2.LiveChatMessageListRequest(
                live_chat_id=chat_id,
                part=["id", "snippet", "authorDetails"],
                page_token=page_token,
            )
            self.quota_used += QUOTA_STREAMLIST
            self.connections += 1
            self._last_open = time.monotonic()
            await self.hub.set_status(
                "youtube", f"connected to live chat (est. quota used today: {self.quota_used} of {self.quota_per_day})"
            )
            try:
                opened = time.monotonic()
                responses = 0
                call = stub.StreamList(request, metadata=(("authorization", f"Bearer {token}"),))
                async for response in call:
                    responses += 1
                    if response.next_page_token:
                        page_token = response.next_page_token
                    for msg in response.items:
                        ts = _parse_ts(msg.snippet.published_at)
                        if ts is not None and ts < history_cutoff:
                            continue  # backlog sent on first connect: not live chat
                        if await self._dispatch(msg) == "ended":
                            return
                    if response.offline_at:
                        return
                    backoff = 1.0
            except grpc.aio.AioRpcError as err:
                code = err.code()
                log.info("chat connection #%d closed with %s after %.0fs (%d batches)",
                         self.connections, code.name, time.monotonic() - opened, responses)
                if code in (grpc.StatusCode.FAILED_PRECONDITION, grpc.StatusCode.NOT_FOUND):
                    return  # chat ended or disabled
                if code == grpc.StatusCode.UNAUTHENTICATED:
                    force_refresh = True
                    continue
                await self.hub.set_status("youtube", f"stream error {code.name}: {err.details()}")
                if code == grpc.StatusCode.PERMISSION_DENIED:
                    await asyncio.sleep(60)
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            # Server closed the stream normally: resume from the last page token.
            log.info("chat connection #%d closed normally by YouTube after %.0fs (%d batches); resuming",
                     self.connections, time.monotonic() - opened, responses)

    async def _dispatch(self, msg: Any) -> str:
        action, payload = map_message(self.pb2, msg)
        if action == "add":
            if payload.id in self._seen_set:
                return action
            if len(self._seen) == self._seen.maxlen:
                self._seen_set.discard(self._seen[0])
            self._seen.append(payload.id)
            self._seen_set.add(payload.id)
            await self.hub.add(payload)
        elif action == "delete":
            await self.hub.moderate("youtube", "delete", ids=[payload])
        elif action in ("timeout", "ban"):
            await self.hub.moderate("youtube", action, user_id=payload)
        return action


def _error_reason(body: Any) -> str:
    try:
        return str(body["error"]["errors"][0]["reason"])
    except (KeyError, IndexError, TypeError):
        return ""
