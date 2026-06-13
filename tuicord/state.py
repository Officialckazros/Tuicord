"""Central client state: the in-memory cache of everything we know.

Populated from the gateway READY / READY_SUPPLEMENTAL payloads and kept current
by dispatch events. The renderer and UI read from here to resolve mentions,
role colors, channel names, custom emoji, presence, and unread state.

Everything is parsed defensively — Discord's user-account payloads vary between
client builds, so a missing/renamed field must never crash us.
"""

from __future__ import annotations

from typing import Any

# Channel type constants
CH_TEXT = 0
CH_DM = 1
CH_VOICE = 2
CH_GROUP_DM = 3
CH_CATEGORY = 4
CH_ANNOUNCEMENT = 5
CH_ANNOUNCEMENT_THREAD = 10
CH_PUBLIC_THREAD = 11
CH_PRIVATE_THREAD = 12
CH_STAGE = 13
CH_FORUM = 14

TEXT_CHANNEL_TYPES = {CH_TEXT, CH_ANNOUNCEMENT}
THREAD_TYPES = {CH_ANNOUNCEMENT_THREAD, CH_PUBLIC_THREAD, CH_PRIVATE_THREAD}
DM_TYPES = {CH_DM, CH_GROUP_DM}

STATUS_DOT = {
    "online": "●",
    "idle": "◐",
    "dnd": "⊘",
    "offline": "○",
    "invisible": "○",
}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        # some payloads wrap lists as {"entries": [...]}
        return value.get("entries") or []
    return []


def color_hex(value: int | None) -> str | None:
    if not value:
        return None
    return f"#{value & 0xFFFFFF:06x}"


