"""Read-only Meta probe: verify token, list Pages + lead forms, sample leads.

PII (lead phone/email/name) is masked. Run after putting META_ACCESS_TOKEN in
.env.  Usage: python scripts/meta_probe.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.meta import MetaClient, _field  # noqa: E402


def mask(v):
    if not v:
        return "-"
    return v[:2] + "***" + v[-2:] if len(v) > 4 else "***"


def main():
    s = load_settings()
    with MetaClient(s.meta_access_token) as m:
        who = m.me()
        print(f"token identity: {who.get('name')} (id {who.get('id')})")

        pages = m.list_pages()
        print(f"\nmanaged pages: {len(pages)}")
        for p in pages:
            print(f"  page {p['id']}  {p.get('name')}")

        # Inspect lead forms + a few leads per page.
        for p in pages:
            ptoken = p.get("access_token")
            forms = m.list_lead_forms(p["id"], ptoken)
            if not forms:
                continue
            print(f"\n=== Page '{p.get('name')}' -> {len(forms)} lead forms ===")
            for f in forms[:10]:
                print(f"  form {f['id']}  [{f.get('status')}]  {f.get('name')}")
            # Sample leads from the first form that has any.
            for f in forms:
                leads = m.get_leads(f["id"], ptoken)
                if leads:
                    print(f"\nsample leads from form '{f.get('name')}' ({len(leads)} total):")
                    print(f"  field names: {[fd.get('name') for fd in leads[0].get('field_data', [])]}")
                    for lead in leads[:3]:
                        print(f"  {lead.get('created_time')} | campaign={lead.get('campaign_name')} "
                              f"| phone={mask(_field(lead,'phone_number','phone'))} "
                              f"| email={mask(_field(lead,'email'))}")
                    break


if __name__ == "__main__":
    main()
