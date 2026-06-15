"""Read-only: inspect a real subscription + customer to learn FIELD NAMES.

PII-safe: prints the list of field names, and the *values* only for non-sensitive
source/date fields (sourceID, source, subSource, dateAdded, dateSold, etc.).
Name/phone/email/address values are shown as "<set>"/"<empty>" only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402

# Fields whose values are safe to print (not PII) — anything source/date/id-ish.
SAFE_VALUE_HINTS = ("source", "date", "sold", "status", "active", "office", "id")
# Fields we must never print values for.
PII_HINTS = ("name", "fname", "lname", "phone", "email", "address", "street", "zip", "city")


def show_record(label, rec):
    print(f"\n=== {label}: {len(rec)} fields ===")
    print("field names:", sorted(rec.keys()))
    print("\nsource/date-ish field values:")
    for k in sorted(rec.keys()):
        low = k.lower()
        if any(p in low for p in PII_HINTS):
            v = rec.get(k)
            shown = "<empty>" if v in (None, "", "0") else "<set>"
            print(f"  {k}: {shown}")
        elif any(h in low for h in SAFE_VALUE_HINTS):
            print(f"  {k}: {rec.get(k)!r}")


def first_record(resp, id_key):
    """Pull the records list from a get-response across likely shapes."""
    if isinstance(resp, dict):
        for key in (id_key, "data", "rows", "result"):
            v = resp.get(key)
            if isinstance(v, list) and v:
                return v[0]
            if isinstance(v, dict) and v:
                return next(iter(v.values()))
    if isinstance(resp, list) and resp:
        return resp[0]
    return None


def main():
    s = load_settings()
    with client_from_settings(s) as client:
        search = client.search_subscriptions()
        ids = search.get("subscriptionIDs") if isinstance(search, dict) else None
        print("subscription count:", search.get("count") if isinstance(search, dict) else "?")
        if not ids:
            print("No subscription IDs returned; raw keys:", list(search) if isinstance(search, dict) else type(search))
            return

        # Grab the most RECENT subscriptions (highest IDs); ID 1 is long deleted.
        recent = [str(i) for i in ids][-25:]
        print("trying most-recent IDs (highest):", recent[-5:])
        got = client.get_subscriptions(recent)
        records = got.get("subscriptions") if isinstance(got, dict) else None
        sub = records[0] if records else None
        if not sub:
            print("No records for the recent IDs. Returned count:", got.get("count"))
            return
        print("inspecting subscriptionID:", sub.get("subscriptionID") or sub.get("subscriptionId"))
        show_record("SUBSCRIPTION", sub)

        cust_id = sub.get("customerID") or sub.get("customerId")
        if cust_id:
            cust_resp = client.get_customers([cust_id])
            cust = first_record(cust_resp, "customers")
            if cust:
                show_record("CUSTOMER", cust)


if __name__ == "__main__":
    main()
