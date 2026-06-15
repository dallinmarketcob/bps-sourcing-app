"""Investigate UNSOURCED sales. For each customer ID: confirm the subscription is
truly blank, show what the engine saw in the store, then ACTIVELY hunt for missed
evidence -- live Gmail (any lead email?) and live Genesys (any call, on a mapped
DNIS / in-scope queue?). Read-only. Classifies each as GENUINE no-evidence vs a
fixable GAP (unmapped DNIS, unmatched email domain, queue out of scope, or -- the
loud one -- an engine error where evidence existed and we still didn't source).

Usage: python scripts/investigate_unsourced.py 192241 245572 ...
"""
import email as emaillib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource import store  # noqa: E402
from leadsource.normalize import normalize_email, normalize_phone  # noqa: E402
from leadsource.readers.genesys import GenesysClient, extract_ani_dnis  # noqa: E402
from leadsource.readers.gmail import GmailReader, build_touch_from_email  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings, _parse_dt  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

QN = {"440aa16a-cf8c-49ec-99ed-a8c234b7779e": "InsideSales",
      "e0aeb21e-8416-4f48-8ff7-353a8d6850de": "IS-Spanish",
      "94cc2f96-ce45-4386-baa0-7bb47f04b698": "IS-Email",
      "98e75acd-e8d9-4d90-9227-28dc6881337e": "IS-Outbound",
      "9d8711ff-84f5-40db-bcad-2d907cf5cd7c": "CS-Spanish"}


