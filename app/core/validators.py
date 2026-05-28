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
