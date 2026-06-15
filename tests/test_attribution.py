from datetime import datetime, timedelta

from leadsource.attribution import attribute_subscription, TouchIndex
from leadsource.models import (
    AttributionStatus,
    Channel,
    MatchKey,
    Subscription,
    Touch,
)

PHONE = "+17705551234"
PHONE2 = "+17705559999"
EMAIL = "lead@example.com"
DAY0 = datetime(2026, 5, 1, 9, 0)


def touch(source, when, *, channel=Channel.GMAIL, phone=PHONE, email=None):
    return Touch(channel=channel, source=source, occurred_at=when, phone_e164=phone, email=email)


def sub(sold_at, *, phone1=PHONE, phone2=None, email=None, current_source=None):
    return Subscription(
        subscription_id="S1",
        customer_id="C1",
        sold_at=sold_at,
        phone1_e164=phone1,
        phone2_e164=phone2,
        email=email,
        current_source=current_source,
    )


def attribute(s, touches, stale=30, cluster=24):
    return attribute_subscription(
        s, TouchIndex(touches), stale_window_days=stale, same_day_cluster_hours=cluster
    )


class TestEarliestWinsInArrival:
    def test_three_providers_same_day_earliest_wins(self):
        # Worked example 1: 3 providers in one arrival -> earliest wins.
        touches = [
            touch("ProviderB", DAY0 + timedelta(hours=2)),
            touch("ProviderA", DAY0),  # earliest
            touch("ProviderC", DAY0 + timedelta(hours=4)),
        ]
        res = attribute(sub(DAY0 + timedelta(hours=6)), touches, stale=14)
        assert res.status is AttributionStatus.ATTRIBUTED
        assert res.attributed_source == "ProviderA"
        assert res.matched_key is MatchKey.PHONE1

    def test_touches_within_reset_gap_are_one_arrival(self):
        # 10 days apart with a 14d reset gap = same arrival -> earliest keeps it.
        touches = [touch("ProviderA", DAY0), touch("ProviderB", DAY0 + timedelta(days=10))]
        res = attribute(sub(DAY0 + timedelta(days=11)), touches, stale=14)
        assert res.attributed_source == "ProviderA"


class TestRevivalAndAging:
    def test_new_lead_after_reset_gap_supersedes(self):
        # B arrives 15 days after A (>= 14d reset) -> new arrival, B wins.
        sold = DAY0 + timedelta(days=16)
        touches = [touch("ProviderA", DAY0), touch("ProviderB", DAY0 + timedelta(days=15))]
        res = attribute(sub(sold), touches, stale=14)
        assert res.attributed_source == "ProviderB"

    def test_month_later_reviver_wins(self):
        # Worked example 2: A's form goes cold, B revives a month later and closes.
        sold = DAY0 + timedelta(days=31)
        touches = [
            touch("ProviderA", DAY0, channel=Channel.GMAIL),
            touch("ProviderB", sold, channel=Channel.GENESYS),
        ]
        res = attribute(sub(sold), touches, stale=14)
        assert res.attributed_source == "ProviderB"

    def test_old_lead_kept_when_no_new_arrival(self):
        # THE KEY RULE: a 40-day-old lead with no newer touch is STILL credited
        # (no age cutoff) -- the deal closed late but the original lead earned it.
        sold = DAY0 + timedelta(days=40)
        touches = [touch("ProviderA", DAY0)]
        res = attribute(sub(sold), touches, stale=14)
        assert res.attributed_source == "ProviderA"


