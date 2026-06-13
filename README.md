# Tuicord

**Discord, in your terminal.** A full-featured TUI client built on the **real**
Discord API — the v10 REST endpoints for history and actions, and the live
Gateway WebSocket for real-time messages, typing, presence and reactions.

```
┌ SERVERS ───────────┬ # general   the main channel ──────────────┬ ONLINE — 3 ┐
│ ✉  Direct Messages │ 09:14  Alice   hey @you, build passed ✅    │ ● Alice    │
│ ❖  Cool Guild      │ 09:14  Bob     nice, shipping it            │ ◐ Bob      │
├ CHANNELS ──────────┤ ╭─ @Alice  build passed ✅                  │ ⊘ Carol    │
│ ▾ TEXT             │ 09:15  you     replying to that 🔥 3        │ OFFLINE—12 │
│  ● # general       │                                             │ ○ Dave     │
│   # random         │                                             │ ○ Erin     │
│   🔊 voice         ├─────────────────────────────────────────────┤            │
│ ▾ DEV              │   Bob is typing…                            │            │
│   # backend        │ Message # general    (/help for commands)   │            │
└────────────────────┴─────────────────────────────────────────────┴────────────┘
```

## ⚠️ Read this first — self-bot warning

Tuicord logs in with a **user token (self-bot)**. Automating a personal account
this way **violates Discord's Terms of Service**, and Discord actively detects
it — **your account can be permanently banned.**

- Use a **throwaway / alt account**, never your main.
- Your token grants *full access* to the account. Treat it like a password.
- You accept this risk by using the tool.

Want to stay within ToS? Tuicord is a couple of lines away from running as a
proper **bot** instead — see [Bot-token mode](#bot-token-mode).

## Features

**Messaging**
- Live message stream over the Gateway (send, receive, edit & delete in place)
- Full **Discord markdown**: bold / italic / underline / strikethrough,
  `inline code`, fenced code blocks with syntax highlighting, blockquotes,
  headers, lists, and **spoilers** (toggle reveal with `Ctrl+S`)
- **Mentions** resolved to names with role colors — `@user`, `@role`,
  `#channel`, `@everyone`/`@here`
- **Custom emoji** (`:name:`), **`<t:…>` timestamps** rendered to local time
- **Replies** (with quoted preview), **reactions** (live counts, add/remove)
- **Embeds** (title / description / fields / footer) and **attachments**
- Message **grouping** by author, **edited** / **pinned** badges, system messages
- **Infinite scroll** — page up at the top to load older history

**Navigation & servers**
- Server list + channel list **grouped by category**, with voice/stage/forum shown
- **Unread indicators** and automatic read-state (ack) on open
- **DMs and group DMs**, with presence dots
- **Member list** panel (`Ctrl+B`) grouped by online/offline with role colors,
  populated via lazy gateway subscription (op 14)
- **Typing indicators** ("X is typing…")
- **Presence** (online / idle / dnd / offline) across the member list and DMs

**Actions** (keyboard or slash commands)
- Reply, edit, delete, react, pin/unpin, open links, copy id, view profile
- Set your **status**, set a **nickname**, **upload files**, **search** messages
- View **pinned messages** and **active threads**, open a **DM** by name
- **Desktop notifications** (native macOS) + bell on mentions and DMs

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Get your token

In the Discord **web** app, open DevTools → Network, send a message, and copy
the `Authorization` request header from any `/api/v9/...` request. That string is
your user token.

## Run

```bash
export DISCORD_TOKEN="your.token.here"   # or just run and paste when prompted
python -m tuicord
```

The token may also live in `~/.config/tuicord/token` (the prompt offers to save
it there, `chmod 600`).

## Keys

| Key | Action |
|-----|--------|
| `Tab` | move focus between panes |
| ↑/↓ + `Enter` | pick a server, then a channel |
| type + `Enter` | send to the open channel |
| `Ctrl+G` | jump into the message list to navigate |
| `j`/`k` or ↑/↓ | move the message cursor (when message list focused) |
| `g` / `G` | jump to top / bottom |
| `r` `e` `d` `a` | reply / edit / delete / react to the selected message |
| `p` `o` `c` `u` | pin / open link / copy id / view profile |
| `Enter` / `i` / `Esc` | back to the message box |
| `Ctrl+B` | toggle the member list |
| `Ctrl+S` | reveal / hide spoilers |
| `Ctrl+R` | reload the current channel |
| `F1` | help |
| `Ctrl+Q` | quit |

## Slash commands

Type these in the message box:

```
/help                       /reveal     /reconnect    /quit
/reply <text>   /edit <text>   /delete   /react <emoji>   /pin   /unpin
/pins           /members       /threads  /search <query>
/dm <name|id>   /profile <name|id>       /goto <channel>
/status <online|idle|dnd|invisible>      /nick <name>     /upload <path>
/shrug   /tableflip   /unflip   /spoiler <text>
```

## How it works

```
tuicord/
  props.py     # browser "super properties" so the API trusts the client
  config.py    # token resolution (env → config file → .token) + saving
  api.py       # async REST v10: messages, reactions, pins, threads, members,
               #   upload, search, presence, DM, ack, edit/delete …
  gateway.py   # WebSocket Gateway: HELLO/heartbeat, IDENTIFY, RESUME, presence
               #   updates, lazy member subscription (op 14), event dispatch
  state.py     # central cache: guilds/channels/users/members/roles/emoji/
               #   presence/read-state — resolves mentions, colors, unread
  render.py    # Discord markdown + entities + embeds → Rich renderables
  widgets.py   # MessageView (keyboard nav, in-place updates), MemberList
  app.py       # the Textual app wiring REST + Gateway + widgets together
  __main__.py  # token onboarding + launch
```

- **REST** loads servers, channel history, and performs actions you take.
- The **Gateway** keeps a WebSocket open, heartbeats, resumes after drops, and
  streams events. `app.py` applies them: new/edited/deleted messages, reactions,
  typing, presence, channel/guild updates, and member-list chunks.
- **`state.py`** is the single source of truth the renderer reads, so a `<@123>`
  becomes a colored `@nickname` and unread channels are marked.

## Tests

No account needed — the renderer is unit-tested and the UI is driven headlessly
with a mock REST client and simulated gateway events.

```bash
python tests/test_render.py   # markdown / entity rendering
python tests/test_app.py      # boot, channels, live events, modes, commands
```

## Bot-token mode

To run legitimately as a Discord **bot** instead of a self-bot:

1. In `api.py`, set the `Authorization` header to `f"Bot {self.token}"`.
2. In `gateway.py` `_send_identify`, send a bot identify instead:
   `{"token": ..., "intents": 33281, "properties": {"os": "linux",
   "browser": "tuicord", "device": "tuicord"}}`.
3. Use a bot token from the Discord Developer Portal, invite the bot to a server,
   and enable the **Message Content** privileged intent.

(Member-list op 14 and a few user-only endpoints — search, profile, ack — don't
apply to bots, but messaging, reactions, threads and presence all do.)

## Limitations

Not implemented: **voice/video** (needs the voice gateway + UDP + Opus — a large
separate effort), real application/slash-command *invocation*, inline image
rendering, and emoji-image display (custom emoji show as `:name:`). The
architecture leaves room for each — events land in `app.py`'s gateway handler and
new REST calls go in `api.py`.

---

Built for the terminal with [Textual](https://textual.textualize.io/) +
[aiohttp](https://docs.aiohttp.org/). Not affiliated with Discord.
