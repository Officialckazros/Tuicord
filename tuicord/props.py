"""Client identity / "super properties".

Discord's user (non-bot) API expects requests to look like they come from the
official web client. These properties are sent in the gateway IDENTIFY payload
and, base64-encoded, in the ``X-Super-Properties`` REST header. They don't need
to be perfectly accurate, but they should be self-consistent and plausible.
"""

from __future__ import annotations

import base64
import json

# A recent-ish stable web build. Discord bumps this constantly; an older number
# still connects fine for reading/sending messages.
CLIENT_BUILD_NUMBER = 9999

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SUPER_PROPERTIES: dict = {
    "os": "Mac OS X",
    "browser": "Chrome",
    "device": "",
    "system_locale": "en-US",
    "browser_user_agent": BROWSER_USER_AGENT,
    "browser_version": "124.0.0.0",
    "os_version": "10.15.7",
    "referrer": "",
    "referring_domain": "",
    "referrer_current": "",
    "referring_domain_current": "",
    "release_channel": "stable",
    "client_build_number": CLIENT_BUILD_NUMBER,
    "client_event_source": None,
}


def super_properties_b64() -> str:
    """Return SUPER_PROPERTIES as a base64 string for the REST header."""
    raw = json.dumps(SUPER_PROPERTIES, separators=(",", ":")).encode()
    return base64.b64encode(raw).decode()
