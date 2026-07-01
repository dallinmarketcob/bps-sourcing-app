"""Weekly duplicate-dispute reports, one email per provider group.

For each provider group (source_maps/dispute_groups.csv), find this week's leads
whose phone/email already appeared under a DIFFERENT source within the prior 7
days, and email a per-group CSV (Name, Phone, Email, Pest details, when it came
in, and a timestamped list of the prior touches as proof) via Resend.

Grouping rule: the CSV maps each lead Source -> a report group (e.g. all of a
website network's city sites -> one "Eforce" group). Owned / no-dispute sources
(Website Forms, referrals, Yelp, ...) are deliberately NOT report groups, but
their touches still COUNT as prior evidence against every other group. See
docs/DISPUTE_REPORTS.md for how to build the grouping file.

Duplicate rule: a group lead this week is a duplicate if the same phone (either
number) or email was seen in a DIFFERENT source within the 7 days before that
lead (same-provider repeats are excluded — providers dedupe their own).

Config (.env): RESEND_API_KEY, RESEND_FROM, RESEND_TO, COMPANY_NAME.

Usage:
  python scripts/dispute_reports.py --week                # previous full Sun-Sat week, send (what cron runs)
  python scripts/dispute_reports.py --days 7
  python scripts/dispute_reports.py --start 2026-06-09 --end 2026-06-16
  python scripts/dispute_reports.py --dry-run             # build CSVs, do NOT email
  python scripts/dispute_reports.py --to you@x.com        # override recipients (test)
  python scripts/dispute_reports.py --only Eforce,DoLead  # limit to some groups
"""
from __future__ import annotations

import argparse
import base64
import csv
import email as emaillib
import io
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import httpx  # noqa: E402

from leadsource.config import load_settings  # noqa: E402
from leadsource import store  # noqa: E402
from leadsource.pipeline import load_maps  # noqa: E402
from leadsource.normalize import normalize_email, normalize_phone  # noqa: E402
from leadsource.readers.gmail import GmailReader, build_touch_from_email  # noqa: E402
from leadsource.readers.meta import MetaClient, _field  # noqa: E402
from form_leads_detail_export import body_text, extract_name, extract_pest, STD  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
GROUPS_CSV = ROOT / "source_maps" / "dispute_groups.csv"
PRIOR_WINDOW_DAYS = 7


def load_groups() -> tuple[dict[str, str], dict[str, list[str]]]:
    """Return (source -> group) and (group -> [sources])."""
    if not GROUPS_CSV.exists():
        sys.exit(
            f"Missing {GROUPS_CSV}. Create it (source,provider,group) — copy "
            "source_maps/example_dispute_groups.csv and fill in your sources. "
            "See docs/DISPUTE_REPORTS.md."
        )
    src2grp: dict[str, str] = {}
    grp2srcs: dict[str, list[str]] = defaultdict(list)
    with open(GROUPS_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            src2grp[row["source"]] = row["group"]
            grp2srcs[row["group"]].append(row["source"])
    return src2grp, dict(grp2srcs)


def fmt_pt(dt: datetime | None) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PT).strftime("%Y-%m-%d %I:%M %p")


def build_name_pest_maps(s, start_pt: datetime, end_pt: datetime):
    """Re-parse the window's FORM leads (Gmail + Meta) to recover Name + Pest,
    keyed by phone and by email (so we can attach them to any flagged lead)."""
    source_map, rules = load_maps(s)
    by_phone: dict[str, tuple[str, str]] = {}
    by_email: dict[str, tuple[str, str]] = {}

    def put(phone, email, name, pest):
        if not name and not pest:
            return
        if phone and (phone not in by_phone or (name and not by_phone[phone][0])):
            by_phone[phone] = (name, pest)
        if email and (email not in by_email or (name and not by_email[email][0])):
            by_email[email] = (name, pest)

    # --- Gmail ---
    try:
        gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
        q = f"{s.gmail_lead_query_prefix} after:{start_pt:%Y/%m/%d} before:{end_pt:%Y/%m/%d}".strip()
        for mid, raw in gm.fetch_raw_messages(q, max_results=4000):
            res = build_touch_from_email(raw, rules, source_map, raw_ref=mid)
            if not res.ok or not res.touch:
                continue
            msg = emaillib.message_from_bytes(raw)
            body = body_text(msg)
            put(res.touch.phone_e164, res.touch.email,
                extract_name(body, msg.get("From", "")), extract_pest(body))
    except Exception as e:  # parsing/enrichment is best-effort
        print("  [warn] gmail enrich:", str(e)[:140])

    # --- Meta (loop every configured page — supports multi-page/DBA brands) ---
    start_utc = start_pt.astimezone(timezone.utc)
    end_utc = end_pt.astimezone(timezone.utc)
    for pc in s.meta_page_configs:
        try:
            with MetaClient(pc["token"]) as mc:
                pid = pc["page_id"]
                page_token = mc.get(pid, {"fields": "access_token"}).get("access_token")
                for fm in mc.paged(f"{pid}/leadgen_forms", {"fields": "id,leads_count"}, token=page_token):
                    if not fm.get("leads_count"):
                        continue
                    for lead in mc.get_leads(fm["id"], page_token, since_unix=int(start_utc.timestamp())):
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
                        put(normalize_phone(_field(lead, "phone_number", "phone")),
                            normalize_email(_field(lead, "email")), name or "", pest[:200])
        except Exception as e:
            print(f"  [warn] meta enrich (page {pc['page_id']}):", str(e)[:140])

    return by_phone, by_email


