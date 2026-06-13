"""Token loading and persistence."""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("TUICORD_CONFIG_DIR", Path.home() / ".config" / "tuicord"))
TOKEN_FILE = CONFIG_DIR / "token"


def load_token() -> str | None:
    """Resolve a user token from, in order: env var, config file, local .token."""
    env = os.environ.get("DISCORD_TOKEN")
    if env:
        return env.strip()

    for path in (TOKEN_FILE, Path(".token")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            continue
        if text:
            return text
    return None


def save_token(token: str) -> Path:
    """Persist the token to the config dir (chmod 600)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip(), encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return TOKEN_FILE
