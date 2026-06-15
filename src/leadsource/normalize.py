"""Normalization of the join keys (phone & email).

Every reader runs raw values through these so that a number written as
"(770) 555-1234", "770-555-1234", or "+17705551234" all collapse to one key.
Without this the cross-system join silently fails.
"""
from __future__ import annotations

from datetime import datetime, timezone

import phonenumbers

DEFAULT_REGION = "US"


def as_naive_utc(dt: datetime | None) -> datetime | None:
    """Coerce a datetime to naive UTC so timestamps from different sources
    (Genesys UTC, Gmail with offset, PestRoutes local) are comparable."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def normalize_phone(raw: str | None, region: str = DEFAULT_REGION) -> str | None:
    """Return an E.164 string (e.g. ``+17705551234``) or ``None`` if unparseable.

    Tolerant of formatting, punctuation, and a leading country code. Numbers
    that aren't valid phone numbers (too short, junk) return ``None`` rather
    than a garbage key.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        # If the value already carries a +country code, region is ignored.
        parsed = phonenumbers.parse(text, None if text.startswith("+") else region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(raw: str | None) -> str | None:
    """Return a lowercased, trimmed email, or ``None`` if empty/clearly invalid.

    Intentionally light: lowercase + strip + a single ``@`` sanity check. We are
    matching against values the same systems emitted, not validating deliverability.
    """
    if not raw:
        return None
    text = str(raw).strip().lower()
    if text.count("@") != 1:
        return None
    local, _, domain = text.partition("@")
    if not local or "." not in domain:
        return None
    return text
