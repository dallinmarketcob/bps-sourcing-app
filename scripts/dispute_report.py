"""Duplicate-lead dispute report for pay-per-lead providers.

For each lead from the named providers received in the report window, find any
PRIOR touch (any source) with the SAME phone within the preceding 30 days -- i.e.
a lead we'd already received and shouldn't be charged for again. Writes a CSV you
can hand back to the provider.

Usage: python scripts/dispute_report.py [days_back=7] [lookback=30]
"""
import csv
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.display import friendly_source  # noqa: E402

_SETTINGS = load_settings()
SHEET = _SETTINGS.master_sheet
# Pay-per-lead providers to dispute, read from config (PAY_PER_LEAD_PROVIDERS).
# Display name = the configured name; keyword = its lowercase, substring-matched
# against the sheet's Provider/Channel column to collect their Source N labels.
PROVIDERS = {
    name: name.lower()
    for name in (p.strip() for p in _SETTINGS.pay_per_lead_providers.split(",") if p.strip())
}


def provider_sources():
    src2prov, prov2srcs = {}, defaultdict(set)
    with open(SHEET, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            s = (r.get("Pestroutes Source") or "").strip()
            p = (r.get("Provider / Channel") or "").strip()
            if not s:
                continue
            src2prov[s] = p
            for name, kw in PROVIDERS.items():
                if kw in p.lower():
                    prov2srcs[name].add(s)
    return src2prov, prov2srcs


def main():
    days_back = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    lookback = int(sys.argv[2]) if len(sys.argv) > 2 else 30

    s = load_settings()
    if not PROVIDERS:
        sys.exit(
            "No pay-per-lead providers configured. Set PAY_PER_LEAD_PROVIDERS in "
            ".env (comma-separated provider names, e.g. Elocal,DoLead,Flow Bridge,ElectGen)."
        )
    src2prov, prov2srcs = provider_sources()
    src2name = {sn: name for name, srcs in prov2srcs.items() for sn in srcs}
    target_sources = set(src2name)
    print("providers -> sources:")
    for name, srcs in prov2srcs.items():
        print(f"  {name}: {sorted(srcs, key=lambda x:int(''.join(c for c in x if c.isdigit()) or 0))}")

    # Load all touches with a phone (we match on phone).
    conn = sqlite3.connect(str(s.db_path)); conn.row_factory = sqlite3.Row
    by_phone = defaultdict(list)
    for r in conn.execute(
        "SELECT source, channel, occurred_at, phone_e164 FROM touches "
        "WHERE phone_e164 IS NOT NULL AND occurred_at IS NOT NULL"
    ):
        by_phone[r["phone_e164"]].append((datetime.fromisoformat(r["occurred_at"]),
                                          r["source"], r["channel"]))
    for v in by_phone.values():
        v.sort()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now - timedelta(days=days_back)
    print(f"\nreport window: {start:%Y-%m-%d} .. {now:%Y-%m-%d} | lookback {lookback}d")

    disputes = []
    for phone, events in by_phone.items():
        for when, source, channel in events:
            if source not in target_sources or not (start <= when <= now):
                continue
            priors = [(w, sc, ch) for (w, sc, ch) in events
                      if w < when and (when - w) <= timedelta(days=lookback)]
            if priors:
                disputes.append((src2name[source], source, phone, when, priors))

    out = ROOT / "data" / f"disputes_{start:%Y%m%d}_{now:%Y%m%d}_{now:%H%M}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["provider", "lead_source", "phone", "lead_received",
                    "times_seen_prior_30d", "earliest_prior", "prior_history"])
        for prov, source, phone, when, priors in sorted(disputes, key=lambda d: d[0]):
            hist = "; ".join(
                f"{pw:%Y-%m-%d} {friendly_source(sc, ch, src2prov)}" for pw, sc, ch in priors
            )
            w.writerow([prov, source, phone, f"{when:%Y-%m-%d %H:%M}",
                        len(priors), f"{priors[0][0]:%Y-%m-%d}", hist])

    from collections import Counter
    tally = Counter(d[0] for d in disputes)
    total = {name: sum(1 for p, evs in by_phone.items() for w, sc, ch in evs
                       if sc in srcs and start <= w <= now)
             for name, srcs in prov2srcs.items()}
    print(f"\nwrote {len(disputes)} disputable leads -> {out}\n")
    print(f"{'provider':<14}{'leads (wk)':>11}{'disputable':>12}")
    for name in PROVIDERS:
        print(f"  {name:<12}{total.get(name,0):>11}{tally.get(name,0):>12}")


if __name__ == "__main__":
    main()
