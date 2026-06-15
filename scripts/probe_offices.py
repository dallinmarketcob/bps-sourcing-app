"""Probe how a global key surfaces per-office subscriptions, and what the initial
appointment-status values look like (for the 'only scheduled' filter)."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402

OFFICES = ["1", "2", "3", "4", "5", "6", "8", "9", "10"]
BET = json.dumps({"operator": "BETWEEN", "value": ["2026-05-31 00:00:00", "2026-06-06 23:59:59"]})


def main():
    s = load_settings()
    with client_from_settings(s) as pr:
        print("=== per-office subscription counts (week), various param styles ===")
        for oid in OFFICES:
            r1 = pr.request("subscription", "search", {"dateAdded": BET, "officeIDs": oid})
            r2 = pr.request("subscription", "search", {"dateAdded": BET, "officeID": oid})
            print(f"  office {oid:>2}: officeIDs={r1.get('count'):>4}  officeID={r2.get('count'):>4}")

        # Inspect initial-status values on a recent office-1 batch.
        print("\n=== initial appointment status distribution (office 1 week) ===")
        r = pr.request("subscription", "search", {"dateAdded": BET, "includeData": 1})
        subs = r.get("subscriptions") or []
        st = Counter(su.get("initialStatusText") for su in subs)
        active = Counter(su.get("activeText") or su.get("active") for su in subs)
        print("  initialStatusText:", dict(st))
        print("  active:", dict(active))
        # Show a couple of canceled/odd ones for the field shape.
        for su in subs[:1]:
            print("  sample fields:", {k: su.get(k) for k in
                  ("initialStatus", "initialStatusText", "active", "activeText",
                   "dateCancelled", "nextAppointmentDueDate", "appointmentIDs",
                   "completedAppointmentIDs", "initialAppointmentID")})


if __name__ == "__main__":
    main()
