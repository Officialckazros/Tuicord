"""Custom Textual widgets: the message view, message rows, and member list."""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from . import render
from .state import State

MAX_MESSAGES = 300

# Message type → system-message phrasing (subset of Discord's types)
SYSTEM_MESSAGES = {
    1: "added someone to the group",
    2: "removed someone from the group",
    3: "started a call",
    4: "changed the channel name",
    5: "changed the channel icon",
    6: "pinned a message to this channel",
    7: "joined the server",
    8: "boosted the server",
    11: "started an event",
    12: "started an event",
}


class MessageWidget(Static):
    """One message (or a compacted continuation) rendered in place."""

    DEFAULT_CSS = """
    MessageWidget { padding: 0 1; }
    MessageWidget.-selected { background: $boost; }
    MessageWidget.-mention { border-left: thick $warning; }
    """

    def __init__(self, message: dict, ctx: "MessageView") -> None:
        super().__init__()
        self.message = message
        self.ctx = ctx
        self.message_id = message.get("id", "")

    def on_mount(self) -> None:
        self._refresh()
        if self._mentions_me():
            self.add_class("-mention")

    def update_message(self, message: dict) -> None:
        self.message = message
        self._refresh()

    def _mentions_me(self) -> bool:
        me = self.ctx.me_id
        if any(u.get("id") == me for u in self.message.get("mentions", [])):
            return True
        return self.message.get("mention_everyone", False)

    def _refresh(self) -> None:
        self.update(self._build())

    def _build(self) -> RenderableType:
        msg = self.message
        state = self.ctx.state
        gid = self.ctx.guild_id
        parts: list[RenderableType] = []

        mtype = msg.get("type", 0)
        if mtype in SYSTEM_MESSAGES:
            who = (msg.get("author") or {}).get("global_name") \
                or (msg.get("author") or {}).get("username", "someone")
            line = Text("→ ", style="grey50")
            line.append(who, style="bold")
            line.append(" " + SYSTEM_MESSAGES[mtype], style="grey62")
            return line

        ref = msg.get("referenced_message")
        if ref:
            parts.append(render.render_reply_preview(ref, state, gid))

        if not self.ctx.is_continuation(msg):
            parts.append(self._header())

        body = render.content_to_renderables(
            msg.get("content", ""), state, gid, self.ctx.reveal)
        parts.extend(body)

        for embed in msg.get("embeds", []) or []:
            parts.append(render.render_embed(embed, state, gid))
        parts.extend(render.render_attachments(msg.get("attachments", []) or []))

        reactions = render.render_reactions(msg.get("reactions", []) or [], state)
        if reactions:
            parts.append(reactions)

        if not parts:
            parts.append(Text(""))
        return Group(*parts)

    def _header(self) -> Text:
        msg = self.message
        author = msg.get("author", {})
        aid = author.get("id", "")
        name = self.ctx.state.user_name(aid, self.ctx.guild_id)
        color = self.ctx.state.member_color(self.ctx.guild_id, aid) or "#dcdcff"

        header = Text()
        header.append(render.format_time(msg.get("timestamp")) + " ", style="grey42")
        header.append(name, style=f"bold {color}")
        if author.get("bot"):
            header.append(" BOT", style="bold #ffffff on #5865f2")
        if msg.get("edited_timestamp"):
            header.append("  (edited)", style="dim italic")
        if msg.get("pinned"):
            header.append("  📌", style="grey50")
        return header


