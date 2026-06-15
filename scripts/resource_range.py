"""Re-source every sold subscription whose dateAdded falls in a date range,
against the FULL touch store. FILLs blanks + CHANGEs wrong sources; never
overwrites a protected source; leaves AGREE/no-evidence alone. Each real write
is read-back verified and logged to the `writes` audit table.

Dry-run by default; pass --write to apply. Writes a review CSV either way:
  data/resource_<start>_<end>.csv

Usage:  python scripts/resource_range.py 2026-05-01 2026-06-10 [--write]
"""
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource import store, writeback  # noqa: E402
from leadsource.attribution import attribute_all  # noqa: E402
from leadsource.config import load_settings  # noqa: E402
from leadsource.display import decision_label, friendly_source  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings, pull_sold_subscriptions  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

INVENTORY = ROOT / "data" / "pestroutes_source_inventory.json"


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: python scripts/resource_range.py <start YYYY-MM-DD> <end YYYY-MM-DD> [--write]")
        return 1
    start, end = sys.argv[1], sys.argv[2]
    do_write = "--write" in sys.argv[3:]
    s = load_settings()
    ran_at = datetime.now(timezone.utc).isoformat()
    conn = store.connect(s.db_path)
    src2prov = load_source_map_csv(s.master_sheet).source_to_provider
    inventory = json.loads(INVENTORY.read_text())

    print(f"=== RE-SOURCE {start} .. {end} | {'LIVE WRITE' if do_write else 'DRY-RUN'} ===", flush=True)
    # Full touch history (backfilled to Jan 1) so old leads are available.
    touches = store.load_touches(conn, since=datetime(2026, 1, 1, tzinfo=timezone.utc))
    print(f"touches loaded: {len(touches)}", flush=True)

    with client_from_settings(s) as pr:
        subs = pull_sold_subscriptions(
            pr, f"{start} 00:00:00", f"{end} 00:00:00",
            office_ids=s.office_id_list, sourceable_only=True)
        subs = [x for x in subs if x.sold_at]
        print(f"sourceable subscriptions in range: {len(subs)}", flush=True)

        results = attribute_all(
            subs, touches, stale_window_days=s.stale_window_days,
            same_day_cluster_hours=s.same_day_cluster_hours,
            protected_sources=s.protected_source_set,
            meta_source=s.meta_lead_source, website_form_source=s.website_form_source,
            meta_form_tiebreak_minutes=s.meta_form_tiebreak_minutes)
        by_id = {x.subscription_id: x for x in subs}

        cids = sorted({x.customer_id for x in subs})
        names: dict[str, str] = {}
        for i in range(0, len(cids), 200):
            for c in (pr.get_customers(cids[i:i + 200]).get("customers") or []):
                names[str(c.get("customerID"))] = f"{c.get('fname','')} {c.get('lname','')}".strip()

        writes = writeback.apply_writeback(
            pr, results, by_id, inventory, conn, ran_at, names=names, dry_run=not do_write)
    write_status = {w.subscription_id: w.status for w in writes}

    out = ROOT / "data" / f"resource_{start}_{end}.csv"
    order = {"DISAGREE (change)": 0, "FILL (was blank)": 1, "AGREE": 2,
             "PROTECTED (kept)": 3, "NO EVIDENCE (kept)": 4, "UNSOURCED (no source on file)": 5}
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["subscription_id", "customer", "sold_at", "current_source", "engine_source",
                    "decision", "write_status", "matched_on", "evidence", "evidence_date", "reason"])
        for r in sorted(results, key=lambda r: order.get(decision_label(r, by_id[r.subscription_id].current_source), 9)):
            sub = by_id[r.subscription_id]; wt = r.winning_touch
            w.writerow([
                r.subscription_id, names.get(sub.customer_id, ""),
                sub.sold_at.strftime("%Y-%m-%d %H:%M"),
                friendly_source(sub.current_source, None, src2prov) if sub.current_source else "",
                friendly_source(r.attributed_source, wt.channel.value if wt else None, src2prov),
                decision_label(r, sub.current_source), write_status.get(r.subscription_id, "-"),
                r.matched_key.value if wt else "",
                f"{wt.channel.value}/{friendly_source(wt.source, wt.channel.value, src2prov)}" if wt else "",
                wt.occurred_at.strftime("%Y-%m-%d %H:%M") if wt and wt.occurred_at else "",
                r.reason])

    tally = Counter(decision_label(r, by_id[r.subscription_id].current_source) for r in results)
    wtally = Counter(w.status for w in writes)
    store.record_run(conn, ran_at, "resource_range", f"{start}..{end} write={do_write} writes={len(writes)}")
    print(f"\n{len(subs)} sales -> {out}", flush=True)
    print("decisions:", dict(tally), flush=True)
    print(f"writes ({'LIVE' if do_write else 'DRY-RUN'}):", dict(wtally), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
