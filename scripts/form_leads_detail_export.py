"""Export FORM leads (Meta + Gmail + LSA message/booking) for a date range with
NAME, PHONE, EMAIL, and PEST DETAILS parsed from the raw source (the touch store
only keeps phone/email, so we re-pull). Dates are local Pacific.

Also provides the name/pest parsing helpers reused by dispute_reports.py.

Usage: python scripts/form_leads_detail_export.py 2026-06-14 2026-06-16  (end exclusive)
"""
import csv
import email as emaillib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.pipeline import load_maps  # noqa: E402
from leadsource.normalize import normalize_email, normalize_phone  # noqa: E402
from leadsource.readers.gmail import GmailReader, build_touch_from_email  # noqa: E402
from leadsource.readers.lsa import client_from_settings as lsa_client  # noqa: E402
from leadsource.readers.meta import MetaClient, _field  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
STD = {"full_name", "first_name", "last_name", "name", "phone_number", "phone",
       "work_phone_number", "email", "work_email", "zip_code", "zip", "city",
       "state", "street_address", "post_code"}


def body_text(msg):
    parts = msg.walk() if msg.is_multipart() else [msg]
    plain = html = ""
    for p in parts:
        ct = p.get_content_type()
        if ct == "text/plain" and not plain:
            plain = (p.get_payload(decode=True) or b"").decode("utf-8", "ignore")
        elif ct == "text/html" and not html:
            html = (p.get_payload(decode=True) or b"").decode("utf-8", "ignore")
    txt = plain or re.sub("<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", txt)


def grab(body, pats):
    for pat in pats:
        m = re.search(pat, body, re.I)
        if m and m.group(1).strip():
            return re.sub(r"\s+", " ", m.group(1)).strip(" -:|").strip()[:140]
    return ""


def extract_name(body, frm_display):
    fn = grab(body, [r"First Name[:=]\s*([^|]+?)\s+Last Name[:=]"])
    ln = grab(body, [r"Last Name[:=]\s*([^|]+?)(?:\s+Phone|\s+Email|\s+Zip|$)"])
    if fn or ln:
        return f"{fn} {ln}".strip()
    n = grab(body, [r"Full Name[:=]\s*([^|]+?)(?:Phone|Email|$)",
                    r"\bName[:=]\s*([^|]+?)(?:Phone|Email|Address|$)",
                    r"Customer[:=]\s*([^|]+?)(?:Phone|Email|$)"])
    return n


def extract_pest(body):
    return grab(body, [
        r"Describe Your Pest Problem\s*[=:]\s*([^|]+?)(?:Tag:|Source:|Visit this|$)",
        r"\bPest[:=]\s*([^|]+?)(?:Tag:|Source:|Zip|$)",
        r"How Can We Help You\??\s*[:=]\s*([^|]+?)(?:Reply|$)",
        r"Service(?: Needed| Requested)?[:=]\s*([^|]+?)(?:Phone|Email|$)",
        r"Reason for (?:call|inquiry)[:=]\s*([^|]+)",
        r"Message[:=]\s*([^|]+?)(?:Reply|Visit this|$)",
        r"Comments?[:=]\s*([^|]+)",
    ])


