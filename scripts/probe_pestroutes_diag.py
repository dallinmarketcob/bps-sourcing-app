"""Triangulate the working endpoint/method for this PestRoutes account.

Tries several entity/verb/method/param combinations read-only and reports which
ones return success. Prints only success flags, error messages, and JSON key
names -- no record contents (so no PII).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import PestRoutesClient, PestRoutesError  # noqa: E402

ATTEMPTS = [
    ("source", "search", "GET", {}),
    ("source", "search", "GET", {"includeData": 1}),
    ("source", "search", "POST", {}),
    ("sources", "search", "GET", {}),
    ("source", "get", "GET", {}),
    ("subscription", "search", "GET", {}),
    ("customer", "search", "GET", {"officeIDs": ""}),
    ("region", "search", "GET", {}),
]


def main():
    s = load_settings()
    client = PestRoutesClient(
        s.pestroutes_base_url, s.pestroutes_auth_key, s.pestroutes_auth_token
    )
    for entity, action, method, params in ATTEMPTS:
        label = f"{method:4} {entity}/{action} {params}"
        try:
            resp = client.request(entity, action, params, method=method)
        except PestRoutesError as e:
            print(f"[ERR ] {label}\n        {str(e)[:160]}")
            continue
        if isinstance(resp, dict):
            success = resp.get("success")
            err = resp.get("errorMessage")
            keys = [k for k in resp if k not in ("success", "errorMessage")]
            print(f"[{str(success):>5}] {label}")
            if err:
                print(f"        errorMessage: {err[:120]}")
            if keys:
                print(f"        other keys: {keys}")
        else:
            print(f"[ ?? ] {label} -> {type(resp).__name__}")
    client.close()


if __name__ == "__main__":
    main()
