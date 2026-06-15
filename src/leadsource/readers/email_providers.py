"""Identify the canonical source of a form-lead email.

As the user noted, emails don't follow one rigid template — the provider shows up
*somewhere*: the sender domain, the subject, or the body. So identification is a
prioritized match:

1. **Sender domain** (most reliable) -- e.g. ``@pestnet.com`` -> ``Source 23``.
2. **Explicit keyword** in subject/body -- e.g. "PestNet" -> ``Source 23``.
   Both come from a small, user-maintained alias table
   (``source_maps/email_providers.csv``: ``match_type,pattern,source``).
3. **Fallback:** the sheet's own provider names (col B) matched as whole words.

Anything unmatched returns ``None`` with a reason, to be logged for review.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .email_parse import ParsedEmail
from .source_maps import SourceMap, _norm_provider

# Provider names too generic to safely auto-match in free text via the fallback.
_AMBIGUOUS = {"organic", "test", "mmg", "outbound", "upsell"}


@dataclass
class ProviderRule:
    match_type: str  # "domain" | "keyword"
    pattern: str
    source: str      # canonical "Source N"


def load_email_providers(path: Path) -> list[ProviderRule]:
    """Load the provider alias table; returns [] if the file is absent."""
    path = Path(path)
    if not path.exists():
        return []
    rules: list[ProviderRule] = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            mt = (row.get("match_type") or "").strip().lower()
            pat = (row.get("pattern") or "").strip().lower()
            src = (row.get("source") or "").strip()
            if mt in ("domain", "keyword") and pat and src:
                rules.append(ProviderRule(mt, pat, src))
    return rules


def identify_source(
    parsed: ParsedEmail,
    rules: list[ProviderRule],
    source_map: SourceMap | None = None,
    use_name_fallback: bool = False,
) -> tuple[str | None, str]:
    """Return ``(canonical_source | None, reason)`` for an email.

    Only the curated alias table (domain + keyword) is trusted by default. The
    provider-name fallback is OFF by default because real spam ("not appearing on
    Google, Bing…") made it mis-attribute to 'Bing'. A wrong source is worse than
    an honest 'unidentified', so unmatched emails are flagged for review instead.
    """
    # 1) Sender domain.
    domain = parsed.from_domain
    for r in rules:
        if r.match_type == "domain" and domain and (
            domain == r.pattern or domain.endswith("." + r.pattern)
        ):
            return r.source, f"domain match '{r.pattern}' -> {r.source}"

    # 2) Explicit keyword anywhere in subject/from/body (incl. website domains).
    hay = parsed.haystack
    for r in rules:
        if r.match_type == "keyword" and r.pattern in hay:
            return r.source, f"keyword '{r.pattern}' -> {r.source}"

    # 3) Opt-in only: the sheet's provider names as whole words. Risky on free
    #    text (false positives), so disabled unless explicitly requested.
    if use_name_fallback and source_map is not None:
        for norm_name, source in source_map.provider_to_source.items():
            if norm_name in _AMBIGUOUS or len(norm_name) < 5:
                continue
            if re.search(rf"\b{re.escape(norm_name)}\b", hay):
                return source, f"provider-name match '{norm_name}' -> {source} (fallback)"

    return None, "no provider matched (domain/keyword) - needs review"