def main() -> int:
    start_d, end_d = sys.argv[1], sys.argv[2]
    s = load_settings()
    source_map, rules = load_maps(s)
    s2p = source_map.source_to_provider
    start_pt = datetime.fromisoformat(start_d).replace(tzinfo=PT)
    end_pt = datetime.fromisoformat(end_d).replace(tzinfo=PT)
    start_utc = start_pt.astimezone(timezone.utc)
    end_utc = end_pt.astimezone(timezone.utc)
    rows = []  # name, phone, email, pest, channel, source/provider, dt

    # --- GMAIL ---
    gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
    q = f"{s.gmail_lead_query_prefix} after:{start_pt:%Y/%m/%d} before:{end_pt:%Y/%m/%d}".strip()
    for mid, raw in gm.fetch_raw_messages(q, max_results=3000):
        res = build_touch_from_email(raw, rules, source_map, raw_ref=mid)
        if not res.ok or not res.touch:
            continue
        msg = emaillib.message_from_bytes(raw)
        body = body_text(msg)
        prov = s2p.get(res.touch.source, res.touch.source)
        dt = res.touch.occurred_at
        rows.append([extract_name(body, msg.get("From", "")),
                     res.touch.phone_e164 or "", res.touch.email or "",
                     extract_pest(body), "Gmail", prov,
                     dt.replace(tzinfo=timezone.utc).astimezone(PT).strftime("%Y-%m-%d %I:%M %p") if dt else ""])

    # --- META (re-pull leads with full field_data; loop every configured page) ---
    for pc in s.meta_page_configs:
        try:
            with MetaClient(pc["token"]) as mc:
                pid = pc["page_id"]
                page_token = mc.get(pid, {"fields": "access_token"}).get("access_token")
                forms = list(mc.paged(f"{pid}/leadgen_forms", {"fields": "id,leads_count"}, token=page_token))
                for f in forms:
                    if not f.get("leads_count"):
                        continue
                    for lead in mc.get_leads(f["id"], page_token, since_unix=int(start_utc.timestamp())):
                        ct = lead.get("created_time", "")
                        try:
                            when = datetime.fromisoformat(ct.replace("Z", "+00:00"))
                        except ValueError:
                            when = None
                        if not when or not (start_utc <= when < end_utc):
                            continue
                        name = _field(lead, "full_name") or " ".join(
                            x for x in [_field(lead, "first_name"), _field(lead, "last_name")] if x)
                        pest = "; ".join(f"{fd.get('name')}: {(fd.get('values') or [''])[0]}"
                                         for fd in lead.get("field_data", [])
                                         if (fd.get("name") or "").lower() not in STD
                                         and (fd.get("values") or [""])[0])
                        rows.append([name or "", normalize_phone(_field(lead, "phone_number", "phone")) or "",
                                     normalize_email(_field(lead, "email")) or "", pest[:200], "Meta",
                                     "Meta Instant Form", when.astimezone(PT).strftime("%Y-%m-%d %I:%M %p")])
        except Exception as e:
            print(f"meta error (page {pc['page_id']}):", str(e)[:160])

    # --- LSA message/booking ---
    try:
        ql = ("SELECT local_services_lead.lead_type, local_services_lead.contact_details, "
              "local_services_lead.creation_date_time FROM local_services_lead WHERE "
              f"local_services_lead.creation_date_time >= '{start_d} 00:00:00' "
              f"AND local_services_lead.creation_date_time < '{end_d} 00:00:00'")
        with lsa_client(s) as lc:
            for cid in lc.child_accounts():
                try:
                    for r in lc.search(cid, ql):
                        lead = r.get("localServicesLead", {})
                        if lead.get("leadType") not in ("MESSAGE", "BOOKING"):
                            continue
                        d = lead.get("contactDetails") or {}
                        if isinstance(d, list):
                            d = d[0] if d else {}
                        raw = lead.get("creationDateTime", "")[:19]
                        try:
                            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=PT)
                        except ValueError:
                            dt = None
                        rows.append([d.get("consumerName") or "", normalize_phone(d.get("phoneNumber")) or "",
                                     normalize_email(d.get("email")) or "", lead.get("leadType", "").title(),
                                     "LSA", "Local Service Ads",
                                     dt.strftime("%Y-%m-%d %I:%M %p") if dt else ""])
                except Exception:
                    continue
    except Exception as e:
        print("lsa error:", str(e)[:160])

    rows.sort(key=lambda r: r[6])
    out = ROOT / "data" / f"form_leads_detail_{start_d}_{end_d}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Name", "Phone", "Email", "Pest Details", "Channel", "Source", "DateTime (PT)"])
        w.writerows(rows)
    from collections import Counter
    print(f"rows: {len(rows)} -> {out}")
    print("by channel:", dict(Counter(r[4] for r in rows)))
    print("with a name:", sum(1 for r in rows if r[0]), "| with pest detail:", sum(1 for r in rows if r[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
