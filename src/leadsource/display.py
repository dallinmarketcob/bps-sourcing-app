"""Human-friendly source labels for reports.

Source 144 is overloaded: it's both "Facebook Inbound" calls (Genesys, to the FB
tracking numbers) AND Meta instant-form leads. PestRoutes stores both as
sourceID 144, but in REPORTS we split them by channel so a form isn't mistaken
for a phone call.
"""
from __future__ import annotations


def friendly_source(
    source: str | None,
    channel: str | None = None,
    src2prov: dict[str, str] | None = None,
    meta_source: str = "Source 144",
) -> str:
    """Display name for a source, channel-aware for the overloaded Meta/FB source."""
    if not source:
        return ""
    if source == meta_source:
        if channel == "genesys":
            return "Facebook Inbound Call (S144)"
        return "Meta Form Lead (S144)"
    prov = (src2prov or {}).get(source)
    return f"{prov} ({source})" if prov else source


def decision_label(result, current_source: str | None) -> str:
    """Bucket an attribution result for reports: what the engine would do."""
    blank = not current_source
    if result.is_unsourced:  # no inbound touch found
        return "UNSOURCED (no source on file)" if blank else "NO EVIDENCE (kept)"
    if result.protected:
        return "PROTECTED (kept)"
    if blank:
        return "FILL (was blank)"
    return "DISAGREE (change)" if result.is_change else "AGREE"
