"""The Tuicord Textual application."""

from __future__ import annotations

import asyncio
import time
import webbrowser

from rich.console import Group
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from .api import DiscordError, RestClient
from .gateway import Gateway
from .state import (
    CH_ANNOUNCEMENT,
    CH_CATEGORY,
    CH_FORUM,
    CH_STAGE,
    CH_TEXT,
    CH_VOICE,
    DM_TYPES,
    State,
    THREAD_TYPES,
)
from .widgets import MemberList, MessageView

DM_SENTINEL = "@dms"
OPENABLE = {CH_TEXT, CH_ANNOUNCEMENT} | THREAD_TYPES | DM_TYPES
CHANNEL_ICON = {
    CH_TEXT: "#", CH_ANNOUNCEMENT: "📣", CH_VOICE: "🔊", CH_STAGE: "🎙",
    CH_FORUM: "🗒", 11: "🧵", 12: "🧵", 10: "🧵",
}

HELP_TEXT = """\
[b]Tuicord — keys & commands[/b]

[b]Global[/b]
  Ctrl+Q  quit          Ctrl+R  reload channel
  Ctrl+B  members panel  Ctrl+S  reveal/hide spoilers
  Ctrl+G  navigate messages   F1  this help
  Tab     move focus      Esc  cancel mode / back to input

[b]Message navigation[/b] (when the message list is focused)
  j / k or down up   move    g / G   top / bottom
  r reply   e edit   d delete   a react
  p pin     o open link   c copy id   u profile
  Enter / i   back to typing

[b]Slash commands[/b] (type in the message box)
  /help                 /reveal              /reconnect   /quit
  /reply <text>         /edit <text>         /delete
  /react <emoji>        /pin       /unpin    /pins
  /members              /threads             /search <query>
  /dm <name|id>         /profile <name|id>   /goto <channel>
  /status <online|idle|dnd|invisible>
  /nick <name>          /upload <path>
  /shrug /tableflip /unflip /spoiler <text>
"""


class DataItem(ListItem):
    def __init__(self, item_id: str, label_text: str, *, openable: bool = True,
                 style: str = "") -> None:
        lbl = Label(Text(label_text, style=style) if style else label_text)
        super().__init__(lbl)
        self.item_id = item_id
        self.openable = openable
        self._label = lbl

    def set_label(self, label_text: str, style: str = "") -> None:
        self._label.update(Text(label_text, style=style) if style else label_text)


class InfoScreen(ModalScreen):
    """A dismissable centered panel for help, pins, search results, etc."""

    DEFAULT_CSS = """
    InfoScreen { align: center middle; }
    InfoScreen > #box {
        width: 80%; max-width: 100; height: 80%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    InfoScreen #title { text-style: bold; color: $accent; height: 1; }
    """
    BINDINGS = [Binding("escape,q,enter", "close", "Close")]

    def __init__(self, title: str, body) -> None:
        super().__init__()
        self._title = title
        self._body = body

    def compose(self) -> ComposeResult:
        with Vertical(id="box"):
            yield Static(self._title, id="title")
            with VerticalScroll():
                yield Static(self._body)

    def action_close(self) -> None:
        self.dismiss()


