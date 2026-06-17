"""Small, dependency-free validators shared across the app.

Kept pure (no I/O, no models) so they're trivially unit-testable and safe to
import from anywhere — including hot auth paths.
"""

from __future__ import annotations

import re

# Pragmatic email shape check — NOT full RFC 5322. The goal is to reject
# obviously-broken values (empty, missing `@`/domain) and, critically,
# UNRENDERED Clerk JWT/template shortcodes like
# `{{user.primary_email_address.email_address}}` that a misconfigured token
# template can emit as a literal string. Storing such a literal poisons the
# UNIQUE(users.email) constraint and breaks every subsequent signup.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_plausible_email(value: str | None) -> bool:
    """True if `value` looks like a real email address.

    Rejects: None/blank, anything containing template braces (`{{` / `}}`),
    and anything that fails a basic `local@domain.tld` shape check.
    """
    if not value:
        return False
    candidate = value.strip()
    if "{{" in candidate or "}}" in candidate:
        return False
    return bool(_EMAIL_RE.match(candidate))


# A YouTube video id is exactly 11 chars from the URL-safe base64 alphabet.
# We extract the id from whatever URL form the admin pastes and store ONLY the
# id — never the raw URL — so the storefront builds the embed deterministically
# and an attacker can't smuggle a different host/path into the iframe `src`.
_YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_URL_PATTERNS = (
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:[^&]*&)*v=([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/shorts/([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/embed/([A-Za-z0-9_-]{11})"),
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/live/([A-Za-z0-9_-]{11})"),
)


def extract_youtube_id(value: str | None) -> str | None:
    """Return the canonical 11-char YouTube id from a URL or bare id, else None.

    Accepts the common forms an admin might paste:
      - https://www.youtube.com/watch?v=<id>  (with extra query params)
      - https://youtu.be/<id>
      - https://www.youtube.com/shorts/<id>
      - https://www.youtube.com/embed/<id>  /  /live/<id>
      - a bare 11-char id

    Anything else (blank, foreign host, malformed) returns None so the caller
    can reject it with a clear validation error.
    """
    if not value:
        return None
    candidate = value.strip()
    if _YOUTUBE_ID_RE.match(candidate):
        return candidate
    for pattern in _YOUTUBE_URL_PATTERNS:
        match = pattern.search(candidate)
        if match:
            return match.group(1)
    return None
