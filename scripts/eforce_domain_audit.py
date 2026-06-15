"""Audit Gmail for Eforce Web form-lead domains we're NOT yet matching, and
propose a Source N for each by matching the city against the master sheet.

Eforce sends every city site from no-reply@multiscreensite.com; the per-city
WEBSITE DOMAIN is the From display name (e.g. www.brookspestsanbernardino.com).
There are many domain variants per city, so the alias table drifts behind.

Writes data/eforce_domain_proposal.csv (domain,count,proposed_source,city,
confidence,already_mapped). Confident rows = a clean city-name substring match
to a master-sheet "Eforce Web <City>". Review rows = non-brooks brands
(pestkiller*/sameday*) or ambiguous/abbreviated cities -> a human decides.

Usage: python scripts/eforce_domain_audit.py [days=120]
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.gmail import GmailReader  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

PROVIDERS = ROOT / "source_maps" / "email_providers.csv"
OUT = ROOT / "data" / "eforce_domain_proposal.csv"

# City abbreviations seen in domains -> canonical city token in the master sheet.
ALIASES = {"la": "losangeles", "sf": "sanfrancisco", "nla": "northla",
           "sla": "southla", "norange": "northorange", "sorange": "southorange",
           "soudiego": "sandiego"}
# Brands that are NOT the plain "brooks<city>" pattern -> always needs review.
REVIEW_PREFIXES = ("pestkiller", "sameday", "pestscontrol", "pestkillers")


import re


def clean(s: str) -> str:
    """City token from a master provider name, e.g. 'Eforce Web San Jose'->'sanjose'."""
    s = s.lower()
    for w in ("eforce", "web", "-", "_", " "):
        s = s.replace(w, "")
    return s


def light(domain: str) -> str:
    """Letters-only domain WITHOUT stripping pest/brooks, so city substrings
    survive the 'pest|s|anjose' boundary (sanjose stays matchable)."""
    return re.sub("[^a-z]", "", domain.lower().replace(".com", ""))


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    s = load_settings()
    sm = load_source_map_csv(s.master_sheet)
    # master-sheet Eforce city token -> Source N
    city_src: dict[str, str] = {}
    for src, prov in sm.source_to_provider.items():
        if prov and "eforce" in prov.lower():
            city_src[clean(prov)] = src
    # domains we already match
    known = {r[1].lower() for r in csv.reader(open(PROVIDERS))
             if r and not r[0].startswith("#") and r[0] in ("domain", "keyword")}

    gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
    froms = gm.list_senders(f"from:multiscreensite.com newer_than:{days}d", max_results=3000)
    dom = Counter()
    for f in froms:
        disp = f.split("<")[0].strip().strip('"').lower().replace("www.", "")
        if disp.endswith(".com"):
            dom[disp] += 1

    def resolve(domain: str) -> tuple[str, str, str]:
        ld = light(domain)
        # longest master city token that is a substring of the raw domain wins
        best = ""
        for city in city_src:
            if city and city in ld and len(city) > len(best):
                best = city
        if not best:  # fall back to abbreviation aliases (la->losangeles etc.)
            heavy = ld
            for w in ("brooks", "pestcontrol", "pests", "pest", "control",
                      "killer", "sameday"):
                heavy = heavy.replace(w, "")
            alias = ALIASES.get(heavy)
            if alias and alias in city_src:
                best = alias
        if not best:
            return "", "", "none"
        src = city_src[best]
        prov = sm.source_to_provider.get(src, "")
        # non-brooks brands (pestkiller*/sameday*) still need a human to confirm
        # they map to the same Source as the city's Eforce site.
        conf = "review" if domain.startswith(REVIEW_PREFIXES) else "confident"
        return src, prov, conf

    rows = []
    for d, n in dom.most_common():
        mapped = d in known
        src, prov, conf = resolve(d)
        if mapped:
            conf = "already_mapped"
        rows.append((d, n, src, prov, conf, mapped))

    with open(OUT, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["domain", "count", "proposed_source", "city", "confidence", "already_mapped"])
        w.writerows(rows)

    conf_rows = [r for r in rows if r[4] == "confident"]
    review_rows = [r for r in rows if r[4] in ("review", "none")]
    miss = sum(r[1] for r in rows if not r[5])
    print(f"multiscreensite emails/{days}d: {len(froms)} | distinct domains: {len(dom)} | "
          f"unmapped emails: {miss}")
    print(f"\nCONFIDENT auto-maps ({len(conf_rows)} domains): "
          f"{sum(r[1] for r in conf_rows)} emails")
    for d, n, src, prov, *_ in conf_rows:
        print(f"   {n:4}  {d:40} -> {src} ({prov})")
    print(f"\nNEEDS REVIEW ({len(review_rows)} domains): {sum(r[1] for r in review_rows)} emails")
    for d, n, src, prov, conf, _ in review_rows:
        print(f"   {n:4}  {d:40} -> {src or '?':10} {('('+prov+')') if prov else ''} [{conf}]")
    print(f"\nproposal -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