def find_duplicates(group_sources, all_touches, start_utc, end_utc, by_phone_idx, by_email_idx):
    """For one group, return a list of flagged-duplicate identity dicts."""
    gset = set(group_sources)
    week = [t for t in all_touches
            if t.source in gset and t.occurred_at and start_utc <= t.occurred_at < end_utc]
    # group this week's group-touches into identities (phone preferred, else email)
    identities: dict[str, list] = defaultdict(list)
    for t in week:
        key = t.phone_e164 or t.email
        if key:
            identities[key].append(t)

    rows = []
    for key, members in identities.items():
        phones = {t.phone_e164 for t in members if t.phone_e164}
        email = next((t.email for t in members if t.email), None)
        anchor = max(t.occurred_at for t in members)
        floor = anchor - timedelta(days=PRIOR_WINDOW_DAYS)
        # gather candidate prior touches (any source/channel) sharing phone or email
        cand: dict[int, object] = {}
        for p in phones:
            for t in by_phone_idx.get(p, []):
                cand[id(t)] = t
        if email:
            for t in by_email_idx.get(email, []):
                cand[id(t)] = t
        # Cross-provider only: a prior touch from THIS group's own sources is a
        # same-provider repeat (providers already dedupe those), so ignore it.
        # We only report a lead we'd already received from a DIFFERENT source.
        priors = [t for t in cand.values()
                  if t.occurred_at and floor <= t.occurred_at < anchor and t.source not in gset]
        if not priors:
            continue
        priors.sort(key=lambda t: t.occurred_at)
        rows.append({
            "phones": sorted(phones), "email": email or "",
            "anchor": anchor, "week_count": len(members),
            "week_first": min(t.occurred_at for t in members),
            "priors": priors,
        })
    rows.sort(key=lambda r: r["anchor"], reverse=True)
    return rows


def rows_to_csv(group, rows, s2p, name_phone, name_email) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Name", "Phone", "Email", "Pest Details", "Lead Received (PT)",
                f"Times from {group} this week", f"Prior Touches ({PRIOR_WINDOW_DAYS}d)",
                "Prior Touch Detail (proof)"])
    for r in rows:
        phone = r["phones"][0] if r["phones"] else ""
        # name/pest from the re-parsed form maps (by any phone, then email)
        name = pest = ""
        for p in r["phones"]:
            if p in name_phone:
                name, pest = name_phone[p]
                break
        if not name and r["email"] and r["email"] in name_email:
            name, pest = name_email[r["email"]]
        proof = []
        for t in r["priors"][:10]:
            prov = s2p.get(t.source, t.source)
            proof.append(f"{fmt_pt(t.occurred_at)} | {t.channel.value} | {prov}")
        if len(r["priors"]) > 10:
            proof.append(f"(+{len(r['priors']) - 10} more)")
        w.writerow([name, phone, r["email"], pest, fmt_pt(r["anchor"]),
                    r["week_count"], len(r["priors"]), " ;  ".join(proof)])
    return buf.getvalue().encode("utf-8-sig")


def email_html(s, group, rows, start_pt, end_pt) -> str:
    n = len(rows)
    signature = s.company_name or "Marketing"
    return (
        f"<p>Hi,</p>"
        f"<p>Attached is the weekly <b>{group}</b> duplicate-lead report for "
        f"<b>{start_pt:%b %d}</b>&ndash;<b>{end_pt:%b %d, %Y}</b>.</p>"
        f"<p>We received <b>{n}</b> lead{'s' if n != 1 else ''} from {group} this week "
        f"whose phone or email had already reached us within the prior {PRIOR_WINDOW_DAYS} days. "
        f"The attached CSV lists each one with the timestamped prior touches as proof.</p>"
        f"<p>&mdash; {signature}</p>"
    )


