"""Discord Gateway (real-time WebSocket) client for a user account.

Implements the bits needed for a chat client: HELLO/heartbeat, IDENTIFY,
RESUME, and dispatch of events (READY, MESSAGE_CREATE, ...) to a callback.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

from .props import SUPER_PROPERTIES

log = logging.getLogger("tdiscord.gateway")

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"

# Gateway opcodes
OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_PRESENCE_UPDATE = 3
OP_RESUME = 6
OP_RECONNECT = 7
OP_REQUEST_GUILD_MEMBERS = 8
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11
OP_GUILD_SUBSCRIBE = 14

EventCallback = Callable[[str, dict], Awaitable[None]]


class Gateway:
    def __init__(self, token: str, on_event: EventCallback):
        self.token = token
        self.on_event = on_event

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._seq: int | None = None
        self._session_id: str | None = None
        self._resume_url: str | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._closed = False

    async def run(self) -> None:
        """Connect and keep the connection alive, reconnecting as needed."""
        self._session = aiohttp.ClientSession()
        try:
            while not self._closed:
                try:
                    await self._connect_once()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — log and retry any failure
                    log.exception("gateway connection error; reconnecting in 5s")
                    await asyncio.sleep(5)
        finally:
            await self._session.close()

    async def close(self) -> None:
        self._closed = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()

    # -----------------------------------------------------------------------

    async def _connect_once(self) -> None:
        url = self._resume_url or GATEWAY_URL
        if self._resume_url and "encoding" not in url:
            url += "/?v=10&encoding=json"
        assert self._session is not None

        async with self._session.ws_connect(url, max_msg_size=0) as ws:
            self._ws = ws
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._dispatch(msg.json())
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        # connection ended; stop heartbeating until we reconnect
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None

    async def _dispatch(self, payload: dict) -> None:
        op = payload.get("op")

        if op == OP_HELLO:
            interval = payload["d"]["heartbeat_interval"] / 1000
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(interval))
            if self._session_id and self._resume_url:
                await self._send_resume()
            else:
                await self._send_identify()

        elif op == OP_DISPATCH:
            self._seq = payload.get("s", self._seq)
            event = payload.get("t") or ""
            data = payload.get("d") or {}
            if event == "READY":
                self._session_id = data.get("session_id")
                self._resume_url = data.get("resume_gateway_url")
            await self.on_event(event, data)

        elif op == OP_HEARTBEAT:
            await self._send({"op": OP_HEARTBEAT, "d": self._seq})

        elif op == OP_INVALID_SESSION:
            # cannot resume — drop session and re-identify after a short wait
            self._session_id = None
            self._resume_url = None
            await asyncio.sleep(1 + random.random() * 4)
            await self._send_identify()

        elif op == OP_RECONNECT:
            if self._ws and not self._ws.closed:
                await self._ws.close()

    async def _heartbeat_loop(self, interval: float) -> None:
        # initial jitter per Discord's recommendation
        await asyncio.sleep(interval * random.random())
        try:
            while True:
                await self._send({"op": OP_HEARTBEAT, "d": self._seq})
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _send(self, payload: dict) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.send_json(payload)

    # --- public sends ------------------------------------------------------

    async def update_presence(self, status: str) -> None:
        """status: online | idle | dnd | invisible"""
        await self._send({
            "op": OP_PRESENCE_UPDATE,
            "d": {"status": status, "since": 0, "activities": [], "afk": False},
        })

    async def subscribe_guild(self, guild_id: str, channel_id: str | None = None) -> None:
        """Lazily subscribe to a guild so it streams members/presences/typing.

        Discord's web client only loads this data on demand (op 14); without it
        large guilds send no member list. We request the first chunk of the
        member sidebar for the open channel.
        """
        payload: dict = {
            "guild_id": guild_id,
            "typing": True,
            "threads": True,
            "activities": True,
        }
        if channel_id:
            payload["channels"] = {channel_id: [[0, 99]]}
        await self._send({"op": OP_GUILD_SUBSCRIBE, "d": payload})

    async def request_members(self, guild_id: str, query: str = "", limit: int = 50) -> None:
        await self._send({
            "op": OP_REQUEST_GUILD_MEMBERS,
            "d": {"guild_id": guild_id, "query": query, "limit": limit, "presences": True},
        })

    async def _send_identify(self) -> None:
        await self._send(
            {
                "op": OP_IDENTIFY,
                "d": {
                    "token": self.token,
                    "capabilities": 16381,
                    "properties": SUPER_PROPERTIES,
                    "presence": {
                        "status": "online",
                        "since": 0,
                        "activities": [],
                        "afk": False,
                    },
                    "compress": False,
                    "client_state": {
                        "guild_versions": {},
                        "highest_last_message_id": "0",
                        "read_state_version": 0,
                        "user_guild_settings_version": -1,
                        "user_settings_version": -1,
                        "private_channels_version": "0",
                        "api_code_version": 0,
                    },
                },
            }
        )

    async def _send_resume(self) -> None:
        await self._send(
            {
                "op": OP_RESUME,
                "d": {
                    "token": self.token,
                    "session_id": self._session_id,
                    "seq": self._seq,
                },
            }
        )
