"""One-off: reconcile a lead provider's invoiced-accounts spreadsheet against our
attribution rule, and (optionally) correct the source in PestRoutes.

The provider (ElectGen) sent a sheet of customers they claim to have sourced.
Many got overwritten by later duplicate leads (DoLead/MMG/Flow Bridge/Meta), so
their PestRoutes source is wrong. We run OUR rule (earliest touch of the most-
recent 7-day arrival; protected sources never overwritten) using each customer's
store touches PLUS the sheet's authoritative ElectGen + Brooks-FB timestamps,
then write a review CSV. Writes to PestRoutes only with --write.

Timezones: ElectGen lead times are MDT (America/Denver); Sale + Brooks-FB times
are PDT (America/Los_Angeles); PestRoutes dateAdded is LA local. All normalized
to naive UTC to match the touch store.

Usage:
    python scripts/electgen_reconcile.py <provider.csv>                 # dry-run review
    python scripts/electgen_reconcile.py <provider.csv> --write customer
    python scripts/electgen_reconcile.py <provider.csv> --write subscription
    python scripts/electgen_reconcile.py <provider.csv> --write both
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource import store  # noqa: E402
from leadsource.attribution import attribute_all  # noqa: E402
from leadsource.config import load_settings  # noqa: E402
from leadsource.models import AttributionStatus, Channel, Subscription, Touch  # noqa: E402
from leadsource.normalize import normalize_email, normalize_phone  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

MDT = ZoneInfo("America/Denver")
LA = ZoneInfo("America/Los_Angeles")
INVENTORY = ROOT / "data" / "pestroutes_source_inventory.json"
ELECTGEN_SOURCE = "Source 25"   # ElectGen
META_SOURCE = "Source 144"      # Brooks FB / Meta instant form


def parse_dt(s: str, tz: ZoneInfo) -> datetime | None:
    """Parse the sheet's ' May 29, 2026 at 1:16: PM' format -> naive UTC."""
    if not s:
        return None
    s = s.replace(" ", " ").replace(" ", " ").strip()
    if not s or s.upper() == "N/A":
        return None
    s = re.sub(r"\s+", " ", s).strip()  # collapses U+202F / U+00A0 to a space
    s = re.sub(r":\s*(AM|PM)", r" \1", s, flags=re.IGNORECASE)
    try:
        dt = datetime.strptime(s, "%B %d, %Y at %I:%M %p")
    except ValueError:
        return None
    return dt.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def parse_pr_dt(s: str | None) -> datetime | None:
    """PestRoutes 'YYYY-MM-DD HH:MM:SS' is LA local -> naive UTC."""
    if not s:
        return None
    try:
        dt = datetime.strptime(str(s).strip(), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=LA).astimezone(timezone.utc).replace(tzinfo=None)


def read_sheet(path: Path) -> list[dict]:
    """Parse the provider sheet rows (skips the colour legend + header)."""
    rows = list(csv.reader(open(path, encoding="utf-8-sig")))
    out, started = [], False
    for r in rows:
        if not started:
            if r and r[0].strip() == "Customer ID":
                started = True
            continue
        if not r or not r[0].strip():
            continue
        cell = (r + [""] * 10)[:10]
        cust_ids = [c.strip() for c in cell[0].split(",") if c.strip()]
        out.append({
            "customer_ids": cust_ids,
            "match_type": cell[1].strip(),
            "phone": re.sub(r"\D", "", cell[2]),
            "email": cell[3].strip().strip(","),
            "sale_dt": parse_dt(cell[4], LA),
            "fb_dt": parse_dt(cell[5], LA),
            "electgen_dt": parse_dt(cell[6], MDT),
            "sheet_source": cell[7].strip(),
            "note": cell[8].strip(),
        })
    return out


