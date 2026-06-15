"""Find the working subscription/search date filter so we can pull a recent
window instead of all 50000. Tries a few PestRoutes filter formats, reports
which one reduces the count. Read-only, no PII.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402

SINCE = "2026-06-01 00:00:00"


def count(client, params, label):
    try:
        resp = client.request("subscription", "search", params)
        c = resp.get("count") if isinstance(resp, dict) else "?"
        ignored = resp.get("ignoredParams") if isinstance(resp, dict) else None
        print(f"[{c:>7}] {label}   ignored={ignored}")
    except Exception as e:
        print(f"[  ERR ] {label}: {str(e)[:120]}")


def main():
    s = load_settings()
    with client_from_settings(s) as client:
        count(client, {}, "no filter (baseline)")
        count(client, {"dateAdded": json.dumps({"operator": ">", "value": SINCE})},
              'dateAdded {">":value}')
        count(client, {"dateAdded": json.dumps({"operator": ">=", "value": SINCE})},
              'dateAdded {">=":value}')
        count(client, {"dateAdded": json.dumps(
            {"operator": "BETWEEN", "value": [SINCE, "2026-06-09 00:00:00"]})},
              'dateAdded BETWEEN')
        count(client, {"dateAdded": SINCE}, "dateAdded plain string")


if __name__ == "__main__":
    main()
