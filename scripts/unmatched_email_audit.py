"""Audit EVERY lead email we fetch and report any that don't resolve to a valid
source. Two failure buckets:

  NO-PROVIDER  -> a real gap: we got a phone/email but matched no source rule.
                 These are leads we're dropping; every one must be mapped.
  NO-CONTACT   -> expected non-touch: masked LSA calls, report/digest emails with
                 no phone/email to attribute. Listed so we can confirm they're noise.

Writes data/unmatched_email_audit.csv. Usage: python scripts/unmatched_email_audit.py [days=90]
"""
from __future__ import annotations

import csv
import email
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource import pipeline  # noqa: E402
from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.gmail import GmailReader, build_touch_from_email  # noqa: E402

OUT = ROOT / "data" / "unmatched_email_audit.csv"


def sender_of(frm: str) -> str:
    return (frm.split("<")[-1].strip(" >") if "<" in frm else frm).split("@")[-1].lower()


def display_of(frm: str) -> str:
    return frm.split("<")[0].strip().strip('"').lower()


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    s = load_settings()
    source_map, rules = pipeline.load_maps(s)
    gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
    q = f"{s.gmail_lead_query_prefix} newer_than:{days}d".strip()
    print(f"auditing lead emails: {q}")

    matched = 0
    no_provider = []   # rows: sender, display, subject
    no_contact = Counter()
    for mid, raw in gm.fetch_raw_messages(q, max_results=10000):
        res = build_touch_from_email(raw, rules, source_map, raw_ref=mid)
        if res.ok:
            matched += 1
            continue
        msg = email.message_from_bytes(raw)
        frm = msg.get("From", "")
        subj = (msg.get("Subject", "") or "").strip()[:60]
        reason = (getattr(res, "reason", "") or "").lower()
        # A real gap = provider/source genuinely UNidentified. If a rule matched
        # but no touch formed (e.g. "keyword X -> Source 145" with no phone in the
        # email, like LSA call notices), that's a no-contact, not an unmapped source.
        unmapped = any(k in reason for k in
                       ("no provider", "no source", "unidentified", "needs review"))
        if unmapped:
            no_provider.append((sender_of(frm), display_of(frm), subj))
        else:
            no_contact[sender_of(frm)] += 1

    # group NO-PROVIDER by sender; for multiscreensite, by the site domain (display)
    grp = defaultdict(Counter)
    for sender, disp, subj in no_provider:
        key = disp if sender == "multiscreensite.com" and disp.endswith(".com") else sender
        grp[sender][key] += 1

    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["bucket", "sender", "key", "subject_sample", "count"])
        seen = {}
        for sender, disp, subj in no_provider:
            key = disp if sender == "multiscreensite.com" and disp.endswith(".com") else sender
            if (sender, key) not in seen:
                seen[(sender, key)] = subj
        for sender, keys in grp.items():
            for key, n in keys.most_common():
                w.writerow(["NO-PROVIDER", sender, key, seen.get((sender, key), ""), n])
        for sender, n in no_contact.most_common():
            w.writerow(["NO-CONTACT", sender, "", "", n])

    total_np = sum(len(v) for v in [no_provider])
    print(f"\nmatched OK: {matched}")
    print(f"NO-PROVIDER (real gap -> must map): {total_np} emails")
    for sender, keys in sorted(grp.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  [{sender}] {sum(keys.values())} emails:")
        for key, n in keys.most_common():
            print(f"      {n:4}  {key}")
    print(f"\nNO-CONTACT (expected noise / masked): {sum(no_contact.values())} emails")
    for sender, n in no_contact.most_common(20):
        print(f"      {n:4}  {sender}")
    print(f"\ndetail -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
