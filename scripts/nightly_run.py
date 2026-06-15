"""Nightly run: ingest all touches -> source the last day's sales (write-back)
-> daily sourcing report.

WRITE-BACK is gated by DRY_RUN in .env (default True). While dry-run, it plans
every change and reports it, but writes nothing to PestRoutes.

Usage: python scripts/nightly_run.py [ingest_days=7] [source_days=2]
"""
import csv
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource import pipeline, store, writeback  # noqa: E402
from leadsource.attribution import attribute_all  # noqa: E402
from leadsource.config import load_settings  # noqa: E402
from leadsource.display import decision_label, friendly_source  # noqa: E402
from leadsource.models import MatchKey  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

INVENTORY = ROOT / "data" / "pestroutes_source_inventory.json"


def key_value(sub, mk):
    return {MatchKey.PHONE1: sub.phone1_e164, MatchKey.PHONE2: sub.phone2_e164,
            MatchKey.EMAIL: sub.email}.get(mk)


def main():
    ingest_days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    source_days = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    s = load_settings()
    now = datetime.now(timezone.utc)
    ran_at = now.isoformat()
    conn = store.connect(s.db_path)
    src2prov = load_source_map_csv(s.master_sheet).source_to_provider
    inventory = json.loads(INVENTORY.read_text()) if INVENTORY.exists() else {}

    print(f"=== NIGHTLY RUN {now:%Y-%m-%d %H:%M} UTC | DRY_RUN={s.dry_run} ===")

    # 1) Ingest every channel.
    print("\n[1] ingest touches")
    for name, res in pipeline.ingest(conn, s, days=ingest_days).items():
        print(f"    {name:8} {res}")

    # 2) Pull the last day's sales, attribute against the full store. The touch
    # window must honor the NO-AGE-CUTOFF rule: a 90+ day-old lead still earns
    # credit when nothing newer arrived (a fixed 60d window left such sales
    # unsourced — found via a 98-day-old DoLead lead).
    print(f"\n[2] sourcing sales from the last {source_days} day(s)")
    touches = store.load_touches(conn, since=now - timedelta(days=s.lookback_days))
    with client_from_settings(s) as pr:
        subs = pipeline.pull_recent_subscriptions(pr, s, days=source_days)
        results = attribute_all(
            subs, touches, stale_window_days=s.stale_window_days,
            same_day_cluster_hours=s.same_day_cluster_hours,
            protected_sources=s.protected_source_set,
            meta_source=s.meta_lead_source, website_form_source=s.website_form_source,
            meta_form_tiebreak_minutes=s.meta_form_tiebreak_minutes,
        )
        by_id = {x.subscription_id: x for x in subs}
        cids = sorted({x.customer_id for x in subs})
        names: dict[str, str] = {}
        for i in range(0, len(cids), 200):
            for c in (pr.get_customers(cids[i:i + 200]).get("customers") or []):
                names[str(c.get("customerID"))] = f"{c.get('fname','')} {c.get('lname','')}".strip()

        # 3) Write back (dry-run honored), logged to the audit trail.
        writes = writeback.apply_writeback(
            pr, results, by_id, inventory, conn, ran_at, names=names, dry_run=s.dry_run)
    write_status = {w.subscription_id: w.status for w in writes}

    # 4) Daily sourcing report.
    out = ROOT / "data" / f"sourcing_{now:%Y%m%d}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["subscription_id", "customer", "sold_at", "current_source",
                    "engine_source", "decision", "write_status", "matched_on",
                    "evidence", "evidence_date", "reason"])
        order = {"DISAGREE (change)": 0, "FILL (was blank)": 1, "AGREE": 2,
                 "PROTECTED (kept)": 3, "NO EVIDENCE (kept)": 4,
                 "UNSOURCED (no source on file)": 5}
        rows = sorted(results, key=lambda r: order.get(decision_label(r, by_id[r.subscription_id].current_source), 9))
        for r in rows:
            sub = by_id[r.subscription_id]
            wt = r.winning_touch
            dec = decision_label(r, sub.current_source)
            w.writerow([
                r.subscription_id, names.get(sub.customer_id, ""),
                sub.sold_at.strftime("%Y-%m-%d %H:%M"),
                friendly_source(sub.current_source, None, src2prov) if sub.current_source else "",
                friendly_source(r.attributed_source, wt.channel.value if wt else None, src2prov),
                dec, write_status.get(r.subscription_id, "-"),
                r.matched_key.value if wt else "",
                f"{wt.channel.value}/{friendly_source(wt.source, wt.channel.value, src2prov)}" if wt else "",
                wt.occurred_at.strftime("%Y-%m-%d %H:%M") if wt and wt.occurred_at else "",
                r.reason,
            ])

    # Summary.
    from collections import Counter
    tally = Counter(decision_label(r, by_id[r.subscription_id].current_source) for r in results)
    wtally = Counter(w.status for w in writes)
    print(f"\n[3] {len(subs)} sales sourced -> {out}")
    print("    decisions:", dict(tally))
    print(f"    writes ({'DRY-RUN' if s.dry_run else 'LIVE'}): {dict(wtally)}")
    store.record_run(conn, ran_at, "nightly", f"subs={len(subs)} writes={len(writes)} dry_run={s.dry_run}")


if __name__ == "__main__":
    main()
