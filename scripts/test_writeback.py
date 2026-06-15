"""SAFE write-back round-trip test on ONE subscription.

Proves the PestRoutes source write works end-to-end WITHOUT changing any data:
  read current -> no-op write (same value) -> write a new value -> verify it
  changed -> REVERT to the original -> verify it's back.

Picks a DISAGREE row from the audit (current vs engine), so the "new value" is a
real, valid sourceID. A try/finally guarantees the revert runs.

Usage: python scripts/test_writeback.py [subscription_id]
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402

AUDIT = ROOT / "data" / "audit_week_2026-05-31_to_2026-06-06.csv"
INVENTORY = ROOT / "data" / "pestroutes_source_inventory.json"


def read_source(client, sub_id):
    recs = client.get_subscriptions([sub_id]).get("subscriptions") or []
    if not recs:
        return None, None
    return str(recs[0].get("sourceID")), recs[0].get("source")


def main():
    inv = json.loads(INVENTORY.read_text())  # {label: sourceID}
    rows = list(csv.DictReader(open(AUDIT, encoding="utf-8-sig")))

    # Choose a DISAGREE row whose current + engine labels both resolve to IDs.
    if len(sys.argv) > 1:
        target_rows = [r for r in rows if r["subscription_id"] == sys.argv[1]]
    else:
        target_rows = [
            r for r in rows
            if r["decision"] == "DISAGREE" and r["current_source"] in inv
            and r["engine_assigned_source"] in inv
        ]
    if not target_rows:
        print("No suitable test row found.")
        return
    row = target_rows[0]
    sub_id = row["subscription_id"]
    orig_label, new_label = row["current_source"], row["engine_assigned_source"]
    orig_id, new_id = inv[orig_label], inv[new_label]

    s = load_settings()
    with client_from_settings(s) as client:
        live_id, live_label = read_source(client, sub_id)
        print(f"TEST subscription {sub_id}")
        print(f"  live source: {live_label} (sourceID {live_id})")
        # Use the LIVE current as the revert target (authoritative).
        revert_id = live_id

        try:
            print(f"\n1) no-op write (sourceID {revert_id})...")
            r = client.update_subscription(sub_id, {"sourceID": revert_id})
            print(f"   success={r.get('success')}  -> read back: {read_source(client, sub_id)[0]}")

            print(f"\n2) change write -> {new_label} (sourceID {new_id})...")
            r = client.update_subscription(sub_id, {"sourceID": new_id})
            after = read_source(client, sub_id)[0]
            print(f"   success={r.get('success')}  -> read back: {after}  "
                  f"({'CHANGED OK' if after == str(new_id) else 'DID NOT CHANGE'})")
        finally:
            print(f"\n3) REVERT -> sourceID {revert_id}...")
            client.update_subscription(sub_id, {"sourceID": revert_id})
            back = read_source(client, sub_id)[0]
            print(f"   read back: {back}  ({'REVERTED OK' if back == str(revert_id) else 'REVERT FAILED!'})")


if __name__ == "__main__":
    main()
