"""The attribution brain — pure logic, no I/O.

For each sold subscription we decide which lead source earned the credit:

1. **One contact.** Combine the customer's touches across ``phone1`` + ``phone2``
   (the reliable identifiers). Fall back to ``email``-matched touches ONLY when
   there's no phone touch at all — email is a weaker key, so a phone match always
   takes priority.
2. **Arrivals (reset gap).** Order the matched touches in time and split into
   "arrivals": a new arrival starts when a touch lands ``stale_window_days`` or
   more after the previous one. A lead never expires on its own — it only loses
   credit when a *newer* arrival supersedes it (no age cutoff).
3. **Winner = the earliest touch of the most-recent arrival.** Several sources
   arriving together → the first one there gets it. A fresh lead after a quiet
   gap → that source revived the deal and takes the credit. An old lead with no
   newer touch keeps its credit no matter how long ago it came in.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import timedelta

from .models import (
    AttributionResult,
    AttributionStatus,
    MatchKey,
    Subscription,
    Touch,
)


class TouchIndex:
    """In-memory lookup of touches by normalized phone and email.

    Built once per run from all observed touches; the brain queries it per
    subscription. Keeping it separate keeps the brain pure and testable.
    """

    def __init__(self, touches: Iterable[Touch]) -> None:
        self._by_phone: dict[str, list[Touch]] = defaultdict(list)
        self._by_email: dict[str, list[Touch]] = defaultdict(list)
        for t in touches:
            if t.phone_e164:
                self._by_phone[t.phone_e164].append(t)
            if t.email:
                self._by_email[t.email].append(t)

    def for_phone(self, phone_e164: str | None) -> list[Touch]:
        return list(self._by_phone.get(phone_e164, ())) if phone_e164 else []

    def for_email(self, email: str | None) -> list[Touch]:
        return list(self._by_email.get(email, ())) if email else []


def _split_into_arrivals(touches: list[Touch], reset_gap_days: float) -> list[list[Touch]]:
    """Split time-ordered touches into 'arrivals'. A new arrival starts when a
    touch lands >= ``reset_gap_days`` after the previous one (a fresh lead after
    the prior one went quiet). Touches closer than that are the same arrival.

    There is NO age cutoff: a lead stays eligible no matter how old — it only
    loses credit when a *newer* arrival supersedes it.
    """
    arrivals: list[list[Touch]] = []
    current: list[Touch] = []
    prev: Touch | None = None
    for t in touches:
        gap_days = (t.occurred_at - prev.occurred_at).total_seconds() / 86400.0 if prev else 0.0
        if prev is not None and gap_days >= reset_gap_days:
            arrivals.append(current)
            current = []
        current.append(t)
        prev = t
    if current:
        arrivals.append(current)
    return arrivals


def attribute_subscription(
    sub: Subscription,
    index: TouchIndex,
    stale_window_days: int = 30,
    same_day_cluster_hours: float = 24,
    protected_sources: frozenset[str] = frozenset(),
    meta_source: str = "Source 144",
    website_form_source: str = "Source 55",
    meta_form_tiebreak_minutes: float = 2,
) -> AttributionResult:
    """Run the credit rule for one subscription.

    ``stale_window_days`` is the **reset gap**: a new lead arriving this many days
    after the previous touch starts a fresh arrival and takes over the credit.
    There is NO age cutoff — a lead stays credited no matter how old (30/60/90+
    days) until a newer arrival supersedes it.

    ``protected_sources`` (lowercased source names) are internal/process sources
    — Door to Door, Additional Property, renewals, referrals — that must never be
    overwritten even when a touch is found.

    (``same_day_cluster_hours`` is accepted for forward-compatibility but, under
    the "earliest of the most-recent arrival" rule, doesn't change the outcome:
    the reset gap already separates a same arrival from a revival.)
    """
    # 1. Phone is the reliable identifier: combine phone1 + phone2 touches. Only
    #    fall back to email-matched touches if there's NO phone touch at all.
    #    (All touches at/before the sale are eligible; no age cutoff.)
    def _collect(lookups: list[list[Touch]]) -> list[Touch]:
        seen: set = set()
        out: list[Touch] = []
        for lst in lookups:
            for t in lst:
                if t.occurred_at > sub.sold_at:
                    continue
                ident = (
                    (t.channel, t.raw_ref) if t.raw_ref
                    else (t.channel, t.occurred_at, t.source, t.phone_e164, t.email)
                )
                if ident not in seen:
                    seen.add(ident)
                    out.append(t)
        return out

    touches = _collect([index.for_phone(sub.phone1_e164), index.for_phone(sub.phone2_e164)])
    if not touches:
        touches = _collect([index.for_email(sub.email)])

    if not touches:
        return AttributionResult(
            subscription_id=sub.subscription_id,
            status=AttributionStatus.NO_TOUCHES,
            matched_key=MatchKey.NONE,
            reason="No lead touches found on phone1, phone2, or email at/before sale.",
        )

    # 2. Split into arrivals; the most-recent arrival holds the credit. An old
    #    lead keeps credit until a new arrival (>= reset gap later) supersedes it.
    touches.sort(key=lambda t: t.occurred_at)
    arrivals = _split_into_arrivals(touches, stale_window_days)
    winning_arrival = arrivals[-1]

    # 3. Earliest (first) touch of that arrival wins.
    last_streak = winning_arrival
    winner = winning_arrival[0]

    # Tie-break: a Meta ad that drove them to the site beats a near-simultaneous
    # website form, even if the form's (lagging) email timestamp looks earlier.
    if winner.source == website_form_source:
        cutoff = winner.occurred_at + timedelta(minutes=meta_form_tiebreak_minutes)
        metas = [t for t in winning_arrival if t.source == meta_source and t.occurred_at <= cutoff]
        if metas:
            winner = metas[0]

    # Which key did the winning touch come in on (for the audit)?
    matched_key = MatchKey.NONE
    if winner.phone_e164 and winner.phone_e164 == sub.phone1_e164:
        matched_key = MatchKey.PHONE1
    elif winner.phone_e164 and winner.phone_e164 == sub.phone2_e164:
        matched_key = MatchKey.PHONE2
    elif winner.email and winner.email == sub.email:
        matched_key = MatchKey.EMAIL

    is_change = (sub.current_source or None) != winner.source
    protected = bool(
        sub.current_source and sub.current_source.strip().lower() in protected_sources
    )
    if protected:
        reason = (
            f"Protected source '{sub.current_source}' kept; would have been "
            f"{winner.source} ({winner.channel.value}) - not overwritten."
        )
    else:
        reason = (
            f"Matched by {matched_key.value}; {len(arrivals)} arrival(s), "
            f"credited earliest touch in the most-recent arrival "
            f"({winner.channel.value}/{winner.source} @ {winner.occurred_at:%Y-%m-%d})."
        )
    return AttributionResult(
        subscription_id=sub.subscription_id,
        status=AttributionStatus.ATTRIBUTED,
        attributed_source=winner.source,
        matched_key=matched_key,
        winning_touch=winner,
        streak=last_streak,
        is_change=is_change,
        protected=protected,
        reason=reason,
    )


def attribute_all(
    subs: Iterable[Subscription],
    touches: Iterable[Touch],
    stale_window_days: int = 30,
    same_day_cluster_hours: float = 24,
    protected_sources: frozenset[str] = frozenset(),
    **kwargs,
) -> list[AttributionResult]:
    """Convenience: build the index once and attribute every subscription.
    Extra keyword args (meta_source, website_form_source,
    meta_form_tiebreak_minutes) pass straight through to each call."""
    index = TouchIndex(touches)
    return [
        attribute_subscription(
            s, index, stale_window_days, same_day_cluster_hours, protected_sources, **kwargs
        )
        for s in subs
    ]
