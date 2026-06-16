from leadsource.config import Settings
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


# --- multi-page config resolution -------------------------------------------

def _settings(**kw):
    # _env_file=None isolates from any on-disk .env so tests are deterministic.
    return Settings(_env_file=None, **kw)


def test_meta_page_configs_single_fallback():
    s = _settings(meta_page_id="999", meta_access_token="T", meta_lead_source="Source 5")
    assert s.meta_page_configs == [
        {"page_id": "999", "token": "T", "source": "Source 5"}
    ]


def test_meta_page_configs_multi_with_per_page_token_and_source():
    s = _settings(
        meta_pages="579676868552462|tokA|Source 10, 894672603731889",
        meta_access_token="USER", meta_lead_source="Source 99",
    )
    assert s.meta_page_configs == [
        {"page_id": "579676868552462", "token": "tokA", "source": "Source 10"},
        # token+source omitted -> fall back to META_ACCESS_TOKEN / META_LEAD_SOURCE
        {"page_id": "894672603731889", "token": "USER", "source": "Source 99"},
    ]


def test_meta_page_configs_shared_user_token():
    # Two pages, no per-page tokens -> both use the shared user token.
    s = _settings(
        meta_pages="111|\n222|", meta_access_token="USER", meta_lead_source="Source 7",
    )
    assert [c["token"] for c in s.meta_page_configs] == ["USER", "USER"]
    assert [c["page_id"] for c in s.meta_page_configs] == ["111", "222"]


def test_meta_page_configs_empty_when_nothing_set():
    assert _settings(meta_page_id="", meta_access_token="").meta_page_configs == []