def main() -> int:
    args = [a for a in sys.argv[1:]]
    write_target = None
    if "--write" in args:
        i = args.index("--write")
        write_target = args[i + 1] if i + 1 < len(args) else "subscription"
        del args[i:i + 2]
    if not args:
        print("Usage: python scripts/electgen_reconcile.py <provider.csv> [--write customer|subscription|both]")
        return 1
    sheet_path = Path(args[0])

    s = load_settings()
    inv = json.loads(INVENTORY.read_text())                 # {label: sourceID}
    id_to_label = {v: k for k, v in inv.items()}
    src2prov = load_source_map_csv(s.master_sheet).source_to_provider

    def prov(label: str | None) -> str:
        if not label:
            return ""
        p = src2prov.get(label)
        return f"{label} ({p})" if p else label

    rows = read_sheet(sheet_path)
    # unique customer -> the sheet row it came from (first wins)
    cust_row: dict[str, dict] = {}
    for r in rows:
        for cid in r["customer_ids"]:
            cust_row.setdefault(cid, r)
    cust_ids = list(cust_row)
    print(f"sheet: {len(rows)} rows -> {len(cust_ids)} unique customers")

    conn = store.connect(s.db_path)
    store_touches = store.load_touches(conn, since=datetime(2026, 1, 1, tzinfo=timezone.utc))

    with client_from_settings(s) as pr:
        customers = {str(c["customerID"]): c
                     for c in (pr.get_customers(cust_ids).get("customers") or [])}
        # gather all subscription ids referenced by these customers
        sub_ids: list[str] = []
        cust_subs: dict[str, list[str]] = {}
        for cid, c in customers.items():
            ids = [x for x in str(c.get("subscriptionIDs") or "").split(",") if x.strip()]
            cust_subs[cid] = ids
            sub_ids += ids
        sub_recs = {str(x["subscriptionID"]): x
                    for x in (pr.get_subscriptions(sub_ids).get("subscriptions") or [])} if sub_ids else {}

        # Build Subscription objects + the touch pool (store + injected sheet times).
        subs: list[Subscription] = []
        meta: dict[str, dict] = {}            # subscription_id -> context for the report
        touches = list(store_touches)
        for cid in cust_ids:
            c = customers.get(cid)
            if not c:
                print(f"  (!) customer {cid} not found in PestRoutes; skipping")
                continue
            row = cust_row[cid]
            p1 = normalize_phone(c.get("phone1")) or normalize_phone(row["phone"])
            p2 = normalize_phone(c.get("phone2"))
            email = normalize_email(c.get("email")) or normalize_email(row["email"])
            # Read the source LABEL straight off the record (handles duplicate
            # sourceIDs that share a label, e.g. two "Source 54"s).
            cust_src = (c.get("source") or None)

            # Inject the sheet's authoritative ElectGen + FB touches for this contact.
            if row["electgen_dt"]:
                touches.append(Touch(Channel.GMAIL, ELECTGEN_SOURCE, row["electgen_dt"],
                                     phone_e164=p1, email=email, raw_ref=f"sheet:eg:{cid}"))
            if row["fb_dt"]:
                touches.append(Touch(Channel.META, META_SOURCE, row["fb_dt"],
                                     phone_e164=p1, email=email, raw_ref=f"sheet:fb:{cid}"))

            for sid in (cust_subs.get(cid) or [None]):
                rec = sub_recs.get(str(sid)) if sid else None
                sold_at = parse_pr_dt(rec.get("dateAdded")) if rec else None
                sold_at = sold_at or row["sale_dt"] or datetime.utcnow()
                sub_src = (rec.get("source") or None) if rec else None
                # We write the SUBSCRIPTION source, so the decision (change / fill /
                # protected) is driven by the subscription's current source.
                subs.append(Subscription(
                    subscription_id=str(sid) if sid else f"cust{cid}",
                    customer_id=cid, sold_at=sold_at,
                    phone1_e164=p1, phone2_e164=p2, email=email,
                    current_source=sub_src,
                ))
                meta[str(sid) if sid else f"cust{cid}"] = {
                    "cid": cid, "name": f"{c.get('fname','')} {c.get('lname','')}".strip(),
                    "phone": p1, "email": email, "sub_src": sub_src, "cust_src": cust_src,
                    "row": row,
                }

        results = attribute_all(
            subs, touches, stale_window_days=s.stale_window_days,
            same_day_cluster_hours=s.same_day_cluster_hours,
            protected_sources=s.protected_source_set,
            meta_source=s.meta_lead_source, website_form_source=s.website_form_source,
            meta_form_tiebreak_minutes=s.meta_form_tiebreak_minutes,
        )

        # ---- review CSV ----
        out = ROOT / "data" / f"electgen_reconcile_{datetime.now(timezone.utc):%Y%m%d}.csv"
        # If it's open in Excel (locked), fall back to a numbered name.
        n = 2
        while True:
            try:
                open(out, "a").close()
                break
            except PermissionError:
                out = ROOT / "data" / f"electgen_reconcile_{datetime.now(timezone.utc):%Y%m%d}_v{n}.csv"
                n += 1
        from collections import Counter
        tally: Counter = Counter()
        plan: list[tuple] = []
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["customer_id", "subscription_id", "name", "phone", "sale_date",
                        "current_customer_source", "current_sub_source", "engine_source",
                        "decision", "is_electgen", "matched_on", "winning_evidence",
                        "arrivals", "electgen_lead(MDT->local)", "fb_lead", "reason", "sheet_note"])
            for r in results:
                m = meta[r.subscription_id]
                row = m["row"]
                wt = r.winning_touch
                eng = r.attributed_source
                if r.status is AttributionStatus.NO_TOUCHES:
                    decision = "UNSOURCED"
                elif r.protected:
                    decision = "PROTECTED (kept)"
                elif not r.is_change:
                    decision = "AGREE"
                elif not m["sub_src"]:
                    decision = "FILL (was blank)"
                else:
                    decision = "CHANGE"
                tally[decision] += 1
                is_eg = "YES" if eng == ELECTGEN_SOURCE else ""
                w.writerow([
                    m["cid"], r.subscription_id, m["name"], m["phone"],
                    m["row"]["sale_dt"].strftime("%Y-%m-%d %H:%M") if row["sale_dt"] else "",
                    prov(m["cust_src"]), prov(m["sub_src"]), prov(eng),
                    decision, is_eg, r.matched_key.value,
                    f"{wt.channel.value}/{wt.source} @ {wt.occurred_at:%Y-%m-%d %H:%M}" if wt else "",
                    len(r.streak) and "", row["electgen_dt"].strftime("%Y-%m-%d %H:%M") if row["electgen_dt"] else "",
                    row["fb_dt"].strftime("%Y-%m-%d %H:%M") if row["fb_dt"] else "",
                    r.reason, row["note"],
                ])
                if r.needs_write and eng:
                    plan.append((m["cid"], r.subscription_id, m["cust_src"], m["sub_src"], eng))

        print(f"\nreview -> {out}")
        print("decisions:", dict(tally))
        print(f"would change {len(plan)} accounts; of those, "
              f"{sum(1 for *_ , e in plan if e == ELECTGEN_SOURCE)} -> ElectGen (Source 25), "
              f"{sum(1 for *_ , e in plan if e != ELECTGEN_SOURCE)} -> other source")

        if not write_target:
            print("\nDRY-RUN: no writes. Re-run with --write customer|subscription|both to apply.")
            return 0

        # ---- writes (with read-back verification) ----
        print(f"\nWRITING source to PestRoutes (target={write_target}) for {len(plan)} accounts...")
        ran_at = datetime.now(timezone.utc).isoformat()

        def sub_source_id(sub_id):
            recs = pr.get_subscriptions([sub_id]).get("subscriptions") or []
            return str(recs[0].get("sourceID")) if recs else None

        ok = fail = 0
        for cid, sid, cur_cust, cur_sub, eng in plan:
            new_id = inv.get(eng)
            if not new_id:
                print(f"  {cid}/{sid}: no sourceID for {eng}; SKIP")
                continue
            if write_target in ("subscription", "both") and not str(sid).startswith("cust"):
                # PestRoutes scopes writes to the sub's office; must pass officeID.
                office_id = str((sub_recs.get(str(sid)) or {}).get("officeID") or "")
                pr.update_subscription(sid, {"sourceID": new_id, "officeID": office_id})
                got = sub_source_id(sid)
                verified = (got == str(new_id))
                ok, fail = (ok + 1, fail) if verified else (ok, fail + 1)
                store.record_write(conn, ran_at, {
                    "subscription_id": sid, "customer_name": meta[sid]["name"],
                    "old_source": cur_sub, "old_source_id": inv.get(cur_sub or ""),
                    "new_source": eng, "new_source_id": new_id,
                    "decision": "RECONCILE-SUB",
                    "status": "WRITTEN" if verified else "WRITE_FAILED", "dry_run": False})
                print(f"  {'OK ' if verified else 'FAIL'} {cid}/sub {sid}: "
                      f"{cur_sub or '(blank)'} -> {eng} ({new_id})"
                      + ("" if verified else f"  read-back={got}"))
            if write_target in ("customer", "both"):
                pr.request("customer", "update", {"customerID": cid, "sourceID": new_id})
                store.record_write(conn, ran_at, {
                    "subscription_id": f"cust{cid}", "customer_name": meta[sid]["name"],
                    "old_source": cur_cust, "old_source_id": inv.get(cur_cust or ""),
                    "new_source": eng, "new_source_id": new_id,
                    "decision": "RECONCILE-CUST", "status": "WRITTEN", "dry_run": False})
        print(f"done. verified OK={ok}  failed={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
