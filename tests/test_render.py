"""Unit tests for the markdown / content renderer."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console  # noqa: E402

from tuicord import render  # noqa: E402
from tuicord.state import State  # noqa: E402


def make_state() -> State:
    s = State()
    s.users["111"] = {"id": "111", "username": "alice", "global_name": "Alice"}
    s.users["222"] = {"id": "222", "username": "bob"}
    s.members[("g1", "111")] = {"user": s.users["111"], "nick": "Ali", "roles": ["700"]}
    s.roles["700"] = {"id": "700", "name": "Admins", "color": 0xFF0000, "position": 5}
    s.guild_role_ids["g1"] = ["700"]
    s.channels["800"] = {"id": "800", "name": "general", "guild_id": "g1", "type": 0}
    s.emojis["999"] = {"id": "999", "name": "pepe"}
    return s


def plain(text) -> str:
    """Render a Rich object to a plain string (styles stripped)."""
    console = Console(width=80, no_color=True)
    with console.capture() as cap:
        console.print(text, end="")
    return cap.get()


def test_user_mention_uses_nick_in_guild():
    s = make_state()
    t = render.parse_inline("hi <@111>!", s, guild_id="g1")
    assert "@Ali" in t.plain
    # outside the guild, falls back to global name
    t2 = render.parse_inline("hi <@111>!", s)
    assert "@Alice" in t2.plain


def test_role_and_channel_mentions():
    s = make_state()
    assert "@Admins" in render.parse_inline("<@&700>", s).plain
    assert "#general" in render.parse_inline("<#800>", s).plain


def test_custom_emoji_and_everyone():
    s = make_state()
    assert ":pepe:" in render.parse_inline("<:pepe:999>", s).plain
    assert "@everyone" in render.parse_inline("yo @everyone", s).plain


def test_emphasis_strip_markers():
    s = make_state()
    t = render.parse_inline("**bold** and *italic* and ~~no~~", s)
    assert "**" not in t.plain and "~~" not in t.plain
    assert "bold" in t.plain and "italic" in t.plain


def test_bold_actually_styled():
    s = make_state()
    t = render.parse_inline("a **b** c", s)
    spans = [t.plain[sp.start:sp.end] for sp in t.spans if "bold" in str(sp.style)]
    assert "b" in spans


def test_spoiler_hidden_then_revealed():
    s = make_state()
    hidden = render.parse_inline("a ||secret|| b", s, reveal_spoilers=False)
    assert "secret" not in hidden.plain
    shown = render.parse_inline("a ||secret|| b", s, reveal_spoilers=True)
    assert "secret" in shown.plain


def test_inline_code_atomic():
    s = make_state()
    t = render.parse_inline("use `**not bold**` ok", s)
    assert "**not bold**" in t.plain  # markers preserved inside code


def test_escape():
    s = make_state()
    t = render.parse_inline(r"not \*\*bold\*\*", s)
    assert "**bold**" in t.plain


def test_timestamp():
    out = render.format_discord_timestamp(0, "d")
    assert "1970" in out


def test_fenced_code_block():
    s = make_state()
    blocks = render.content_to_renderables("```python\nprint(1)\n```", s)
    assert any("print(1)" in plain(b) for b in blocks)


def test_masked_link():
    s = make_state()
    t = render.parse_inline("see [docs](https://x.com/y)", s)
    assert "docs" in t.plain and "https://x.com/y" not in t.plain


def test_reactions_and_reply():
    s = make_state()
    rx = render.render_reactions([{"emoji": {"name": "🔥"}, "count": 3, "me": True}], s)
    assert "🔥" in rx.plain and "3" in rx.plain
    ref = {"author": {"username": "bob"}, "content": "original"}
    rp = render.render_reply_preview(ref, s)
    assert "@bob" in rp.plain and "original" in rp.plain


if __name__ == "__main__":
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
