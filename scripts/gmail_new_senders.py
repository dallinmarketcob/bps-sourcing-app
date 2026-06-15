"""Detect lead senders we're NOT yet pulling.

Since we ingest Gmail by sender (for completeness on a busy inbox), the risk is
missing a NEW provider. This scans recent inbox senders and lists the high-volume
domains that are NOT already in gmail_lead_senders -- candidates to add. Run it
periodically (e.g. weekly).

Usage: python scripts/gmail_new_senders.py [days=7] [max=1500]
"""
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.gmail import GmailReader  # noqa: E402

_DOMAIN = re.compile(r"[\w.+-]+@([\w.-]+)")
# Obvious non-lead noise to hide from the candidate list.
NOISE = {
    "google.com", "googlemail.com", "calendar.google.com", "docs.google.com",
    "nextdoor.com", "is.email.nextdoor.com", "mail.nextdoor.com",
    "facebookmail.com", "linkedin.com", "intuit.com", "quickbooks.com",
    "amazon.com", "amazonses.com", "slack.com", "zoom.us", "microsoft.com",
}


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    max_n = int(sys.argv[2]) if len(sys.argv) > 2 else 1500

    s = load_settings()
    known = {x.strip().lower() for x in s.gmail_lead_senders.split(",") if x.strip()}
    # Reduce known full addresses to their domains too.
    known_domains = {k.split("@")[-1] for k in known}

    gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
    print(f"scanning up to {max_n} inbox messages from the last {days} days...")
    froms = gm.list_senders(f"newer_than:{days}d", max_results=max_n)

    counts: Counter = Counter()
    for f in froms:
        m = _DOMAIN.search(f or "")
        if m:
            counts[m.group(1).lower()] += 1

    print(f"\nscanned {len(froms)} messages, {len(counts)} distinct sender domains")
    print("\nHigh-volume senders NOT in our lead list (review for new providers):")
    shown = 0
    for dom, n in counts.most_common():
        if n < 3:
            break
        if dom in known_domains or any(dom.endswith("." + k) or dom == k for k in known_domains):
            continue
        if dom in NOISE:
            continue
        print(f"  {n:>4}  {dom}")
        shown += 1
        if shown >= 30:
            break
    if not shown:
        print("  (none — every busy sender is already covered)")


if __name__ == "__main__":
    main()