def send_via_resend(s, to_list, subject, html, filename, content_bytes) -> tuple[bool, str]:
    payload = {
        "from": s.resend_from,
        "to": to_list,
        "subject": subject,
        "html": html,
        "attachments": [{
            "filename": filename,
            "content": base64.b64encode(content_bytes).decode("ascii"),
        }],
    }
    try:
        resp = httpx.post("https://api.resend.com/emails",
                          headers={"Authorization": f"Bearer {s.resend_api_key}"},
                          json=payload, timeout=30)
        if resp.status_code in (200, 201):
            return True, resp.json().get("id", "ok")
        return False, f"{resp.status_code} {resp.text[:160]}"
    except Exception as e:
        return False, str(e)[:160]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", action="store_true",
                    help="previous full Sun-Sat calendar week (what cron uses); overrides --days")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--to", help="comma-separated recipient override (testing)")
    ap.add_argument("--only", help="comma-separated group names to limit to")
    args = ap.parse_args()

    s = load_settings()
    s2p = load_maps(s)[0].source_to_provider
    src2grp, grp2srcs = load_groups()

    # window (Pacific calendar days; end is EXCLUSIVE)
    today0 = datetime.now(PT).replace(hour=0, minute=0, second=0, microsecond=0)
    if args.start or args.end:
        end_pt = datetime.fromisoformat(args.end).replace(tzinfo=PT) if args.end else today0
        start_pt = (datetime.fromisoformat(args.start).replace(tzinfo=PT) if args.start
                    else end_pt - timedelta(days=args.days))
    elif args.week:
        # most recent Sunday on/before today = start of the CURRENT week; the
        # previous full week is the 7 days before that (Sun 00:00 .. next Sun 00:00).
        days_since_sun = (today0.weekday() + 1) % 7  # Mon=0..Sun=6 -> 1..0
        end_pt = today0 - timedelta(days=days_since_sun)
        start_pt = end_pt - timedelta(days=7)
    else:
        end_pt = today0
        start_pt = end_pt - timedelta(days=args.days)
    start_utc, end_utc = start_pt.astimezone(timezone.utc), end_pt.astimezone(timezone.utc)
    # touches in the store carry naive-UTC timestamps; compare against naive bounds
    start_naive, end_naive = start_utc.replace(tzinfo=None), end_utc.replace(tzinfo=None)

    only = {g.strip() for g in args.only.split(",")} if args.only else None
    to_list = ([a.strip() for a in args.to.split(",")] if args.to else s.resend_to_list)

    if not args.dry_run and not to_list:
        sys.exit("No recipients: set RESEND_TO in .env or pass --to (or use --dry-run).")

    print(f"window: {start_pt:%Y-%m-%d} -> {end_pt:%Y-%m-%d} PT | prior {PRIOR_WINDOW_DAYS}d | "
          f"{'DRY-RUN' if args.dry_run else 'SEND -> ' + ','.join(to_list)}")

    # load touches deep enough to see the prior window before the window start
    conn = store.connect(s.db_path)
    load_since = start_utc - timedelta(days=PRIOR_WINDOW_DAYS + 1)
    all_touches = store.load_touches(conn, since=load_since)
    by_phone_idx, by_email_idx = defaultdict(list), defaultdict(list)
    for t in all_touches:
        if t.phone_e164:
            by_phone_idx[t.phone_e164].append(t)
        if t.email:
            by_email_idx[t.email].append(t)
    print(f"loaded {len(all_touches)} touches since {load_since:%Y-%m-%d}")

    # name/pest only needed for the current week's flagged leads -> parse 7d of forms
    name_phone, name_email = build_name_pest_maps(s, start_pt, end_pt)
    print(f"form enrichment: {len(name_phone)} phones, {len(name_email)} emails")

    outdir = ROOT / "data" / "dispute_reports"
    outdir.mkdir(parents=True, exist_ok=True)
    summary = []
    for group in sorted(grp2srcs):
        if only and group not in only:
            continue
        rows = find_duplicates(grp2srcs[group], all_touches, start_naive, end_naive,
                               by_phone_idx, by_email_idx)
        if not rows:
            summary.append((group, 0, "no duplicates"))
            continue
        content = rows_to_csv(group, rows, s2p, name_phone, name_email)
        fname = f"{group.replace(' ', '_')}_dupes_{start_pt:%Y%m%d}_{end_pt:%Y%m%d}.csv"
        (outdir / fname).write_bytes(content)
        subject = f"[Duplicate Leads] {group} — {start_pt:%b %d}–{end_pt:%b %d} ({len(rows)})"
        if args.dry_run:
            summary.append((group, len(rows), f"csv -> {fname}"))
        else:
            ok, info = send_via_resend(s, to_list, subject,
                                       email_html(s, group, rows, start_pt, end_pt),
                                       fname, content)
            summary.append((group, len(rows), f"SENT {info}" if ok else f"FAILED {info}"))

    print("\n" + "=" * 78 + "\nSUMMARY")
    total = 0
    for group, n, note in summary:
        total += n
        flag = "" if n == 0 else "  <=="
        print(f"  {group:20} {n:4} dup leads   {note}{flag}")
    print(f"\n  {total} duplicate leads across {sum(1 for _, n, _ in summary if n)} groups")
    store.record_run(conn, datetime.now(timezone.utc).isoformat(), "dispute_reports",
                     f"{start_pt:%Y-%m-%d}..{end_pt:%Y-%m-%d} total={total} dry={args.dry_run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
