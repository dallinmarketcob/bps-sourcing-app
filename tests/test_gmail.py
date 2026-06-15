from leadsource.models import Channel
from leadsource.readers.email_providers import ProviderRule, identify_source
from leadsource.readers.email_parse import parse_email
from leadsource.readers.gmail import build_touch_from_email
from leadsource.readers.source_maps import load_source_map_csv

PESTNET = """From: Lead Notifications <leads@pestnet.com>
Subject: New PestNet Lead
Date: Wed, 03 Jun 2026 14:22:10 -0700
Content-Type: text/plain; charset="utf-8"

Name: Jane Homeowner
Phone: (770) 555-1234
Email: jane.h@gmail.com
"""

GENERIC = """From: Web Forms <forms@somehost.com>
Subject: New website inquiry
Date: Wed, 03 Jun 2026 14:22:10 -0700
Content-Type: text/plain; charset="utf-8"

Name: Pat Lee
Phone: 833-999-3128
"""

RULES = [
    ProviderRule("domain", "pestnet.com", "Source 23"),
    ProviderRule("keyword", "dolead", "Source 11"),
]

SHEET = """Pestroutes Source,Provider / Channel,DNIS
Source 23,Pestnet,833-999-3128
Source 1,Aragon Fresno,628-232-0597
"""


def _source_map(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(SHEET, encoding="utf-8")
    return load_source_map_csv(p)


class TestIdentifySource:
    def test_domain_match_wins(self):
        src, reason = identify_source(parse_email(PESTNET), RULES)
        assert src == "Source 23"
        assert "domain" in reason

    def test_keyword_match(self):
        msg = PESTNET.replace("pestnet.com", "x.com").replace("PestNet", "DoLead")
        src, _ = identify_source(parse_email(msg), RULES)
        assert src == "Source 11"

    def test_name_fallback_is_opt_in(self, tmp_path):
        # The provider-name fallback is OFF by default (prevents spam false-
        # positives like matching 'Bing' in body text), ON only when requested.
        msg = GENERIC.replace("New website inquiry", "Lead from Pestnet")
        smap = _source_map(tmp_path)
        # Off by default -> unidentified.
        src, _ = identify_source(parse_email(msg), [], smap)
        assert src is None
        # Opt-in -> matches.
        src, reason = identify_source(parse_email(msg), [], smap, use_name_fallback=True)
        assert src == "Source 23"
        assert "fallback" in reason

    def test_unmatched_returns_none(self):
        src, reason = identify_source(parse_email(GENERIC), RULES)
        assert src is None
        assert "review" in reason


class TestBuildTouch:
    def test_full_touch(self):
        res = build_touch_from_email(PESTNET, RULES)
        assert res.ok
        t = res.touch
        assert t.channel is Channel.GMAIL
        assert t.source == "Source 23"
        assert t.phone_e164 == "+17705551234"
        assert t.email == "jane.h@gmail.com"
        assert t.occurred_at.year == 2026

    def test_unidentified_source_no_touch(self):
        res = build_touch_from_email(GENERIC, RULES)
        assert not res.ok
        assert res.problem == "unidentified source"

    def test_identified_but_no_contact(self):
        msg = """From: leads@pestnet.com
Subject: New PestNet Lead
Date: Wed, 03 Jun 2026 14:22:10 -0700

(form submission with no contact info)
"""
        res = build_touch_from_email(msg, RULES)
        assert not res.ok
        assert res.problem == "no phone or email found"
