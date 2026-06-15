"""Read-only LSA / Google Ads probe. Safe to run anytime.

Confirms each layer in order so we know exactly what's working:
  1) OAuth refresh -> access token  (works now, before Basic Access)
  2) List child accounts under the MCC
  3) Pull a sample local_services_lead and DUMP raw fields (to validate names)

Before Basic Access is granted, step 2/3 will return a clear
"developer token not approved / test accounts only" error — that's expected and
tells us we're just waiting on the access upgrade.

Usage: python scripts/lsa_probe.py [since_days=30]
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.lsa import LSAError, client_from_settings  # noqa: E402


def main() -> int:
    since_days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    s = load_settings()
    missing = [k for k in ("google_ads_developer_token", "google_ads_login_customer_id",
                           "google_ads_client_id", "google_ads_client_secret",
                           "google_ads_refresh_token") if not getattr(s, k)]
    if missing:
        print("Missing creds in .env:", ", ".join(missing))
        return 1

    client = client_from_settings(s)
    print(f"MCC (login-customer-id): {client._login}   API {s.google_ads_api_version}")

    # 1) OAuth
    try:
        tok = client._access_token()
        print(f"[1] OAuth OK — access token acquired ({len(tok)} chars)")
    except LSAError as e:
        print(f"[1] OAuth FAILED: {e}")
        return 1

    # 2) child accounts
    try:
        accts = client.child_accounts()
        print(f"[2] child accounts under MCC: {len(accts)} -> {accts}")
    except LSAError as e:
        print(f"[2] account list FAILED: {e}")
        if "test account" in str(e).lower() or "DEVELOPER_TOKEN" in str(e):
            print("    -> This is the Basic-Access gate. OAuth works; we just need "
                  "Basic Access approved, then re-run. Nothing else to fix.")
        return 0

    # 3) sample lead + raw field dump
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%d %H:%M:%S")
    query = ("SELECT local_services_lead.id, local_services_lead.lead_type, "
             "local_services_lead.contact_details, local_services_lead.lead_status, "
             "local_services_lead.creation_date_time FROM local_services_lead "
             f"WHERE local_services_lead.creation_date_time >= '{since}' LIMIT 3")
    for cid in accts:
        try:
            rows = client.search(cid, query)
        except LSAError as e:
            print(f"[3] {cid}: {str(e)[:120]}")
            continue
        if rows:
            print(f"[3] sample lead from account {cid} (raw JSON to validate field names):")
            print(json.dumps(rows[0], indent=2)[:1200])
            return 0
    print("[3] OAuth + accounts work, but no leads found in the window across accounts.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