class MessageView(VerticalScroll):
    """Scrollable, keyboard-navigable list of messages with in-place updates."""

    can_focus = True

    BINDINGS = [
        Binding("j,down", "cursor(1)", "Down", show=False),
        Binding("k,up", "cursor(-1)", "Up", show=False),
        Binding("g", "cursor_edge(-1)", "Top", show=False),
        Binding("G", "cursor_edge(1)", "Bottom", show=False),
        Binding("ctrl+u,pageup", "load_older", "Older", show=False),
        Binding("r", "msg('reply')", "Reply"),
        Binding("e", "msg('edit')", "Edit"),
        Binding("d", "msg('delete')", "Delete"),
        Binding("a", "msg('react')", "React"),
        Binding("p", "msg('pin')", "Pin"),
        Binding("o", "msg('open')", "Open"),
        Binding("c", "msg('copy')", "Copy ID"),
        Binding("u", "msg('profile')", "Profile"),
        Binding("enter,i,escape", "focus_input", "Type"),
    ]

    selected_index: reactive[int] = reactive(-1)

    class Action(Message):
        def __init__(self, action: str, target: dict | None) -> None:
            super().__init__()
            self.action = action
            self.target = target

    class FocusInput(Message):
        pass

    class LoadOlder(Message):
        pass

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.state = State()
        self.guild_id: str | None = None
        self.me_id: str = ""
        self.reveal = False
        self._order: list[str] = []
        self._widgets: dict[str, MessageWidget] = {}

    def set_context(self, state: State, guild_id: str | None, me_id: str, reveal: bool) -> None:
        self.state = state
        self.guild_id = guild_id
        self.me_id = me_id
        self.reveal = reveal

    def is_continuation(self, msg: dict) -> bool:
        """True if this message should be drawn without a fresh header."""
        try:
            pos = self._order.index(msg.get("id", ""))
        except ValueError:
            return False
        if pos == 0:
            return False
        prev = self._widgets[self._order[pos - 1]].message
        if msg.get("type", 0) != 0 or prev.get("type", 0) != 0:
            return False
        if (prev.get("author") or {}).get("id") != (msg.get("author") or {}).get("id"):
            return False
        if msg.get("referenced_message"):
            return False
        return _close_in_time(prev.get("timestamp"), msg.get("timestamp"))

    async def clear_messages(self) -> None:
        self._order.clear()
        self._widgets.clear()
        self.selected_index = -1
        await self.remove_children()

    async def add_message(self, msg: dict, *, prepend: bool = False,
                          follow: bool = True) -> None:
        mid = msg.get("id")
        if not mid or mid in self._widgets:
            if mid in self._widgets:
                self._widgets[mid].update_message(msg)
            return
        widget = MessageWidget(msg, self)
        self._widgets[mid] = widget
        if prepend:
            self._order.insert(0, mid)
            first = self.children[0] if self.children else None
            await self.mount(widget, before=first)
        else:
            self._order.append(mid)
            await self.mount(widget)
            if follow:
                self.scroll_end(animate=False)
        await self._trim()

    def update_message(self, msg: dict) -> None:
        w = self._widgets.get(msg.get("id", ""))
        if w:
            merged = {**w.message, **msg}
            w.update_message(merged)

    async def remove_message(self, message_id: str) -> None:
        w = self._widgets.pop(message_id, None)
        if w:
            if message_id in self._order:
                self._order.remove(message_id)
            await w.remove()

    async def _trim(self) -> None:
        while len(self._order) > MAX_MESSAGES:
            oldest = self._order.pop(0)
            w = self._widgets.pop(oldest, None)
            if w:
                await w.remove()

    def oldest_id(self) -> str | None:
        return self._order[0] if self._order else None

    def oldest_real_id(self) -> str | None:
        """The oldest server (snowflake) id, skipping synthetic rows."""
        for mid in self._order:
            if mid.isdigit():
                return mid
        return None

    def refresh_all(self) -> None:
        for w in self._widgets.values():
            w.update_message(w.message)

    @property
    def at_bottom(self) -> bool:
        return self.max_scroll_y == 0 or self.scroll_offset.y >= self.max_scroll_y - 2

    def adjust_reaction(self, message_id: str, emoji: dict, delta: int, is_me: bool) -> None:
        w = self._widgets.get(message_id)
        if not w:
            return
        reactions = list(w.message.get("reactions", []) or [])
        key = emoji.get("id") or emoji.get("name")
        for r in reactions:
            re = r.get("emoji", {})
            if (re.get("id") or re.get("name")) == key:
                r["count"] = max(0, r.get("count", 0) + delta)
                if is_me:
                    r["me"] = delta > 0
                break
        else:
            if delta > 0:
                reactions.append({"emoji": emoji, "count": 1, "me": is_me})
        w.message["reactions"] = [r for r in reactions if r.get("count", 0) > 0]
        w.update_message(w.message)

    def selected_message(self) -> dict | None:
        if 0 <= self.selected_index < len(self._order):
            return self._widgets[self._order[self.selected_index]].message
        return None

    # --- navigation --------------------------------------------------------

    def on_focus(self) -> None:
        if self.selected_index < 0 and self._order:
            self._move_to(len(self._order) - 1)

    def action_cursor(self, delta: int) -> None:
        if not self._order:
            return
        if self.selected_index < 0:
            self._move_to(len(self._order) - 1)
            return
        new = self.selected_index + delta
        if new < 0:
            self.post_message(self.LoadOlder())
            new = 0
        new = max(0, min(new, len(self._order) - 1))
        self._move_to(new)

    def action_cursor_edge(self, direction: int) -> None:
        if self._order:
            self._move_to(len(self._order) - 1 if direction > 0 else 0)

    def _move_to(self, index: int) -> None:
        for i, mid in enumerate(self._order):
            self._widgets[mid].set_class(i == index, "-selected")
        self.selected_index = index
        self._widgets[self._order[index]].scroll_visible(animate=False)

    def action_msg(self, action: str) -> None:
        self.post_message(self.Action(action, self.selected_message()))

    def action_focus_input(self) -> None:
        self.post_message(self.FocusInput())

    def action_load_older(self) -> None:
        self.post_message(self.LoadOlder())