def main() -> int:
    cids = sys.argv[1:]
    s = load_settings()
    sm = load_source_map_csv(s.master_sheet)
    source_map_obj, rules = sm, None
    from leadsource.pipeline import load_maps
    sm, rules = load_maps(s)
    in_scope = set(s.genesys_queue_ids)
    conn = store.connect(s.db_path)
    allt = store.load_touches(conn, since=datetime(2026, 1, 1, tzinfo=timezone.utc))

    with client_from_settings(s) as pr:
        custs = {str(c["customerID"]): c for c in (pr.get_customers(cids).get("customers") or [])}
        sub_ids, cust_sub = [], {}
        for cid, c in custs.items():
            ids = [x for x in str(c.get("subscriptionIDs") or "").split(",") if x.strip()]
            cust_sub[cid] = ids
            sub_ids += ids
        subs = {str(r["subscriptionID"]): r
                for r in (pr.get_subscriptions(sub_ids).get("subscriptions") or [])}

    gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
    verdicts = {}
    with GenesysClient(s.genesys_region, s.genesys_client_id, s.genesys_client_secret) as gx:
        for cid in cids:
            c = custs.get(cid)
            print("=" * 78)
            if not c:
                print(f"customer {cid}: NOT FOUND in PestRoutes"); continue
            p1 = normalize_phone(c.get("phone1")); p2 = normalize_phone(c.get("phone2"))
            em = normalize_email(c.get("email"))
            keys = {k for k in (p1, p2) if k}
            print(f"cust {cid}  {c.get('fname','')} {c.get('lname','')}  ph1={c.get('phone1')} "
                  f"ph2={c.get('phone2')} email={c.get('email')!r}")
            sold = None
            for sid in cust_sub.get(cid, []):
                r = subs.get(sid, {})
                so = _parse_dt(r.get("dateAdded"))
                sold = so or sold
                print(f"  sub {sid}: source={r.get('source')!r} sourceID={r.get('sourceID')} "
                      f"sold={r.get('dateAdded')} office={r.get('officeID')} status={r.get('initialStatusText')}")
            if not sold:
                sold = datetime.utcnow()

            # 1) what the engine saw
            st = sorted([t for t in allt if t.phone_e164 in keys or (em and t.email == em)],
                        key=lambda t: t.occurred_at or datetime.min)
            print(f"  STORE touches ({len(st)}): " +
                  (", ".join(f"{t.occurred_at:%m-%d}/{t.channel.value}/{t.source}" for t in st) or "none"))
            pre = [t for t in st if t.occurred_at and t.occurred_at <= sold]

            # 2) live Gmail hunt (email + phone variants)
            terms = []
            if em:
                terms.append(f'"{em}"')
            for p in (c.get("phone1"), c.get("phone2")):
                d = "".join(ch for ch in (p or "") if ch.isdigit())[-10:]
                if len(d) == 10:
                    terms += [f'"{d}"', f'"{d[:3]}-{d[3:6]}-{d[6:]}"', f'"{d[:3]}{d[3:6]}{d[6:]}"']
            gmail_hits = []
            if terms:
                q = "(" + " OR ".join(terms) + ") newer_than:180d"
                for mid, raw in gm.fetch_raw_messages(q, max_results=8):
                    msg = emaillib.message_from_bytes(raw)
                    res = build_touch_from_email(raw, rules, sm, raw_ref=mid)
                    gmail_hits.append((msg.get("From", "")[:34], (msg.get("Subject", "") or "")[:34],
                                       res.ok, (res.touch.source if res.touch else None)))
            print(f"  LIVE GMAIL ({len(gmail_hits)}):")
            for frm, sub, ok, src in gmail_hits:
                print(f"      {frm:34} | {sub:34} | matched={ok} src={src}")

            # 3) live Genesys hunt (inbound caller on either phone, Jan->now)
            gcalls = []
            for ph in (c.get("phone1"), c.get("phone2")):
                d = "".join(ch for ch in (ph or "") if ch.isdigit())[-10:]
                if len(d) != 10:
                    continue
                cur = datetime(2026, 1, 1, tzinfo=timezone.utc); end = datetime.now(timezone.utc)
                while cur < end:
                    nxt = min(cur + timedelta(days=30), end)
                    body = {"interval": f"{cur:%Y-%m-%dT%H:%M:%SZ}/{nxt:%Y-%m-%dT%H:%M:%SZ}", "order": "asc",
                            "segmentFilters": [{"type": "and", "predicates": [{"dimension": "ani", "value": "tel:+1" + d}]}],
                            "paging": {"pageSize": 25, "pageNumber": 1}}
                    for conv in gx._request("POST", "/api/v2/analytics/conversations/details/query", json=body).get("conversations", []):
                        gcalls.append(conv)
                    cur = nxt
            print(f"  LIVE GENESYS inbound calls ({len(gcalls)}):")
            for conv in gcalls:
                a, dn = extract_ani_dnis(conv)
                src = sm.source_for_dnis(dn)
                qs = {seg.get("queueId") for p in conv.get("participants", []) for ss in p.get("sessions", []) for seg in ss.get("segments", []) if seg.get("queueId")}
                qnames = [QN.get(q, q[:8]) for q in qs]
                scoped = "in-scope" if (qs & in_scope) else "OUT-OF-SCOPE"
                print(f"      {conv.get('conversationStart','')[:16]} DNIS={dn} -> src={src} queues={qnames} [{scoped}]")

            # verdict
            v = "GENUINE no-evidence"
            if pre:
                v = "!!! ENGINE ERROR — store had a pre-sale touch but sub is blank"
            elif any(g[2] for g in gmail_hits):
                v = "GAP — a Gmail lead matched a source but isn't in store (re-ingest/check)"
            elif gmail_hits:
                v = "GAP? — Gmail lead email exists but matched NO provider (unmapped domain/sender)"
            else:
                # genesys: pre-sale inbound call?
                for conv in gcalls:
                    a, dn = extract_ani_dnis(conv)
                    when = conv.get("conversationStart", "")[:19]
                    qs = {seg.get("queueId") for p in conv.get("participants", []) for ss in p.get("sessions", []) for seg in ss.get("segments", []) if seg.get("queueId")}
                    if conv.get("originatingDirection") == "inbound" and when and when <= f"{sold:%Y-%m-%dT%H:%M:%S}":
                        src = sm.source_for_dnis(dn)
                        if not src:
                            v = f"GAP — inbound pre-sale call on UNMAPPED DNIS {dn} (add to sheet)"
                        elif not (qs & in_scope):
                            v = f"GAP — inbound pre-sale call in OUT-OF-SCOPE queue (src would be {src})"
                        else:
                            v = f"CHECK — pre-sale call src={src} in-scope but not sourced"
                        break
            verdicts[cid] = v
            print(f"  >>> VERDICT: {v}")

    print("\n" + "=" * 78 + "\nSUMMARY")
    for cid, v in verdicts.items():
        print(f"  {cid}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
