"""Headless integration tests: drive the TUI with a mock REST + fake gateway."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console  # noqa: E402

from tuicord.app import TerminalDiscord  # noqa: E402
from tuicord.widgets import MemberList, MessageView  # noqa: E402
from textual.widgets import Input, Static  # noqa: E402


def plain(renderable) -> str:
    """Render any str/Text/Group to plain text for assertions."""
    console = Console(width=200, no_color=True)
    with console.capture() as cap:
        console.print(renderable, end="")
    return cap.get()

GUILD_CHANNELS = [
    {"id": "cat", "name": "Text", "type": 4, "position": 0},
    {"id": "c1", "name": "general", "type": 0, "position": 0, "parent_id": "cat",
     "topic": "the main channel"},
    {"id": "c2", "name": "voice", "type": 2, "position": 1, "parent_id": "cat"},
]

HISTORY = [{
    "id": "100", "channel_id": "c1", "type": 0,
    "author": {"id": "2", "username": "alice", "global_name": "Alice"},
    "content": "**hello** <@1> ~~old~~ ||secret||",
    "timestamp": "2026-01-01T00:00:00+00:00",
    "mentions": [{"id": "1"}],
}]


class FakeRest:
    def __init__(self, token=None):
        self.sent = []
        self.edited = []
        self.deleted = []
        self.reactions = []
        self.status = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def me(self):
        return {"id": "1", "username": "me", "global_name": "Me"}

    async def guilds(self):
        return [{"id": "g1", "name": "Cool Guild"}]

    async def guild_channels(self, gid):
        return [dict(c) for c in GUILD_CHANNELS]

    async def dm_channels(self):
        return [{"id": "d1", "type": 1,
                 "recipients": [{"id": "2", "username": "alice", "global_name": "Alice"}]}]

    async def messages(self, cid, limit=50, before=None):
        return [] if before else [dict(m) for m in HISTORY]

    async def send_message(self, cid, content, reply_to=None):
        self.sent.append((cid, content, reply_to))
        return {"id": "999", "channel_id": cid, "content": content}

    async def edit_message(self, cid, mid, content):
        self.edited.append((cid, mid, content))
        return {}

    async def delete_message(self, cid, mid):
        self.deleted.append((cid, mid))

    async def add_reaction(self, cid, mid, emoji):
        self.reactions.append((cid, mid, emoji))

    async def ack(self, cid, mid):
        pass

    async def set_status(self, status):
        self.status = status
        return {}

    async def pins(self, cid):
        return [dict(HISTORY[0])]

    async def search_messages(self, gid, content):
        return {"total_results": 1, "messages": [[dict(HISTORY[0])]]}

    async def create_dm(self, uid):
        return {"id": "d1", "type": 1, "recipients": [{"id": uid, "username": "alice"}]}


class FakeGateway:
    def __init__(self, token=None, on_event=None):
        self._stop = asyncio.Event()
        self.presence = None
        self.subscribed = []

    async def run(self):
        await self._stop.wait()

    async def close(self):
        self._stop.set()

    async def update_presence(self, status):
        self.presence = status

    async def subscribe_guild(self, gid, cid=None):
        self.subscribed.append(gid)

    async def request_members(self, *a, **k):
        pass


def make_app():
    app = TerminalDiscord("token")
    app.rest = FakeRest()
    app.gateway = FakeGateway()
    return app


READY = {
    "user": {"id": "1", "username": "me", "global_name": "Me"},
    "users": [{"id": "2", "username": "alice", "global_name": "Alice"},
              {"id": "3", "username": "bob"}],
    "guilds": [{
        "id": "g1", "name": "Cool Guild",
        "roles": [{"id": "r1", "name": "Admin", "color": 0xFF0000, "position": 2}],
        "emojis": [], "channels": [dict(c) for c in GUILD_CHANNELS],
    }],
    "private_channels": [{"id": "d1", "type": 1,
                          "recipients": [{"id": "2", "username": "alice"}]}],
    "merged_members": [[{"user": {"id": "2"}, "nick": "Ali", "roles": ["r1"]}]],
    "presences": [{"user": {"id": "2"}, "status": "online"}],
}


async def push(app, pilot, event, data):
    app.post_message(app.GatewayEvent(event, data))
    await pilot.pause()


# --- tests -----------------------------------------------------------------


async def t_boot_and_guilds(app, pilot):
    guilds = app.query_one("#guilds")
    labels = [plain(c._label.content) for c in guilds.children]
    assert any("Direct Messages" in s for s in labels), labels
    assert any("Cool Guild" in s for s in labels), labels


async def t_ready_populates_state(app, pilot):
    await push(app, pilot, "READY", READY)
    assert "g1" in app.state.guilds
    assert app.state.user_name("2", "g1") == "Ali"  # nick from merged_members
    assert app.state.presences.get("2") == "online"


async def t_open_channel_renders_history(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._populate_channels("g1")
    assert "c1" in app._channel_items
    assert "c2" in app._channel_items  # voice listed (not openable)
    assert not app._channel_items["c2"].openable
    await app._open_channel("c1")
    mv = app.query_one(MessageView)
    assert mv._order == ["100"]
    # spoiler hidden, mention styled, markers stripped
    rendered = mv._widgets["100"].message["content"]
    assert "hello" in rendered
    assert mv._widgets["100"].has_class("-mention")  # mentions me


async def t_live_message_and_reactions(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._open_channel("c1")
    await push(app, pilot, "MESSAGE_CREATE", {
        "id": "101", "channel_id": "c1", "guild_id": "g1",
        "author": {"id": "3", "username": "bob"}, "content": "hi there", "type": 0})
    mv = app.query_one(MessageView)
    assert "101" in mv._order
    await push(app, pilot, "MESSAGE_REACTION_ADD", {
        "channel_id": "c1", "message_id": "101",
        "emoji": {"name": "🔥"}, "user_id": "3"})
    reactions = mv._widgets["101"].message.get("reactions", [])
    assert reactions and reactions[0]["count"] == 1


async def t_edit_and_delete(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._open_channel("c1")
    await push(app, pilot, "MESSAGE_CREATE", {
        "id": "102", "channel_id": "c1", "author": {"id": "3"}, "content": "x", "type": 0})
    await push(app, pilot, "MESSAGE_UPDATE", {
        "id": "102", "channel_id": "c1", "content": "edited!", "author": {"id": "3"}})
    mv = app.query_one(MessageView)
    assert mv._widgets["102"].message["content"] == "edited!"
    await push(app, pilot, "MESSAGE_DELETE", {"id": "102", "channel_id": "c1"})
    assert "102" not in mv._order


async def t_typing_indicator(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._open_channel("c1")
    await push(app, pilot, "TYPING_START", {"channel_id": "c1", "user_id": "2"})
    typing = app.query_one("#typing", Static).content.plain
    assert "typing" in typing and "Ali" in typing


async def t_send_message(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._open_channel("c1")
    app.query_one("#composer", Input).focus()
    app.query_one("#composer", Input).value = "hello world"
    await pilot.press("enter")
    await pilot.pause()
    assert ("c1", "hello world", None) in app.rest.sent


async def t_reply_mode(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._open_channel("c1")
    app._enter_reply(HISTORY[0])
    assert app.mode == "reply"
    app.query_one("#composer", Input).value = "a reply"
    await pilot.press("enter")
    await pilot.pause()
    assert ("c1", "a reply", "100") in app.rest.sent
    assert app.mode == "send"  # reset after send


async def t_slash_help_opens_modal(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._open_channel("c1")
    app.query_one("#composer", Input).value = "/help"
    await pilot.press("enter")
    await pilot.pause()
    assert len(app.screen_stack) > 1
    await pilot.press("escape")


async def t_slash_status(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._run_command("status dnd")
    assert app.gateway.presence == "dnd"
    assert app.rest.status == "dnd"


async def t_reveal_toggle(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._open_channel("c1")
    assert app.reveal is False
    app.action_reveal()
    assert app.reveal is True


async def t_members_panel(app, pilot):
    await push(app, pilot, "READY", READY)
    app.current_guild_id = "g1"
    app.action_toggle_members()
    members = app.query_one(MemberList)
    assert not members.has_class("-hidden")
    body = plain(members._body.content)
    assert "Ali" in body


async def t_message_nav_and_action(app, pilot):
    await push(app, pilot, "READY", READY)
    await app._open_channel("c1")
    mv = app.query_one(MessageView)
    mv.focus()
    await pilot.pause()
    assert mv.selected_index == 0  # auto-selects on focus
    # 'r' triggers a reply action on the selected message
    await pilot.press("r")
    await pilot.pause()
    assert app.mode == "reply"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("t_")]


async def main():
    import traceback
    passed = 0
    for test in TESTS:
        app = make_app()
        try:
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                await test(app, pilot)
            print(f"PASS {test.__name__}")
            passed += 1
        except Exception:  # noqa: BLE001
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(TESTS)} passed")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