class MemberList(VerticalScroll):
    """Read-only roster for the current guild, grouped by online/offline."""

    DEFAULT_CSS = """
    MemberList { width: 24; border-left: solid $accent-darken-1; padding: 0 1; }
    MemberList.-hidden { display: none; }
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._body = Static("")

    def compose(self):
        yield self._body

    def show_guild(self, state: State, guild_id: str | None) -> None:
        if not guild_id:
            self._body.update(Text("No member list for DMs", style="dim"))
            return
        online: list[Text] = []
        offline: list[Text] = []
        for (gid, uid), member in state.members.items():
            if gid != guild_id:
                continue
            name = member.get("nick") or state.user_name(uid, guild_id)
            status = state.presences.get(uid, "offline")
            dot = state.presence_dot(uid)
            color = state.member_color(guild_id, uid) or "grey78"
            line = Text()
            line.append(dot + " ", style=_dot_style(status))
            line.append(name, style=color if status != "offline" else "grey42")
            (online if status in ("online", "idle", "dnd") else offline).append(line)

        online.sort(key=lambda t: t.plain.lower())
        offline.sort(key=lambda t: t.plain.lower())
        parts: list[RenderableType] = []
        parts.append(Text(f"ONLINE — {len(online)}", style="bold grey62"))
        parts.extend(online or [Text("(none loaded)", style="dim")])
        if offline:
            parts.append(Text(""))
            parts.append(Text(f"OFFLINE — {len(offline)}", style="bold grey42"))
            parts.extend(offline[:100])
        self._body.update(Group(*parts))


def _dot_style(status: str) -> str:
    return {
        "online": "#3ba55d",
        "idle": "#faa81a",
        "dnd": "#ed4245",
    }.get(status, "grey42")


def _close_in_time(a: str | None, b: str | None, *, max_seconds: int = 420) -> bool:
    from datetime import datetime
    if not a or not b:
        return False
    try:
        ta = datetime.fromisoformat(a)
        tb = datetime.fromisoformat(b)
    except ValueError:
        return False
    return abs((tb - ta).total_seconds()) <= max_seconds