class TerminalDiscord(App):
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar { width: 32; border-right: solid $accent-darken-1; }
    #sidebar Static.section { padding: 0 1; color: $text-muted; text-style: bold; }
    #guilds { height: 35%; background: $panel; }
    #channels { height: 1fr; }
    #center { width: 1fr; }
    #chan-header { height: 1; padding: 0 1; background: $panel; color: $text; }
    #messages { height: 1fr; background: $surface; }
    #typing { height: 1; padding: 0 1; color: $text-muted; }
    #composer { dock: bottom; }
    ListView:focus > .list-item--highlighted { background: $accent; }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+r", "reload", "Reload"),
        Binding("ctrl+b", "toggle_members", "Members"),
        Binding("ctrl+s", "reveal", "Spoilers"),
        Binding("ctrl+g", "focus_messages", "Navigate"),
        Binding("f1", "help", "Help"),
        Binding("escape", "escape", "Back", show=False),
    ]

    class GatewayEvent(Message):
        def __init__(self, event: str, data: dict) -> None:
            super().__init__()
            self.event = event
            self.data = data

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token
        self.state = State()
        self.rest = RestClient(token)
        self.gateway = Gateway(token, self._on_gateway_event)

        self.current_channel_id: str | None = None
        self.current_guild_id: str | None = None
        self.reveal = False
        self.mode = "send"  # send | reply | edit | react | search | dm | status | nick
        self.mode_target: dict | None = None
        self._channel_items: dict[str, DataItem] = {}
        self._typing: dict[str, float] = {}
        self._last_notify = 0.0

    # --- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("SERVERS", classes="section")
                yield ListView(id="guilds")
                yield Static("CHANNELS", classes="section")
                yield ListView(id="channels")
            with Vertical(id="center"):
                yield Static("Select a channel", id="chan-header")
                yield MessageView(id="messages")
                yield Static("", id="typing")
                yield Input(placeholder="Select a channel…", id="composer", disabled=True)
            yield MemberList(id="members")
        yield Footer()

    # --- lifecycle ---------------------------------------------------------

    async def on_mount(self) -> None:
        self.title = "Tuicord"
        self.query_one(MemberList).add_class("-hidden")
        await self.rest.__aenter__()
        try:
            me = await self.rest.me()
        except DiscordError as e:
            self.sub_title = "login failed"
            self.notify(f"Login failed: {e}. Check your token.", severity="error",
                        timeout=10)
            return
        self.state.me = me
        self.state.users[me["id"]] = me
        self.sub_title = f"{me.get('global_name') or me.get('username')} — connecting…"
        try:
            for g in await self.rest.guilds():
                self.state._add_guild(g)
        except DiscordError as e:
            self.notify(f"Could not list servers: {e}", severity="error")
        self._populate_guilds()
        self.run_worker(self.gateway.run(), name="gateway")
        self.set_interval(2.0, self._tick_typing)
        self.query_one("#composer", Input).focus()

    async def on_unmount(self) -> None:
        await self.gateway.close()
        await self.rest.__aexit__(None, None, None)

    # --- sidebar -----------------------------------------------------------

    def _populate_guilds(self) -> None:
        lv = self.query_one("#guilds", ListView)
        lv.clear()
        lv.append(DataItem(DM_SENTINEL, "✉  Direct Messages"))
        for g in sorted(self.state.guilds.values(), key=lambda x: x["name"].lower()):
            lv.append(DataItem(g["id"], f"❖  {g['name']}"))

    async def _populate_channels(self, guild_id: str) -> None:
        lv = self.query_one("#channels", ListView)
        await lv.clear()
        self._channel_items.clear()

        if guild_id == DM_SENTINEL:
            try:
                dms = await self.rest.dm_channels()
            except DiscordError as e:
                self.notify(f"Could not load DMs: {e}", severity="error")
                return
            dms.sort(key=lambda c: c.get("last_message_id") or "", reverse=True)
            for ch in dms:
                if ch.get("type") not in DM_TYPES:
                    continue
                ch.setdefault("guild_id", None)
                self.state._add_channel(ch)
                item = self._make_channel_item(ch, dm=True)
                self._channel_items[ch["id"]] = item
                lv.append(item)
            return

        try:
            channels = await self.rest.guild_channels(guild_id)
        except DiscordError as e:
            self.notify(f"Could not load channels: {e}", severity="error")
            return
        for ch in channels:
            ch["guild_id"] = guild_id
            self.state._add_channel(ch)

        cats = sorted((c for c in channels if c.get("type") == CH_CATEGORY),
                      key=lambda c: c.get("position", 0))

        def children(parent_id):
            kids = [c for c in channels
                    if c.get("parent_id") == parent_id and c.get("type") != CH_CATEGORY]
            return sorted(kids, key=lambda c: (c.get("type") == CH_VOICE,
                                               c.get("position", 0)))

        for ch in children(None):
            self._add_channel_item(lv, ch)
        for cat in cats:
            lv.append(DataItem("", f"▾ {cat.get('name', '').upper()}",
                               openable=False, style="bold grey50"))
            for ch in children(cat["id"]):
                self._add_channel_item(lv, ch, indent=True)

    def _add_channel_item(self, lv: ListView, ch: dict, indent: bool = False) -> None:
        item = self._make_channel_item(ch, indent=indent)
        self._channel_items[ch["id"]] = item
        lv.append(item)

    def _make_channel_item(self, ch: dict, *, indent: bool = False,
                           dm: bool = False) -> DataItem:
        ctype = ch.get("type", CH_TEXT)
        openable = ctype in OPENABLE
        pad = "  " if indent else ""
        unread = self.state.is_unread(ch["id"])
        if dm:
            recips = ch.get("recipients", [])
            dot = self.state.presence_dot(recips[0]["id"]) if len(recips) == 1 else "○"
            name = self.state.channel_name(ch["id"])
            label = f"{dot} {name}"
        else:
            icon = CHANNEL_ICON.get(ctype, "#")
            mark = "●" if unread else " "
            label = f"{pad}{mark}{icon} {ch.get('name', '?')}"
        style = "bold white" if unread else ("grey70" if openable else "grey46")
        return DataItem(ch["id"], label, openable=openable, style=style)

    def _refresh_channel_label(self, channel_id: str) -> None:
        item = self._channel_items.get(channel_id)
        ch = self.state.channel(channel_id)
        if not item or not ch:
            return
        unread = self.state.is_unread(channel_id)
        icon = CHANNEL_ICON.get(ch.get("type", CH_TEXT), "#")
        mark = "●" if unread else " "
        name = ch.get("name") or self.state.channel_name(channel_id)
        item.set_label(f" {mark}{icon} {name}",
                       style="bold white" if unread else "grey70")

    # --- selection ---------------------------------------------------------

    @on(ListView.Selected, "#guilds")
    async def _guild_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, DataItem):
            return
        self.current_guild_id = None if item.item_id == DM_SENTINEL else item.item_id
        await self._populate_channels(item.item_id)
        self.query_one(MemberList).show_guild(self.state, self.current_guild_id)
        if self.current_guild_id:
            await self.gateway.subscribe_guild(self.current_guild_id)

    @on(ListView.Selected, "#channels")
    async def _channel_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if not isinstance(item, DataItem) or not item.openable or not item.item_id:
            return
        await self._open_channel(item.item_id)

    async def _open_channel(self, channel_id: str) -> None:
        ch = self.state.channel(channel_id)
        guild_id = ch.get("guild_id") if ch else None
        self.current_channel_id = channel_id
        self.current_guild_id = guild_id
        self._reset_mode()

        name = self.state.channel_name(channel_id)
        topic = (ch or {}).get("topic") or ""
        header = Text(f"# {name}", style="bold")
        if topic:
            header.append(f"   {topic.splitlines()[0][:80]}", style="grey54")
        self.query_one("#chan-header", Static).update(header)

        mv = self.query_one(MessageView)
        mv.set_context(self.state, guild_id, self.state.me.get("id", ""), self.reveal)
        await mv.clear_messages()
        try:
            history = await self.rest.messages(channel_id, limit=50)
        except DiscordError as e:
            self.notify(f"Could not load messages: {e}", severity="error")
            return
        for msg in reversed(history):
            self.state.remember_author(msg.get("author", {}), guild_id)
            await mv.add_message(msg, follow=False)
        mv.scroll_end(animate=False)

        if history:
            self.state.set_read(channel_id, history[0].get("id"))
            await self.rest.ack(channel_id, history[0]["id"])
            self._refresh_channel_label(channel_id)

        composer = self.query_one("#composer", Input)
        composer.disabled = False
        composer.placeholder = f"Message #{name}    (/help for commands)"
        composer.focus()

    # --- input / commands --------------------------------------------------

    @on(Input.Submitted, "#composer")
    async def _on_submit(self, event: Input.Submitted) -> None:
        text = event.value
        event.input.value = ""
        if not text.strip():
            return
        if text.startswith("/"):
            await self._run_command(text[1:])
            return
        if not self.current_channel_id:
            self.notify("Open a channel first.", severity="warning")
            return
        cid = self.current_channel_id
        try:
            if self.mode == "edit" and self.mode_target:
                await self.rest.edit_message(cid, self.mode_target["id"], text)
            elif self.mode == "reply" and self.mode_target:
                await self.rest.send_message(cid, text, reply_to=self.mode_target["id"])
            elif self.mode == "react" and self.mode_target:
                await self.rest.add_reaction(cid, self.mode_target["id"], text.strip())
            elif self.mode == "search":
                await self._do_search(text)
            elif self.mode == "dm":
                await self._open_dm(text.strip())
            elif self.mode == "status":
                await self._set_status(text.strip())
            elif self.mode == "nick":
                await self._set_nick(text.strip())
            else:
                await self.rest.send_message(cid, text)
        except DiscordError as e:
            self.notify(f"{e}", severity="error")
        self._reset_mode()

    async def _run_command(self, raw: str) -> None:
        parts = raw.split(" ", 1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        cid = self.current_channel_id
        sel = self.query_one(MessageView).selected_message()

        try:
            if cmd in ("help", "h", "?"):
                self.push_screen(InfoScreen("Help", Text.from_markup(HELP_TEXT)))
            elif cmd == "quit":
                self.exit()
            elif cmd == "reconnect":
                await self.gateway.close()
                self.gateway = Gateway(self.token, self._on_gateway_event)
                self.run_worker(self.gateway.run(), name="gateway")
                self.notify("Reconnecting…")
            elif cmd == "reveal":
                self.action_reveal()
            elif cmd == "members":
                self.action_toggle_members()
                if self.current_guild_id:
                    await self.gateway.subscribe_guild(self.current_guild_id, cid)
            elif cmd == "status" and arg:
                await self._set_status(arg)
            elif cmd == "status":
                self._enter_mode("status", "Status: online | idle | dnd | invisible")
            elif cmd == "nick" and arg:
                await self._set_nick(arg)
            elif cmd == "dm" and arg:
                await self._open_dm(arg)
            elif cmd == "dm":
                self._enter_mode("dm", "Open DM with (name or id):")
            elif cmd == "search" and arg:
                await self._do_search(arg)
            elif cmd == "search":
                self._enter_mode("search", "Search this server for:")
            elif cmd == "profile" and arg:
                await self._show_profile(arg)
            elif cmd == "goto" and arg:
                self._goto_channel(arg)
            elif cmd == "upload" and arg and cid:
                await self.rest.upload_file(cid, arg)
                self.notify(f"Uploaded {arg}")
            elif cmd == "pins" and cid:
                await self._show_pins(cid)
            elif cmd == "threads" and self.current_guild_id:
                await self._show_threads(self.current_guild_id)
            elif cmd in ("reply", "edit", "delete", "react", "pin", "unpin") and sel and cid:
                await self._message_command(cmd, sel, arg)
            elif cmd == "shrug":
                await self.rest.send_message(cid, (arg + r" ¯\_(ツ)_/¯").strip())
            elif cmd == "tableflip":
                await self.rest.send_message(cid, (arg + " (╯°□°）╯︵ ┻━┻").strip())
            elif cmd == "unflip":
                await self.rest.send_message(cid, (arg + " ┬─┬ ノ( ゜-゜ノ)").strip())
            elif cmd == "spoiler" and cid:
                await self.rest.send_message(cid, f"||{arg}||")
            else:
                self.notify(f"Unknown or unavailable command: /{cmd}", severity="warning")
        except DiscordError as e:
            self.notify(f"{e}", severity="error")

    async def _message_command(self, cmd: str, sel: dict, arg: str) -> None:
        cid = self.current_channel_id
        if cmd == "reply":
            if arg:
                await self.rest.send_message(cid, arg, reply_to=sel["id"])
            else:
                self._enter_reply(sel)
        elif cmd == "edit":
            if arg:
                await self.rest.edit_message(cid, sel["id"], arg)
            else:
                self._enter_edit(sel)
        elif cmd == "delete":
            await self.rest.delete_message(cid, sel["id"])
        elif cmd == "react" and arg:
            await self.rest.add_reaction(cid, sel["id"], arg)
        elif cmd == "pin":
            await self.rest.pin_message(cid, sel["id"])
            self.notify("Pinned")
        elif cmd == "unpin":
            await self.rest.unpin_message(cid, sel["id"])
            self.notify("Unpinned")

    # --- message-view actions ---------------------------------------------

    @on(MessageView.Action)
    async def _msg_action(self, event: MessageView.Action) -> None:
        msg = event.target
        if not msg or not self.current_channel_id:
            return
        cid = self.current_channel_id
        act = event.action
        try:
            if act == "reply":
                self._enter_reply(msg)
            elif act == "edit":
                if (msg.get("author") or {}).get("id") == self.state.me.get("id"):
                    self._enter_edit(msg)
                else:
                    self.notify("You can only edit your own messages.", severity="warning")
            elif act == "delete":
                await self.rest.delete_message(cid, msg["id"])
            elif act == "react":
                self._enter_mode("react", "Emoji to react with (e.g. 🔥 or name:id):",
                                 target=msg)
            elif act == "pin":
                pinned = msg.get("pinned")
                fn = self.rest.unpin_message if pinned else self.rest.pin_message
                await fn(cid, msg["id"])
                self.notify("Unpinned" if pinned else "Pinned")
            elif act == "open":
                self._open_links(msg)
            elif act == "copy":
                await self._copy(msg["id"])
            elif act == "profile":
                await self._show_profile((msg.get("author") or {}).get("id", ""))
        except DiscordError as e:
            self.notify(f"{e}", severity="error")

    @on(MessageView.FocusInput)
    def _focus_input(self, _e: MessageView.FocusInput) -> None:
        self.query_one("#composer", Input).focus()

    @on(MessageView.LoadOlder)
    async def _load_older(self, _e: MessageView.LoadOlder) -> None:
        cid = self.current_channel_id
        mv = self.query_one(MessageView)
        before = mv.oldest_real_id()
        if not cid or not before:
            return
        try:
            older = await self.rest.messages(cid, limit=50, before=before)
        except DiscordError:
            return
        for msg in older:  # newest-first → prepend keeps chronological order
            self.state.remember_author(msg.get("author", {}), self.current_guild_id)
            await mv.add_message(msg, prepend=True)

    # --- input modes -------------------------------------------------------

    def _enter_mode(self, mode: str, placeholder: str, target: dict | None = None) -> None:
        self.mode = mode
        self.mode_target = target
        composer = self.query_one("#composer", Input)
        composer.placeholder = placeholder + "  (Esc to cancel)"
        composer.focus()

    def _enter_reply(self, msg: dict) -> None:
        name = self.state.user_name((msg.get("author") or {}).get("id", ""),
                                    self.current_guild_id)
        self._enter_mode("reply", f"Replying to @{name}", target=msg)

    def _enter_edit(self, msg: dict) -> None:
        self._enter_mode("edit", "Editing message", target=msg)
        composer = self.query_one("#composer", Input)
        composer.value = msg.get("content", "")
        composer.cursor_position = len(composer.value)

    def _reset_mode(self) -> None:
        self.mode = "send"
        self.mode_target = None
        composer = self.query_one("#composer", Input)
        if self.current_channel_id:
            composer.placeholder = f"Message #{self.state.channel_name(self.current_channel_id)}"

    # --- command implementations ------------------------------------------

    async def _do_search(self, query: str) -> None:
        if not self.current_guild_id:
            self.notify("Search works inside a server.", severity="warning")
            return
        try:
            result = await self.rest.search_messages(self.current_guild_id, query)
        except DiscordError as e:
            self.notify(f"Search failed: {e}", severity="error")
            return
        from . import render
        lines = []
        for group in result.get("messages", [])[:25]:
            msg = group[0] if isinstance(group, list) else group
            who = (msg.get("author") or {}).get("username", "?")
            chan = self.state.channel_name(msg.get("channel_id", ""))
            body = render.parse_inline(msg.get("content", ""), self.state,
                                       self.current_guild_id)
            line = Text(f"#{chan} ", style="grey50")
            line.append(f"@{who}: ", style="bold")
            line.append_text(body)
            lines.append(line)
        total = result.get("total_results", len(lines))
        body = Group(*lines) if lines else Text("No results.")
        self.push_screen(InfoScreen(f"Search: {query}  ({total} results)", body))

    async def _show_pins(self, channel_id: str) -> None:
        from . import render
        try:
            pins = await self.rest.pins(channel_id)
        except DiscordError as e:
            self.notify(f"{e}", severity="error")
            return
        lines = []
        for msg in pins:
            who = (msg.get("author") or {}).get("username", "?")
            line = Text(f"@{who}: ", style="bold")
            line.append_text(render.parse_inline(msg.get("content", ""), self.state,
                                                 self.current_guild_id))
            lines.append(line)
        body = Group(*lines) if lines else Text("No pinned messages.")
        self.push_screen(InfoScreen("Pinned messages", body))

    async def _show_threads(self, guild_id: str) -> None:
        try:
            data = await self.rest.active_threads(guild_id)
        except DiscordError as e:
            self.notify(f"{e}", severity="error")
            return
        lines = [Text(f"🧵 {t.get('name', '?')}") for t in data.get("threads", [])]
        body = Group(*lines) if lines else Text("No active threads.")
        self.push_screen(InfoScreen("Active threads", body))

    async def _show_profile(self, who: str) -> None:
        uid = self._resolve_user(who)
        if not uid:
            self.notify("User not found in cache.", severity="warning")
            return
        try:
            profile = await self.rest.user_profile(uid)
        except DiscordError as e:
            self.notify(f"{e}", severity="error")
            return
        user = profile.get("user", {})
        lines = [
            Text(user.get("global_name") or user.get("username", "?"), style="bold"),
            Text(f"@{user.get('username')}", style="grey62"),
            Text(""),
            Text(profile.get("user_profile", {}).get("bio") or "(no bio)", style="italic"),
        ]
        self.push_screen(InfoScreen("Profile", Group(*lines)))

    async def _open_dm(self, who: str) -> None:
        uid = self._resolve_user(who)
        if not uid:
            self.notify(f"No cached user matches '{who}'. Try a user id.", severity="warning")
            return
        try:
            channel = await self.rest.create_dm(uid)
        except DiscordError as e:
            self.notify(f"{e}", severity="error")
            return
        self.state._add_channel(channel)
        await self._open_channel(channel["id"])

    def _resolve_user(self, who: str) -> str:
        if who.isdigit():
            return who
        who = who.lstrip("@").lower()
        for u in self.state.users.values():
            haystack = (u.get("username", "") + (u.get("global_name") or "")).lower()
            if who in haystack:
                return u["id"]
        return ""

    async def _set_status(self, status: str) -> None:
        status = status.lower()
        if status not in ("online", "idle", "dnd", "invisible"):
            self.notify("Status must be online/idle/dnd/invisible.", severity="warning")
            return
        await self.gateway.update_presence(status)
        try:
            await self.rest.set_status(status)
        except DiscordError:
            pass
        self.notify(f"Status set to {status}")

    async def _set_nick(self, nick: str) -> None:
        if not self.current_guild_id:
            self.notify("Nicknames are per-server.", severity="warning")
            return
        await self.rest.set_nick(self.current_guild_id, nick)
        self.notify(f"Nickname set to {nick}")

    def _goto_channel(self, name: str) -> None:
        name = name.lstrip("#").lower()
        for cid in self._channel_items:
            ch = self.state.channel(cid)
            if ch and ch.get("name", "").lower() == name:
                self.run_worker(self._open_channel(cid))
                return
        self.notify(f"No channel named #{name} here.", severity="warning")

    def _open_links(self, msg: dict) -> None:
        import re
        urls = [a.get("url") for a in msg.get("attachments", []) if a.get("url")]
        urls += re.findall(r"https?://[^\s<>]+", msg.get("content", ""))
        if not urls:
            self.notify("No links in this message.", severity="warning")
            return
        webbrowser.open(urls[0])
        self.notify(f"Opening {urls[0][:50]}…")

    async def _copy(self, text: str) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "pbcopy", stdin=asyncio.subprocess.PIPE)
            await proc.communicate(text.encode())
            self.notify(f"Copied {text}")
        except OSError:
            self.notify(text)

    # --- gateway -----------------------------------------------------------

    async def _on_gateway_event(self, event: str, data: dict) -> None:
        self.post_message(self.GatewayEvent(event, data))

    @on(GatewayEvent)
    async def _handle_gateway(self, evt: GatewayEvent) -> None:
        e, d = evt.event, evt.data
        if e == "READY":
            self.state.load_ready(d)
            self._populate_guilds()
            name = self.state.me.get("global_name") or self.state.me.get("username")
            self.sub_title = f"{name} — connected"
            self.notify("Connected to Discord", timeout=2)
        elif e == "READY_SUPPLEMENTAL":
            self.state.load_ready_supplemental(d)
            self.query_one(MemberList).show_guild(self.state, self.current_guild_id)
        elif e == "MESSAGE_CREATE":
            await self._on_message_create(d)
        elif e == "MESSAGE_UPDATE":
            if d.get("channel_id") == self.current_channel_id:
                self.query_one(MessageView).update_message(d)
        elif e == "MESSAGE_DELETE":
            if d.get("channel_id") == self.current_channel_id:
                await self.query_one(MessageView).remove_message(d.get("id", ""))
        elif e in ("MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE"):
            self._on_reaction(e, d)
        elif e == "TYPING_START":
            self._on_typing(d)
        elif e == "PRESENCE_UPDATE":
            self.state.update_presence(d)
            if d.get("guild_id") == self.current_guild_id:
                self.query_one(MemberList).show_guild(self.state, self.current_guild_id)
        elif e in ("CHANNEL_CREATE", "CHANNEL_UPDATE"):
            self.state._add_channel(d)
        elif e == "CHANNEL_DELETE":
            self.state.remove_channel(d.get("id", ""))
        elif e == "GUILD_CREATE":
            self.state._add_guild(d)
            self._populate_guilds()
        elif e == "GUILD_DELETE" and not d.get("unavailable"):
            self.state.guilds.pop(d.get("id", ""), None)
            self._populate_guilds()
        elif e in ("GUILD_MEMBERS_CHUNK", "GUILD_MEMBER_LIST_UPDATE"):
            self._on_members(e, d)

    async def _on_message_create(self, d: dict) -> None:
        cid = d.get("channel_id")
        gid = d.get("guild_id")
        self.state.remember_author(d.get("author", {}), gid)
        ch = self.state.channel(cid)
        if ch is not None:
            ch["last_message_id"] = d.get("id")
        if cid == self.current_channel_id:
            mv = self.query_one(MessageView)
            follow = mv.at_bottom or \
                (d.get("author") or {}).get("id") == self.state.me.get("id")
            await mv.add_message(d, follow=follow)
            self.state.set_read(cid, d.get("id"))
            await self.rest.ack(cid, d["id"])
            self._typing.pop((d.get("author") or {}).get("id", ""), None)
            self._render_typing()
        elif cid in self._channel_items:
            self._refresh_channel_label(cid)
            self._maybe_notify(d)
        else:
            self._maybe_notify(d)

    def _on_reaction(self, event: str, d: dict) -> None:
        if d.get("channel_id") != self.current_channel_id:
            return
        delta = 1 if event.endswith("ADD") else -1
        is_me = d.get("user_id") == self.state.me.get("id")
        self.query_one(MessageView).adjust_reaction(
            d.get("message_id", ""), d.get("emoji", {}), delta, is_me)

    def _on_members(self, event: str, d: dict) -> None:
        gid = d.get("guild_id")
        if event == "GUILD_MEMBERS_CHUNK":
            for m in d.get("members", []):
                self.state.add_member(gid, m)
            for p in d.get("presences", []):
                self.state.update_presence(p)
        else:  # GUILD_MEMBER_LIST_UPDATE (response to op 14 subscribe)
            for op in d.get("ops", []):
                items = op.get("items", [])
                if op.get("item") is not None:
                    items = [op["item"]]
                for item in items:
                    member = item.get("member") if isinstance(item, dict) else None
                    if not member:
                        continue
                    self.state.add_member(gid, member)
                    if member.get("presence"):
                        self.state.update_presence(member["presence"])
        if gid == self.current_guild_id:
            self.query_one(MemberList).show_guild(self.state, self.current_guild_id)

    # --- typing + notifications -------------------------------------------

    def _on_typing(self, d: dict) -> None:
        if d.get("channel_id") != self.current_channel_id:
            return
        uid = d.get("user_id", "")
        if uid == self.state.me.get("id"):
            return
        self._typing[uid] = time.monotonic() + 8
        self._render_typing()

    def _tick_typing(self) -> None:
        now = time.monotonic()
        expired = [u for u, t in self._typing.items() if t < now]
        for u in expired:
            del self._typing[u]
        if expired:
            self._render_typing()

    def _render_typing(self) -> None:
        names = [self.state.user_name(u, self.current_guild_id) for u in self._typing]
        if not names:
            text = ""
        elif len(names) == 1:
            text = f"  {names[0]} is typing…"
        elif len(names) <= 3:
            text = f"  {', '.join(names[:-1])} and {names[-1]} are typing…"
        else:
            text = "  Several people are typing…"
        self.query_one("#typing", Static).update(Text(text, style="italic grey58"))

    def _maybe_notify(self, d: dict) -> None:
        me = self.state.me.get("id")
        mentioned = any(u.get("id") == me for u in d.get("mentions", [])) \
            or d.get("mention_everyone")
        ch = self.state.channel(d.get("channel_id", ""))
        is_dm = bool(ch) and ch.get("guild_id") is None
        if not (mentioned or is_dm):
            return
        author = (d.get("author") or {}).get("global_name") \
            or (d.get("author") or {}).get("username", "someone")
        body = d.get("content", "") or "[attachment]"
        self.notify(f"{author}: {body[:80]}", title="Tuicord", timeout=5)
        self.bell()
        now = time.monotonic()
        if now - self._last_notify > 3:
            self._last_notify = now
            self.run_worker(self._notify_os(author, body[:120]))

    async def _notify_os(self, title: str, body: str) -> None:
        def esc(s: str) -> str:
            return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
        script = f"display notification {esc(body)} with title {esc('Tuicord — ' + title)}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
        except OSError:
            pass

    # --- global actions ----------------------------------------------------

    def action_reload(self) -> None:
        if self.current_channel_id:
            self.run_worker(self._open_channel(self.current_channel_id))

    def action_toggle_members(self) -> None:
        members = self.query_one(MemberList)
        members.toggle_class("-hidden")
        if not members.has_class("-hidden"):
            members.show_guild(self.state, self.current_guild_id)

    def action_reveal(self) -> None:
        self.reveal = not self.reveal
        mv = self.query_one(MessageView)
        mv.reveal = self.reveal
        mv.refresh_all()
        self.notify(f"Spoilers {'revealed' if self.reveal else 'hidden'}")

    def action_focus_messages(self) -> None:
        self.query_one(MessageView).focus()

    def action_help(self) -> None:
        self.push_screen(InfoScreen("Help", Text.from_markup(HELP_TEXT)))

    def action_escape(self) -> None:
        if self.mode != "send":
            self._reset_mode()
            self.query_one("#composer", Input).value = ""
        else:
            self.query_one("#composer", Input).focus()
