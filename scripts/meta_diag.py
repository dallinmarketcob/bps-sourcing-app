"""Diagnose Meta access: granted permissions + where the Pages live
(personal vs Business-owned vs client). Read-only; names only, no lead PII."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.meta import MetaClient  # noqa: E402


def main():
    s = load_settings()
    with MetaClient(s.meta_access_token) as m:
        perms = m.get("me/permissions")
        granted = sorted(p["permission"] for p in perms.get("data", []) if p.get("status") == "granted")
        print("granted permissions:", granted)

        print("\n-- me/accounts (direct page roles) --")
        accts = list(m.paged("me/accounts", {"fields": "id,name"}))
        print(f"  {len(accts)} pages")

        print("\n-- me/businesses --")
        try:
            bizes = list(m.paged("me/businesses", {"fields": "id,name"}))
        except Exception as e:
            bizes = []
            print("  error:", str(e)[:120])
        for b in bizes:
            print(f"  business {b['id']}  {b.get('name')}")
            for edge in ("owned_pages", "client_pages"):
                try:
                    pages = list(m.paged(f"{b['id']}/{edge}", {"fields": "id,name"}))
                    print(f"    {edge}: {len(pages)}")
                    for p in pages[:25]:
                        print(f"      page {p['id']}  {p.get('name')}")
                except Exception as e:
                    print(f"    {edge}: error {str(e)[:100]}")


if __name__ == "__main__":
    main()