class TestCombinedContact:
    def test_lead_on_phone2_counts(self):
        # Landlord/tenant: primary number never appears as a lead; tenant's does.
        touches = [touch("ProviderA", DAY0, phone=PHONE2)]
        res = attribute(sub(DAY0 + timedelta(hours=1), phone1=PHONE, phone2=PHONE2), touches)
        assert res.attributed_source == "ProviderA"
        assert res.matched_key is MatchKey.PHONE2

    def test_lead_on_email_counts(self):
        touches = [touch("ProviderA", DAY0, phone=None, email=EMAIL)]
        res = attribute(
            sub(DAY0 + timedelta(hours=1), phone1=PHONE, phone2=PHONE2, email=EMAIL), touches
        )
        assert res.attributed_source == "ProviderA"
        assert res.matched_key is MatchKey.EMAIL

    def test_earliest_across_both_numbers_wins(self):
        # Scenario 5: form on phone1 (day0), call on phone2 (day5) -- within the
        # 7-day window they're one arrival, so the FIRST touch (form) wins.
        touches = [
            touch("Form", DAY0, phone=PHONE),
            touch("Call", DAY0 + timedelta(days=5), phone=PHONE2),
        ]
        res = attribute(
            sub(DAY0 + timedelta(days=6), phone1=PHONE, phone2=PHONE2), touches, stale=7
        )
        assert res.attributed_source == "Form"
        assert res.matched_key is MatchKey.PHONE1

    def test_phone_match_preferred_over_earlier_email_only(self):
        # An email-only touch is EARLIER, but a phone touch exists -> phone wins
        # (email is the weaker key; only a fallback).
        touches = [
            touch("EmailSource", DAY0, phone=None, email=EMAIL),
            touch("PhoneSource", DAY0 + timedelta(hours=1), phone=PHONE),
        ]
        res = attribute(
            sub(DAY0 + timedelta(hours=2), phone1=PHONE, email=EMAIL), touches, stale=7
        )
        assert res.attributed_source == "PhoneSource"
        assert res.matched_key is MatchKey.PHONE1

    def test_email_only_counts_when_no_phone_touch(self):
        # No phone touch at all -> the email-only lead is used.
        touches = [touch("EmailSource", DAY0, phone=None, email=EMAIL)]
        res = attribute(
            sub(DAY0 + timedelta(hours=2), phone1=PHONE, email=EMAIL), touches, stale=7
        )
        assert res.attributed_source == "EmailSource"
        assert res.matched_key is MatchKey.EMAIL

    def test_earliest_wins_even_when_it_is_on_phone2(self):
        # First touch is the call on phone2 -> it wins (first-touch, any number).
        touches = [
            touch("Call", DAY0, phone=PHONE2),
            touch("Form", DAY0 + timedelta(days=2), phone=PHONE),
        ]
        res = attribute(
            sub(DAY0 + timedelta(days=3), phone1=PHONE, phone2=PHONE2), touches, stale=7
        )
        assert res.attributed_source == "Call"
        assert res.matched_key is MatchKey.PHONE2


class TestMetaFormTiebreak:
    def test_meta_beats_near_simultaneous_website_form(self):
        # Website form looks earlier (its email lags), but a Meta lead within 2
        # min drove them to the site -> Meta wins.
        touches = [
            touch("Source 55", DAY0, channel=Channel.GMAIL),
            touch("Source 144", DAY0 + timedelta(seconds=40), channel=Channel.META),
        ]
        res = attribute_subscription(sub(DAY0 + timedelta(hours=1)), TouchIndex(touches),
                                     stale_window_days=7)
        assert res.attributed_source == "Source 144"

    def test_form_keeps_credit_if_meta_is_far_off(self):
        # Meta is 10 minutes later -> not a tie -> the earlier form keeps it.
        touches = [
            touch("Source 55", DAY0, channel=Channel.GMAIL),
            touch("Source 144", DAY0 + timedelta(minutes=10), channel=Channel.META),
        ]
        res = attribute_subscription(sub(DAY0 + timedelta(hours=1)), TouchIndex(touches),
                                     stale_window_days=7)
        assert res.attributed_source == "Source 55"


class TestEdgeCases:
    def test_no_touches_is_flagged_and_not_written(self):
        res = attribute(sub(DAY0), touches=[])
        assert res.status is AttributionStatus.NO_TOUCHES
        assert res.needs_write is False
        assert res.is_unsourced is True
        assert res.attributed_source is None

    def test_touches_after_sale_are_ignored(self):
        touches = [touch("AfterTheSale", DAY0 + timedelta(days=5))]
        res = attribute(sub(DAY0), touches)
        assert res.status is AttributionStatus.NO_TOUCHES

    def test_unchanged_source_needs_no_write(self):
        touches = [touch("ProviderA", DAY0)]
        res = attribute(sub(DAY0 + timedelta(hours=1), current_source="ProviderA"), touches)
        assert res.status is AttributionStatus.ATTRIBUTED
        assert res.is_change is False
        assert res.needs_write is False

    def test_changed_source_needs_write(self):
        touches = [touch("ProviderA", DAY0)]
        res = attribute(sub(DAY0 + timedelta(hours=1), current_source="OldSource"), touches)
        assert res.needs_write is True


class TestProtectedSources:
    def test_protected_source_is_never_overwritten(self):
        # Door to Door sale + an inbound touch -> keep D2D, do not write.
        touches = [touch("Source 123", DAY0)]
        res = attribute_subscription(
            sub(DAY0 + timedelta(hours=1), current_source="Door to Door"),
            TouchIndex(touches),
            protected_sources=frozenset({"door to door"}),
        )
        assert res.protected is True
        assert res.needs_write is False
        assert res.attributed_source == "Source 123"  # still computed for reporting

    def test_unprotected_source_still_writes(self):
        touches = [touch("Source 123", DAY0)]
        res = attribute_subscription(
            sub(DAY0 + timedelta(hours=1), current_source="Source 99"),
            TouchIndex(touches),
            protected_sources=frozenset({"door to door"}),
        )
        assert res.protected is False
        assert res.needs_write is True