class State:
    def __init__(self) -> None:
        self.me: dict = {}
        self.guilds: dict[str, dict] = {}
        self.channels: dict[str, dict] = {}
        self.users: dict[str, dict] = {}
        self.members: dict[tuple[str, str], dict] = {}
        self.roles: dict[str, dict] = {}
        self.guild_role_ids: dict[str, list[str]] = {}
        self.emojis: dict[str, dict] = {}
        self.presences: dict[str, str] = {}
        self.read_state: dict[str, dict] = {}
        self.relationships: dict[str, int] = {}
        self.dm_channel_ids: list[str] = []

    # --- READY -------------------------------------------------------------

    def load_ready(self, data: dict) -> None:
        self.me = data.get("user", {}) or {}
        if self.me.get("id"):
            self.users[self.me["id"]] = self.me

        for user in _as_list(data.get("users")):
            if user.get("id"):
                self.users[user["id"]] = user

        for rel in _as_list(data.get("relationships")):
            uid = rel.get("user_id") or (rel.get("user") or {}).get("id")
            if uid:
                self.relationships[uid] = rel.get("type", 0)
                if rel.get("user"):
                    self.users.setdefault(uid, rel["user"])

        guilds = _as_list(data.get("guilds"))
        merged_members = data.get("merged_members") or []
        for idx, guild in enumerate(guilds):
            self._add_guild(guild)
            if idx < len(merged_members):
                for member in merged_members[idx] or []:
                    self._add_member(guild.get("id"), member)

        for ch in _as_list(data.get("private_channels")):
            self._add_channel(ch)
            if ch.get("id"):
                self.dm_channel_ids.append(ch["id"])
            for recip in ch.get("recipients", []):
                if recip.get("id"):
                    self.users[recip["id"]] = recip

        for entry in _as_list(data.get("read_state")):
            cid = entry.get("id") or entry.get("channel_id")
            if cid:
                self.read_state[cid] = {
                    "last_message_id": entry.get("last_message_id"),
                    "mention_count": entry.get("mention_count", 0),
                }

        for pres in _as_list(data.get("presences")):
            self._add_presence(pres)

    def load_ready_supplemental(self, data: dict) -> None:
        merged = data.get("merged_presences") or {}
        for group in merged.get("guilds") or []:
            for pres in group or []:
                self._add_presence(pres)
        for pres in merged.get("friends") or []:
            self._add_presence(pres)
        # supplemental guilds carry voice states / activities — ignored for now

    # --- incremental upserts ----------------------------------------------

    def _add_guild(self, guild: dict) -> None:
        gid = guild.get("id")
        if not gid:
            return
        props = guild.get("properties") or {}
        stored = {
            "id": gid,
            "name": guild.get("name") or props.get("name") or "unknown",
            "icon": guild.get("icon") or props.get("icon"),
            "owner_id": guild.get("owner_id") or props.get("owner_id"),
        }
        self.guilds[gid] = stored

        role_ids = []
        for role in guild.get("roles", []) or []:
            if role.get("id"):
                self.roles[role["id"]] = role
                role_ids.append(role["id"])
        if role_ids:
            self.guild_role_ids[gid] = role_ids

        for emoji in guild.get("emojis", []) or []:
            if emoji.get("id"):
                self.emojis[emoji["id"]] = emoji

        for ch in guild.get("channels", []) or []:
            ch = dict(ch)
            ch["guild_id"] = gid
            self._add_channel(ch)
        for thread in guild.get("threads", []) or []:
            thread = dict(thread)
            thread["guild_id"] = gid
            self._add_channel(thread)

    def _add_channel(self, channel: dict) -> None:
        if channel.get("id"):
            self.channels[channel["id"]] = channel

    def remove_channel(self, channel_id: str) -> None:
        self.channels.pop(channel_id, None)
        if channel_id in self.dm_channel_ids:
            self.dm_channel_ids.remove(channel_id)

    def _add_member(self, guild_id: str | None, member: dict) -> None:
        if not guild_id:
            return
        user = member.get("user") or {}
        uid = user.get("id") or member.get("id")
        if not uid:
            return
        if user.get("id"):
            self.users.setdefault(uid, user)
        self.members[(guild_id, uid)] = member

    def add_member(self, guild_id: str, member: dict) -> None:
        self._add_member(guild_id, member)

    def _add_presence(self, presence: dict) -> None:
        user = presence.get("user") or {}
        uid = user.get("id") or presence.get("user_id")
        if uid:
            self.presences[uid] = presence.get("status", "offline")

    def update_presence(self, data: dict) -> None:
        self._add_presence(data)

    def remember_author(self, author: dict, guild_id: str | None = None) -> None:
        if author.get("id"):
            self.users.setdefault(author["id"], author)

    def set_read(self, channel_id: str, last_message_id: str | None) -> None:
        entry = self.read_state.setdefault(channel_id, {})
        entry["last_message_id"] = last_message_id
        entry["mention_count"] = 0

    # --- lookups -----------------------------------------------------------

    def user_name(self, user_id: str, guild_id: str | None = None) -> str:
        if guild_id:
            member = self.members.get((guild_id, user_id))
            if member and member.get("nick"):
                return member["nick"]
        user = self.users.get(user_id)
        if not user:
            return "unknown-user"
        return user.get("global_name") or user.get("username") or "unknown-user"

    def member_color(self, guild_id: str | None, user_id: str) -> str | None:
        if not guild_id:
            return None
        member = self.members.get((guild_id, user_id))
        if not member:
            return None
        best_pos = -1
        best_color: str | None = None
        for rid in member.get("roles", []) or []:
            role = self.roles.get(rid)
            if not role:
                continue
            col = color_hex(role.get("color"))
            if col and role.get("position", 0) > best_pos:
                best_pos = role["position"]
                best_color = col
        return best_color

    def channel(self, channel_id: str) -> dict | None:
        return self.channels.get(channel_id)

    def channel_name(self, channel_id: str) -> str:
        ch = self.channels.get(channel_id)
        if not ch:
            return "unknown-channel"
        if ch.get("name"):
            return ch["name"]
        recips = ch.get("recipients", [])
        if recips:
            return ", ".join(r.get("global_name") or r.get("username", "?") for r in recips)
        return "direct-message"

    def role(self, role_id: str) -> dict | None:
        return self.roles.get(role_id)

    def emoji(self, emoji_id: str) -> dict | None:
        return self.emojis.get(emoji_id)

    def presence_dot(self, user_id: str) -> str:
        return STATUS_DOT.get(self.presences.get(user_id, "offline"), "○")

    def is_unread(self, channel_id: str) -> bool:
        ch = self.channels.get(channel_id)
        if not ch:
            return False
        last = ch.get("last_message_id")
        if not last:
            return False
        read = self.read_state.get(channel_id, {}).get("last_message_id")
        if read is None:
            return True
        try:
            return int(last) > int(read)
        except (TypeError, ValueError):
            return last != read

    def guild_channels(self, guild_id: str) -> list[dict]:
        return [c for c in self.channels.values() if c.get("guild_id") == guild_id]
