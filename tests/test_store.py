from datetime import datetime

from leadsource import store
from leadsource.models import Channel, Touch


def _touch(ref, when, source="Source 1"):
    return Touch(channel=Channel.GENESYS, source=source, occurred_at=when,
                 phone_e164="+17705551234", raw_ref=ref)


def test_ingest_dedup_and_load(tmp_path):
    conn = store.connect(tmp_path / "t.sqlite")
    t1 = _touch("conv-1", datetime(2026, 6, 1, 10, 0))
    t2 = _touch("conv-2", datetime(2026, 6, 5, 10, 0))

    added = store.ingest_touches(conn, [t1, t2], now_iso="2026-06-08T00:00:00")
    assert added == 2

    # Re-ingesting the same touches adds nothing (deduped by channel:raw_ref).
    again = store.ingest_touches(conn, [t1, t2], now_iso="2026-06-08T00:00:00")
    assert again == 0

    all_touches = store.load_touches(conn)
    assert len(all_touches) == 2

    # since-filter works.
    recent = store.load_touches(conn, since=datetime(2026, 6, 3))
    assert len(recent) == 1
    assert recent[0].raw_ref == "conv-2"


def test_counts_by_channel(tmp_path):
    conn = store.connect(tmp_path / "t.sqlite")
    store.ingest_touches(conn, [_touch("a", datetime(2026, 6, 1))], "2026-06-08T00:00:00")
    assert store.counts_by_channel(conn) == {"genesys": 1}
