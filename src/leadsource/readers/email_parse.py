"""Format-agnostic email parsing + field extraction.

Decoupled from how the email was obtained (live Gmail API or a saved ``.eml``)
so the tricky parsing logic can be unit-tested on real samples. Given raw RFC822
bytes/text it returns a ``ParsedEmail`` (from/subject/date/plain-text body) and
offers robust phone/email extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from email import message_from_bytes, message_from_string
from email.message import EmailMessage
from email.policy import default as default_policy
from email.utils import parsedate_to_datetime

import phonenumbers

from .. import normalize as _norm

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n\s*\n+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


@dataclass
class ParsedEmail:
    from_addr: str = ""
    from_domain: str = ""
    subject: str = ""
    date: datetime | None = None
    text: str = ""          # best-effort plain text (html stripped if needed)
    raw_ref: str | None = None  # message id / filename, for audit

    @property
    def haystack(self) -> str:
        """All the text where a provider name might appear, lowercased."""
        return f"{self.subject}\n{self.from_addr}\n{self.text}".lower()


def _html_to_text(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    text = _WS_RE.sub(" ", text)
    return _BLANKLINES_RE.sub("\n\n", text).strip()


def _extract_body(msg: EmailMessage) -> str:
    """Prefer a text/plain part; fall back to stripped text/html."""
    try:
        plain = msg.get_body(preferencelist=("plain",))
        if plain is not None:
            return plain.get_content().strip()
    except Exception:
        pass
    try:
        html = msg.get_body(preferencelist=("html",))
        if html is not None:
            return _html_to_text(html.get_content())
    except Exception:
        pass
    # Non-multipart fallback.
    try:
        content = msg.get_content()
        if isinstance(content, str):
            return _html_to_text(content) if "<" in content and ">" in content else content.strip()
    except Exception:
        pass
    return ""


def parse_email(raw: bytes | str, raw_ref: str | None = None) -> ParsedEmail:
    """Parse raw RFC822 content into a ParsedEmail."""
    if isinstance(raw, bytes):
        msg = message_from_bytes(raw, policy=default_policy)
    else:
        msg = message_from_string(raw, policy=default_policy)

    from_addr = str(msg.get("From", "")).strip()
    from_domain = ""
    m = _EMAIL_RE.search(from_addr)
    if m:
        from_domain = m.group(0).split("@", 1)[1].lower()

    date: datetime | None = None
    raw_date = msg.get("Date")
    if raw_date:
        try:
            date = _norm.as_naive_utc(parsedate_to_datetime(raw_date))
        except (TypeError, ValueError):
            date = None

    return ParsedEmail(
        from_addr=from_addr,
        from_domain=from_domain,
        subject=str(msg.get("Subject", "")).strip(),
        date=date,
        text=_extract_body(msg),
        raw_ref=raw_ref,
    )


def extract_phones(text: str, region: str = "US") -> list[str]:
    """Return distinct valid phone numbers found in free text, as E.164.

    Uses phonenumbers' matcher, which tolerates the many ways a number can be
    written in an email body and ignores non-phone digit runs (zips, ids).
    """
    seen: list[str] = []
    for match in phonenumbers.PhoneNumberMatcher(text or "", region):
        if phonenumbers.is_valid_number(match.number):
            e164 = phonenumbers.format_number(
                match.number, phonenumbers.PhoneNumberFormat.E164
            )
            if e164 not in seen:
                seen.append(e164)
    return seen


def extract_emails(text: str, exclude_domains: set[str] | None = None) -> list[str]:
    """Return distinct email addresses found in text, lowercased.

    ``exclude_domains`` drops sender/provider addresses (e.g. the form provider's
    own domain) so we keep the *lead's* email, not the notification's From.
    """
    exclude = {d.lower() for d in (exclude_domains or set())}
    out: list[str] = []
    for raw in _EMAIL_RE.findall(text or ""):
        addr = raw.lower()
        domain = addr.split("@", 1)[1]
        if domain in exclude:
            continue
        if addr not in out:
            out.append(addr)
    return out
