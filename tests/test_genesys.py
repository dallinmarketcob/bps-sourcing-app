from leadsource.models import Channel
from leadsource.readers.genesys import (
    conversation_to_touch,
    extract_ani_dnis,
    _strip_tel,
)
from leadsource.readers.source_maps import load_source_map_csv

SHEET = """Pestroutes Source,Provider / Channel,DNIS
Source 9,Aragon Spokane,628-229-5074
"""


def _conv(ani="tel:+17705551234", dnis="tel:+16282295074", start="2026-06-06T00:04:55.311Z"):
    # Mirrors the real Conversation Detail shape: customer + agent voice legs.
    return {
        "conversationId": "abc-123",
        "conversationStart": start,
        "participants": [
            {"purpose": "customer", "sessions": [
                {"mediaType": "voice", "direction": "inbound", "ani": ani, "dnis": dnis}]},
            {"purpose": "agent", "sessions": [
                {"mediaType": "voice", "direction": "inbound", "ani": ani, "dnis": dnis}]},
        ],
    }


def _source_map(tmp_path):
    p = tmp_path / "s.csv"
    p.write_text(SHEET, encoding="utf-8")
    return load_source_map_csv(p)


def test_strip_tel():
    assert _strip_tel("tel:+16282295074") == "+16282295074"
    assert _strip_tel("sip:+16282295074@host") == "+16282295074"
    assert _strip_tel(None) is None


def test_extract_ani_dnis_prefers_customer_leg():
    ani, dnis = extract_ani_dnis(_conv())
    assert ani == "+17705551234"
    assert dnis == "+16282295074"


def test_conversation_to_touch_maps_dnis_to_source(tmp_path):
    t = conversation_to_touch(_conv(), _source_map(tmp_path))
    assert t is not None
    assert t.channel is Channel.GENESYS
    assert t.source == "Source 9"          # DNIS 628-229-5074 -> Source 9
    assert t.phone_e164 == "+17705551234"  # caller ANI is the join key
    assert t.occurred_at.year == 2026


def test_unmapped_dnis_yields_no_touch(tmp_path):
    t = conversation_to_touch(_conv(dnis="tel:+16199999999"), _source_map(tmp_path))
    assert t is None


def test_missing_ani_yields_no_touch(tmp_path):
    t = conversation_to_touch(_conv(ani=None), _source_map(tmp_path))
    assert t is None
