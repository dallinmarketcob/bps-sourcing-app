from leadsource.models import Channel
from leadsource.readers.meta import _field, lead_to_touch

LEAD = {
    "id": "lead_1",
    "created_time": "2026-06-07T18:30:00+0000",
    "campaign_name": "Pest - SoCal - Lead Gen",
    "field_data": [
        {"name": "full_name", "values": ["Jane Homeowner"]},
        {"name": "phone_number", "values": ["(770) 555-1234"]},
        {"name": "email", "values": ["Jane.H@gmail.com"]},
    ],
}


def test_field_lookup_is_case_insensitive():
    assert _field(LEAD, "phone_number") == "(770) 555-1234"
    assert _field(LEAD, "EMAIL") == "Jane.H@gmail.com"
    assert _field(LEAD, "missing") is None


def test_lead_to_touch_normalizes():
    t = lead_to_touch(LEAD, source="Source 51")
    assert t.channel is Channel.META
    assert t.source == "Source 51"
    assert t.phone_e164 == "+17705551234"
    assert t.email == "jane.h@gmail.com"
    assert t.occurred_at.year == 2026


def test_lead_with_no_contact_is_dropped():
    bare = {"id": "x", "field_data": [{"name": "full_name", "values": ["No Contact"]}]}
    assert lead_to_touch(bare, source="Source 51") is None
