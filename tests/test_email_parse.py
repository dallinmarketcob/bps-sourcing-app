from leadsource.readers.email_parse import (
    extract_emails,
    extract_phones,
    parse_email,
)

PLAIN = """From: Lead Notifications <leads@pestnet.com>
Subject: New PestNet Lead - Pest Control
Date: Wed, 03 Jun 2026 14:22:10 -0700
Content-Type: text/plain; charset="utf-8"

You have a new lead:
Name: Jane Homeowner
Phone: (770) 555-1234
Email: jane.h@gmail.com
Pest: Ants
"""

HTML = """From: DoLead <noreply@dolead.com>
Subject: New Lead
Date: Thu, 04 Jun 2026 09:00:00 -0700
Content-Type: text/html; charset="utf-8"

<html><body><h2>New Lead</h2>
<p>Name: <b>Bob Smith</b></p>
<p>Phone: 951-356-6354</p>
<p>Email: bob.smith@yahoo.com</p>
</body></html>
"""


class TestParseEmail:
    def test_headers_and_domain(self):
        p = parse_email(PLAIN)
        assert p.from_domain == "pestnet.com"
        assert "PestNet" in p.subject
        assert p.date is not None and p.date.year == 2026

    def test_plain_body_text(self):
        p = parse_email(PLAIN)
        assert "Jane Homeowner" in p.text
        assert "770" in p.text

    def test_html_is_stripped_to_text(self):
        p = parse_email(HTML)
        assert "Bob Smith" in p.text
        assert "<b>" not in p.text and "<p>" not in p.text


class TestExtractPhones:
    def test_finds_and_normalizes(self):
        assert extract_phones("Call me at (770) 555-1234 today") == ["+17705551234"]

    def test_ignores_non_phone_digit_runs(self):
        # A zip code / id shouldn't become a phone.
        assert extract_phones("Zip 30301, ref 1002") == []

    def test_dedupes(self):
        text = "770-555-1234 or +1 770 555 1234"
        assert extract_phones(text) == ["+17705551234"]


class TestExtractEmails:
    def test_excludes_provider_domain(self):
        text = "From leads@pestnet.com — customer email jane.h@gmail.com"
        assert extract_emails(text, exclude_domains={"pestnet.com"}) == ["jane.h@gmail.com"]
