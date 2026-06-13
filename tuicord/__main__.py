"""Entry point: resolve a token, then launch the TUI.

    python -m tuicord
"""

from __future__ import annotations

import sys

from . import __version__
from .app import TerminalDiscord
from .config import TOKEN_FILE, load_token, save_token

BANNER = r"""
  _____      _                  _
 |_   _|   _(_) ___ ___  _ __ __| |
   | || | | | |/ __/ _ \| '__/ _` |
   | || |_| | | (_| (_) | | | (_| |
   |_| \__,_|_|\___\___/|_|  \__,_|   Discord in your terminal
"""

WARNING = """\
⚠  This logs in with a USER token (self-bot), which violates Discord's Terms of
   Service and can get the account permanently banned. Use a throwaway/alt
   account — never your main. You accept this risk by continuing.
"""


def prompt_for_token() -> str | None:
    print(BANNER)
    print(f"  v{__version__}\n")
    print(WARNING)
    print("Paste your Discord user token (input hidden is not possible here),")
    print("or press Enter to abort.\n")
    try:
        token = input("token> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if not token:
        return None
    try:
        if input("Save token to disk for next time? [y/N] ").strip().lower() == "y":
            path = save_token(token)
            print(f"Saved to {path}")
    except (EOFError, KeyboardInterrupt):
        pass
    return token


def main() -> int:
    token = load_token()
    if not token:
        token = prompt_for_token()
    if not token:
        print("No token provided. Set DISCORD_TOKEN, write it to "
              f"{TOKEN_FILE}, or paste it when prompted.", file=sys.stderr)
        return 1
    TerminalDiscord(token).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
