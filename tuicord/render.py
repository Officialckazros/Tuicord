"""Render Discord message content into Rich renderables.

Handles Discord-flavored markdown (bold/italic/underline/strike/spoiler, inline
and fenced code, blockquotes, headers, lists), entity tokens (user/role/channel
mentions, @everyone/@here, custom emoji, <t:...> timestamps, masked links), plus
embeds, attachments, reactions and reply previews.

Pure functions over the :class:`~tuicord.state.State` cache so they're easy to
unit-test without a network or a running UI.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .state import State

# --- entity regexes --------------------------------------------------------

RE_USER = re.compile(r"<@!?(\d+)>")
RE_ROLE = re.compile(r"<@&(\d+)>")
RE_CHANNEL = re.compile(r"<#(\d+)>")
RE_EMOJI = re.compile(r"<(a)?:(\w+):(\d+)>")
RE_TIMESTAMP = re.compile(r"<t:(-?\d+)(?::([tTdDfFR]))?>")
RE_EVERYONE = re.compile(r"@(everyone|here)\b")
RE_MASKED_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
RE_URL = re.compile(r"https?://[^\s<>]+")
RE_INLINE_CODE = re.compile(r"(`{1,2})(.+?)\1", re.DOTALL)
RE_NAV = re.compile(r"<id:(\w+)>")

MENTION_STYLE = "bold #c9d1f9 on #3a3d5c"
CHANNEL_STYLE = "bold #8aa6ff"
EMOJI_STYLE = "yellow"
LINK_STYLE = "underline #5b9bff"
CODE_STYLE = "#e6db74 on grey15"
TS_STYLE = "italic #9bd1ff"

EMPHASIS_MARKERS = ["***", "**", "__", "~~", "||", "*", "_", "`"]
MARKER_STYLE = {
    "***": ("bold", "italic"),
    "**": ("bold",),
    "*": ("italic",),
    "_": ("italic",),
    "__": ("underline",),
    "~~": ("strike",),
}

# --- timestamps ------------------------------------------------------------


def format_discord_timestamp(unix: int, style: str = "f") -> str:
    try:
        dt = datetime.fromtimestamp(unix, tz=timezone.utc).astimezone()
    except (OverflowError, OSError, ValueError):
        return "<invalid-time>"
    if style == "t":
        return dt.strftime("%H:%M")
    if style == "T":
        return dt.strftime("%H:%M:%S")
    if style == "d":
        return dt.strftime("%m/%d/%Y")
    if style == "D":
        return dt.strftime("%B %-d, %Y")
    if style == "F":
        return dt.strftime("%A, %B %-d, %Y %H:%M")
    if style == "R":
        return _relative(dt)
    return dt.strftime("%B %-d, %Y %H:%M")  # 'f' default


def _relative(dt: datetime) -> str:
    delta = datetime.now(tz=timezone.utc).astimezone() - dt
    secs = int(delta.total_seconds())
    future = secs < 0
    secs = abs(secs)
    for unit, size in (("year", 31536000), ("month", 2592000), ("day", 86400),
                       ("hour", 3600), ("minute", 60)):
        if secs >= size:
            n = secs // size
            label = f"{n} {unit}{'s' if n != 1 else ''}"
            return f"in {label}" if future else f"{label} ago"
    return "just now"


def format_time(iso: str | None) -> str:
    if not iso:
        return "--:--"
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%H:%M")
    except ValueError:
        return "--:--"


# --- inline parsing --------------------------------------------------------


def parse_inline(
    text: str, state: State, guild_id: str | None = None, reveal_spoilers: bool = False
) -> Text:
    """Parse one line of Discord markdown into a Rich Text."""
    out = Text()
    open_markers: list[str] = []
    i, n = 0, len(text)

    def styles_now() -> list[str]:
        s: list[str] = []
        spoiler = False
        for m in open_markers:
            if m == "||":
                spoiler = True
            elif m in MARKER_STYLE:
                s.extend(MARKER_STYLE[m])
        if spoiler:
            s.append("__spoiler__")
        return s

    def emit(segment: str, extra: str = "") -> None:
        styles = styles_now()
        spoiler = "__spoiler__" in styles
        styles = [s for s in styles if s != "__spoiler__"]
        if spoiler and not reveal_spoilers:
            segment = "░" * len(segment)
            styles = ["#43465a on #43465a"]
            extra = ""
        elif spoiler:
            styles.append("on #43465a")
        style = " ".join([*styles, extra]).strip()
        out.append(segment, style=style or None)

    while i < n:
        ch = text[i]

        if ch == "\\" and i + 1 < n:  # escape
            emit(text[i + 1])
            i += 2
            continue

        if ch == "`":  # inline code (atomic, no inner markdown)
            m = RE_INLINE_CODE.match(text, i)
            if m:
                emit(m.group(2), CODE_STYLE)
                i = m.end()
                continue

        if ch == "<":  # entity tokens
            consumed = _try_entity(text, i, state, guild_id, emit, out)
            if consumed:
                i += consumed
                continue

        if ch == "[":  # masked link
            m = RE_MASKED_LINK.match(text, i)
            if m:
                out.append(m.group(1), style=LINK_STYLE)
                i = m.end()
                continue

        if ch == "h":  # bare url
            m = RE_URL.match(text, i)
            if m:
                emit(m.group(0), LINK_STYLE)
                i = m.end()
                continue

        if ch == "@":  # @everyone / @here
            m = RE_EVERYONE.match(text, i)
            if m:
                emit("@" + m.group(1), "bold #f9c9c9 on #5c3a3a")
                i = m.end()
                continue

        marker = _marker_at(text, i)
        if marker:
            if marker in open_markers:
                open_markers.reverse()
                open_markers.remove(marker)
                open_markers.reverse()
            else:
                open_markers.append(marker)
            i += len(marker)
            continue

        emit(ch)
        i += 1

    return out


def _marker_at(text: str, i: str) -> str | None:
    for marker in EMPHASIS_MARKERS:
        if marker == "`":
            continue
        if text.startswith(marker, i):
            return marker
    return None


def _try_entity(text, i, state, guild_id, emit, out) -> int:
    m = RE_USER.match(text, i)
    if m:
        emit("@" + state.user_name(m.group(1), guild_id), MENTION_STYLE)
        return m.end() - i
    m = RE_ROLE.match(text, i)
    if m:
        role = state.role(m.group(1))
        name = role.get("name", "role") if role else "role"
        from .state import color_hex
        color = color_hex(role.get("color")) if role else None
        emit("@" + name, f"bold {color}" if color else MENTION_STYLE)
        return m.end() - i
    m = RE_CHANNEL.match(text, i)
    if m:
        emit("#" + state.channel_name(m.group(1)), CHANNEL_STYLE)
        return m.end() - i
    m = RE_EMOJI.match(text, i)
    if m:
        emit(f":{m.group(2)}:", EMOJI_STYLE)
        return m.end() - i
    m = RE_TIMESTAMP.match(text, i)
    if m:
        emit(format_discord_timestamp(int(m.group(1)), m.group(2) or "f"), TS_STYLE)
        return m.end() - i
    m = RE_NAV.match(text, i)
    if m:
        emit(m.group(1).replace("_", " ").title(), CHANNEL_STYLE)
        return m.end() - i
    return 0


# --- block parsing ---------------------------------------------------------


def content_to_renderables(
    content: str, state: State, guild_id: str | None = None, reveal_spoilers: bool = False
) -> list[RenderableType]:
    if not content:
        return []
    blocks: list[RenderableType] = []
    pending = Text()

    def flush() -> None:
        nonlocal pending
        if pending.plain.strip("\n"):
            blocks.append(pending)
        pending = Text()

    lines = content.split("\n")
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        fence = re.match(r"^```(\w+)?\s*$", line)
        if fence:
            code_lines: list[str] = []
            idx += 1
            while idx < len(lines) and not lines[idx].startswith("```"):
                code_lines.append(lines[idx])
                idx += 1
            idx += 1  # skip closing fence
            flush()
            lang = fence.group(1) or "text"
            code = "\n".join(code_lines)
            try:
                blocks.append(Syntax(code, lang, theme="ansi_dark", word_wrap=True,
                                     background_color="#1a1b26"))
            except Exception:  # noqa: BLE001
                blocks.append(Text(code, style=CODE_STYLE))
            continue

        styled = _styled_line(line, state, guild_id, reveal_spoilers)
        if pending.plain:
            pending.append("\n")
        pending.append_text(styled)
        idx += 1

    flush()
    return blocks


def _styled_line(line: str, state: State, guild_id, reveal) -> Text:
    # block-level prefixes
    h = re.match(r"^(#{1,3})\s+(.*)$", line)
    if h:
        inner = parse_inline(h.group(2), state, guild_id, reveal)
        inner.stylize("bold #ffffff")
        return inner
    if line.startswith("-# "):
        inner = parse_inline(line[3:], state, guild_id, reveal)
        inner.stylize("dim")
        return inner
    if line.startswith(">>> "):
        inner = parse_inline(line[4:], state, guild_id, reveal)
        return Text("▎ ", style="grey50") + inner
    if line.startswith("> "):
        inner = parse_inline(line[2:], state, guild_id, reveal)
        return Text("▎ ", style="grey50") + inner
    m = re.match(r"^(\s*)([-*])\s+(.*)$", line)
    if m:
        inner = parse_inline(m.group(3), state, guild_id, reveal)
        return Text(f"{m.group(1)} • ", style="grey50") + inner
    m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
    if m:
        inner = parse_inline(m.group(3), state, guild_id, reveal)
        return Text(f"{m.group(1)} {m.group(2)}. ", style="grey50") + inner
    return parse_inline(line, state, guild_id, reveal)


# --- embeds / attachments / reactions / replies ----------------------------


def render_embed(embed: dict, state: State, guild_id: str | None = None) -> RenderableType:
    from .state import color_hex

    parts: list[RenderableType] = []
    author = embed.get("author") or {}
    if author.get("name"):
        parts.append(Text(author["name"], style="bold"))
    if embed.get("title"):
        title = parse_inline(embed["title"], state, guild_id)
        title.stylize("bold #8aa6ff")
        parts.append(title)
    if embed.get("description"):
        parts.extend(content_to_renderables(embed["description"], state, guild_id))
    fields = embed.get("fields") or []
    if fields:
        table = Table.grid(padding=(0, 2))
        table.add_column()
        for f in fields:
            name = Text(f.get("name", ""), style="bold")
            value = parse_inline(f.get("value", ""), state, guild_id)
            table.add_row(name)
            table.add_row(value)
        parts.append(table)
    footer = embed.get("footer") or {}
    if footer.get("text"):
        parts.append(Text(footer["text"], style="dim"))
    if embed.get("image", {}).get("url"):
        parts.append(Text(f"🖼  {embed['image']['url']}", style=LINK_STYLE))

    color = color_hex(embed.get("color")) or "grey50"
    return Panel(Group(*parts) if parts else Text(""), border_style=color,
                 padding=(0, 1), expand=False)


def render_attachments(attachments: list[dict]) -> list[RenderableType]:
    out: list[RenderableType] = []
    for att in attachments:
        name = att.get("filename", "file")
        url = att.get("url", "")
        size = att.get("size", 0)
        icon = "🖼 " if (att.get("content_type") or "").startswith("image") else "📎"
        out.append(Text(f"{icon} {name} ({_human_size(size)})  {url}", style=LINK_STYLE))
    return out


def render_reactions(reactions: list[dict], state: State) -> Text | None:
    if not reactions:
        return None
    out = Text()
    for r in reactions:
        emoji = r.get("emoji", {})
        name = emoji.get("name") or "?"
        label = f":{name}:" if emoji.get("id") else name
        count = r.get("count", 0)
        style = "bold #5b9bff on #2b3158" if r.get("me") else "grey70 on grey19"
        out.append(f" {label} {count} ", style=style)
        out.append(" ")
    return out


def render_reply_preview(ref: dict, state: State, guild_id: str | None = None) -> Text:
    author = ref.get("author") or {}
    name = author.get("global_name") or author.get("username") or "unknown"
    snippet = (ref.get("content") or "").replace("\n", " ")
    if len(snippet) > 70:
        snippet = snippet[:70] + "…"
    if not snippet and ref.get("attachments"):
        snippet = "[attachment]"
    if not snippet and ref.get("embeds"):
        snippet = "[embed]"
    line = Text("╭─ ", style="grey46")
    line.append("@" + name, style="#8aa6ff")
    line.append("  " + snippet, style="dim italic")
    return line


def _human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}TB"
