"""Async wrapper over the Discord v10 REST API for user accounts."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import aiohttp

from .props import BROWSER_USER_AGENT, super_properties_b64

API_BASE = "https://discord.com/api/v10"


class DiscordError(Exception):
    def __init__(self, status: int, message: str, code: int | None = None):
        self.status = status
        self.code = code
        super().__init__(f"HTTP {status}: {message}")


def encode_emoji(emoji: dict | str) -> str:
    """Encode an emoji for a reaction URL: 'name:id' for custom, raw char for unicode."""
    if isinstance(emoji, dict):
        if emoji.get("id"):
            return f"{emoji.get('name', '')}:{emoji['id']}"
        return emoji.get("name", "")
    return emoji


class RestClient:
    """One reused aiohttp session, with basic 429 rate-limit handling."""

    def __init__(self, token: str):
        self.token = token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> "RestClient":
        self._session = aiohttp.ClientSession(
            headers={
                "Authorization": self.token,
                "User-Agent": BROWSER_USER_AGENT,
                "X-Super-Properties": super_properties_b64(),
                "X-Discord-Locale": "en-US",
            }
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._session:
            await self._session.close()

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        assert self._session is not None, "RestClient used outside async context"
        for _attempt in range(5):
            async with self._session.request(method, f"{API_BASE}{path}", **kwargs) as resp:
                if resp.status == 429:  # rate limited — wait and retry
                    body = await resp.json(content_type=None)
                    await asyncio.sleep(float(body.get("retry_after", 1)) + 0.1)
                    continue
                if resp.status == 204:
                    return None
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    msg = data.get("message", str(data)) if isinstance(data, dict) else str(data)
                    code = data.get("code") if isinstance(data, dict) else None
                    raise DiscordError(resp.status, msg, code)
                return data
        raise DiscordError(429, "rate limited (gave up after retries)")

    # --- account -----------------------------------------------------------

    async def me(self) -> dict:
        return await self._request("GET", "/users/@me")

    async def guilds(self) -> list[dict]:
        return await self._request("GET", "/users/@me/guilds")

    async def set_status(self, status: str) -> dict:
        """status: online | idle | dnd | invisible"""
        return await self._request("PATCH", "/users/@me/settings",
                                   json={"status": status})

    async def user_profile(self, user_id: str) -> dict:
        return await self._request(
            "GET", f"/users/{user_id}/profile",
            params={"with_mutual_guilds": "false"})

    async def set_nick(self, guild_id: str, nick: str) -> dict:
        return await self._request(
            "PATCH", f"/guilds/{guild_id}/members/@me", json={"nick": nick})

    # --- channels ----------------------------------------------------------

    async def guild_channels(self, guild_id: str) -> list[dict]:
        return await self._request("GET", f"/guilds/{guild_id}/channels")

    async def dm_channels(self) -> list[dict]:
        return await self._request("GET", "/users/@me/channels")

    async def create_dm(self, recipient_id: str) -> dict:
        return await self._request("POST", "/users/@me/channels",
                                   json={"recipients": [recipient_id]})

    async def active_threads(self, guild_id: str) -> dict:
        return await self._request("GET", f"/guilds/{guild_id}/threads/active")

    # --- messages ----------------------------------------------------------

    async def messages(self, channel_id: str, limit: int = 50,
                        before: str | None = None) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if before:
            params["before"] = before
        return await self._request("GET", f"/channels/{channel_id}/messages", params=params)

    async def get_message(self, channel_id: str, message_id: str) -> dict:
        return await self._request(
            "GET", f"/channels/{channel_id}/messages/{message_id}")

    async def send_message(self, channel_id: str, content: str,
                           reply_to: str | None = None) -> dict:
        payload: dict[str, Any] = {"content": content}
        if reply_to:
            payload["message_reference"] = {"message_id": reply_to, "channel_id": channel_id}
        return await self._request(
            "POST", f"/channels/{channel_id}/messages", json=payload)

    async def edit_message(self, channel_id: str, message_id: str, content: str) -> dict:
        return await self._request(
            "PATCH", f"/channels/{channel_id}/messages/{message_id}",
            json={"content": content})

    async def delete_message(self, channel_id: str, message_id: str) -> None:
        await self._request("DELETE", f"/channels/{channel_id}/messages/{message_id}")

    async def upload_file(self, channel_id: str, path: str, content: str = "") -> dict:
        assert self._session is not None
        p = Path(path).expanduser()
        form = aiohttp.FormData()
        form.add_field("payload_json", json.dumps({"content": content}),
                       content_type="application/json")
        form.add_field("files[0]", p.read_bytes(), filename=p.name,
                       content_type="application/octet-stream")
        async with self._session.post(
            f"{API_BASE}/channels/{channel_id}/messages", data=form) as resp:
            data = await resp.json(content_type=None)
            if resp.status >= 400:
                msg = data.get("message", str(data)) if isinstance(data, dict) else str(data)
                raise DiscordError(resp.status, msg)
            return data

    # --- reactions ---------------------------------------------------------

    async def add_reaction(self, channel_id: str, message_id: str, emoji: dict | str) -> None:
        e = quote(encode_emoji(emoji), safe="")
        await self._request(
            "PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{e}/@me")

    async def remove_reaction(self, channel_id: str, message_id: str, emoji: dict | str) -> None:
        e = quote(encode_emoji(emoji), safe="")
        await self._request(
            "DELETE", f"/channels/{channel_id}/messages/{message_id}/reactions/{e}/@me")

    # --- pins --------------------------------------------------------------

    async def pins(self, channel_id: str) -> list[dict]:
        return await self._request("GET", f"/channels/{channel_id}/pins")

    async def pin_message(self, channel_id: str, message_id: str) -> None:
        await self._request("PUT", f"/channels/{channel_id}/pins/{message_id}")

    async def unpin_message(self, channel_id: str, message_id: str) -> None:
        await self._request("DELETE", f"/channels/{channel_id}/pins/{message_id}")

    # --- members / search --------------------------------------------------

    async def search_members(self, guild_id: str, query: str, limit: int = 10) -> list[dict]:
        return await self._request(
            "GET", f"/guilds/{guild_id}/members/search",
            params={"query": query, "limit": limit})

    async def search_messages(self, guild_id: str, content: str) -> dict:
        return await self._request(
            "GET", f"/guilds/{guild_id}/messages/search",
            params={"content": content})

    # --- misc --------------------------------------------------------------

    async def trigger_typing(self, channel_id: str) -> None:
        await self._request("POST", f"/channels/{channel_id}/typing")

    async def ack(self, channel_id: str, message_id: str) -> None:
        try:
            await self._request(
                "POST", f"/channels/{channel_id}/messages/{message_id}/ack",
                json={"token": None})
        except DiscordError:
            pass  # acking is best-effort
