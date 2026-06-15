"""Try to find an endpoint that enumerates ALL sources (with IDs), regardless of
whether they've been used on a sale. If one exists we get 100% coverage without
scanning subscriptions. Prints success flags + key names only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import PestRoutesClient, PestRoutesError  # noqa: E402

CANDIDATES = [
    ("source", "search", {"officeIDs": "1"}),
    ("source", "getOptions", {}),
    ("sources", "search", {"officeIDs": "1"}),
    ("leadSource", "search", {}),
    ("customerSource", "search", {}),
    ("marketingSource", "search", {}),
    ("genericFlag", "search", {}),
    ("documentation", "get", {}),
    ("setting", "search", {}),
    ("office", "search", {}),
]


def main():
    s = load_settings()
    c = PestRoutesClient(s.pestroutes_base_url, s.pestroutes_auth_key, s.pestroutes_auth_token)
    for entity, action, params in CANDIDATES:
        try:
            resp = c.request(entity, action, params)
        except PestRoutesError as e:
            print(f"[ERR ] {entity}/{action} {params}: {str(e)[:90]}")
            continue
        if isinstance(resp, dict):
            ok = resp.get("success")
            err = resp.get("errorMessage")
            keys = [k for k in resp if k not in ("params", "tokenUsage", "tokenLimits")]
            print(f"[{str(ok):>5}] {entity}/{action} {params}")
            if err:
                print(f"        err: {err[:80]}")
            else:
                print(f"        keys: {keys}")
        else:
            print(f"[ ?? ] {entity}/{action}: {type(resp).__name__}")
    c.close()


if __name__ == "__main__":
    main()
