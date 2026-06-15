from datetime import datetime

from leadsource import store, writeback
from leadsource.models import AttributionResult, AttributionStatus, Subscription

A = AttributionStatus.ATTRIBUTED


def _res(sid, source, is_change, protected=False):
    return AttributionResult(subscription_id=sid, status=A, attributed_source=source,
                             is_change=is_change, protected=protected)


def _sub(sid, current):
    return Subscription(subscription_id=sid, customer_id="c" + sid,
                        sold_at=datetime(2026, 6, 8), current_source=current)


def test_dry_run_plans_writes_but_changes_nothing(tmp_path):
    conn = store.connect(tmp_path / "t.sqlite")
    results = [
        _res("A", "Source 11", is_change=True),                 # FILL (blank current)
        _res("B", "Source 25", is_change=True),                 # CHANGE
        _res("C", "Source 11", is_change=False),                # AGREE -> no write
        _res("D", "Source 25", is_change=True, protected=True), # PROTECTED -> no write
    ]
    subs = {"A": _sub("A", None), "B": _sub("B", "Source 99"),
            "C": _sub("C", "Source 11"), "D": _sub("D", "Source 99")}
    inv = {"Source 11": "10130", "Source 25": "10140", "Source 99": "5"}

    writes = writeback.apply_writeback(
        client=None, results=results, subs_by_id=subs, inventory=inv,
        conn=conn, ran_at="2026-06-09T00:00:00", dry_run=True)

    assert {w.subscription_id for w in writes} == {"A", "B"}      # only needs_write
    assert all(w.status == "DRY_RUN" for w in writes)
    assert next(w for w in writes if w.subscription_id == "A").decision == "FILL"
    assert next(w for w in writes if w.subscription_id == "B").decision == "CHANGE"
    # logged to the audit trail
    assert conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 2


def test_unresolvable_source_is_skipped_not_written(tmp_path):
    conn = store.connect(tmp_path / "t.sqlite")
    results = [_res("A", "Source 999", is_change=True)]  # not in inventory
    subs = {"A": _sub("A", None)}
    writes = writeback.apply_writeback(
        client=None, results=results, subs_by_id=subs, inventory={},
        conn=conn, ran_at="2026-06-09T00:00:00", dry_run=False)
    assert writes[0].status == "NO_SOURCE_ID"  # never wrote a bad value
