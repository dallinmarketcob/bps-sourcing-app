"""Full audit CSV for a sold-date range: EVERY sold subscription in the window,
the source the engine would assign, and the supporting evidence — for manual
verification. No write-back.

Usage: python scripts/audit_report.py [start YYYY-MM-DD] [end YYYY-MM-DD]
       (defaults to 2026-05-31 .. 2026-06-06)
"""
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource import store  # noqa: E402
from leadsource.attribution import attribute_all  # noqa: E402
from leadsource.config import load_settings  # noqa: E402
from leadsource.models import MatchKey  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings, pull_sold_subscriptions  # noqa: E402


def key_value(sub, mk: MatchKey):
    return {MatchKey.PHONE1: sub.phone1_e164, MatchKey.PHONE2: sub.phone2_e164,
            MatchKey.EMAIL: sub.email}.get(mk)


def decide(r, sub):
    blank = not sub.current_source
    if r.is_unsourced:  # engine found no inbound touch
        return "UNSOURCED (no source on file)" if blank else "NO EVIDENCE (kept)"
    if r.protected:
        return "PROTECTED (kept)"
    if blank:
        return "FILL (was blank)"
    return "DISAGREE" if r.is_change else "AGREE"


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2026-05-31"
    end = sys.argv[2] if len(sys.argv) > 2 else "2026-06-06"
    since = f"{start} 00:00:00"
    until = f"{end} 23:59:59"

    s = load_settings()
    now = datetime.now(timezone.utc)
    conn = store.connect(s.db_path)
    touches = store.load_touches(conn, since=now - timedelta(days=70))
    print(f"loaded {len(touches)} touches from store")

    with client_from_settings(s) as pr:
        subs = [x for x in pull_sold_subscriptions(
            pr, since, until, office_ids=s.office_id_list, sourceable_only=True
        ) if x.sold_at]
        print(f"sourceable sold subscriptions {start}..{end} (all offices): {len(subs)}")

        results = attribute_all(subs, touches, stale_window_days=s.stale_window_days,
                                same_day_cluster_hours=s.same_day_cluster_hours,
                                protected_sources=s.protected_source_set,
                                meta_source=s.meta_lead_source,
                                website_form_source=s.website_form_source,
                                meta_form_tiebreak_minutes=s.meta_form_tiebreak_minutes)
        by_id = {x.subscription_id: x for x in subs}

        order = {"DISAGREE": 0, "FILL (was blank)": 1, "UNSOURCED (no source on file)": 2,
                 "AGREE": 3, "PROTECTED (kept)": 4, "NO EVIDENCE (kept)": 5}
        results.sort(key=lambda r: order.get(decide(r, by_id[r.subscription_id]), 9))

        # Names for every audited customer.
        cids = sorted({x.customer_id for x in subs})
        names: dict[str, str] = {}
        for i in range(0, len(cids), 200):
            got = pr.get_customers(cids[i:i + 200])
            for c in got.get("customers") or []:
                names[str(c.get("customerID"))] = f"{c.get('fname','')} {c.get('lname','')}".strip()

    out = ROOT / "data" / f"audit_week_{start}_to_{end}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "subscription_id", "customer_name", "sold_at", "current_source",
            "engine_assigned_source", "decision", "matched_on", "matched_value",
            "evidence_channel", "evidence_source", "evidence_date", "streak_touches",
            "reason",
        ])
        for r in results:
            sub = by_id[r.subscription_id]
            wt = r.winning_touch
            w.writerow([
                r.subscription_id, names.get(sub.customer_id, ""),
                sub.sold_at.strftime("%Y-%m-%d %H:%M"),
                sub.current_source or "", r.attributed_source or "", decide(r, sub),
                r.matched_key.value if wt else "", key_value(sub, r.matched_key) or "" if wt else "",
                wt.channel.value if wt else "", wt.source if wt else "",
                wt.occurred_at.strftime("%Y-%m-%d %H:%M") if wt and wt.occurred_at else "",
                len(r.streak), r.reason,
            ])

    from collections import Counter
    tally = Counter(decide(r, by_id[r.subscription_id]) for r in results)
    print(f"\nwrote {len(results)} rows -> {out}")
    print("breakdown:", dict(tally))


if __name__ == "__main__":
    main()
