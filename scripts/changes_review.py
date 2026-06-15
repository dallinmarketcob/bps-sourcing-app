"""Export the CHANGE (disagree) writes from a given day as a review CSV, enriched
with the evidence/reason from a resource_range report (which shows them as AGREE
post-write). The OLD->NEW source comes from the `writes` audit table.

Usage:  python scripts/changes_review.py <YYYY-MM-DD> <resource_range_csv>
"""
import csv
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.display import friendly_source  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402


def main() -> int:
    day = sys.argv[1]
    rcsv = Path(sys.argv[2])
    s = load_settings()
    src2prov = load_source_map_csv(s.master_sheet).source_to_provider
    conn = sqlite3.connect(str(s.db_path))
    rows = conn.execute(
        "SELECT subscription_id, customer_name, old_source, new_source FROM writes "
        "WHERE ran_at LIKE ? AND dry_run=0 AND decision='CHANGE' ORDER BY old_source",
        (day + "%",)).fetchall()
    evid = {r["subscription_id"]: r for r in csv.DictReader(open(rcsv, encoding="utf-8-sig"))}

    out = ROOT / "data" / f"changes_review_{day}.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["subscription_id", "customer", "sold_at", "OLD_source", "NEW_source",
                    "matched_on", "evidence", "evidence_date", "reason"])
        for sid, name, old, new in rows:
            e = evid.get(sid, {})
            w.writerow([sid, name, e.get("sold_at", ""),
                        friendly_source(old, None, src2prov) if old else "(blank)",
                        friendly_source(new, None, src2prov),
                        e.get("matched_on", ""), e.get("evidence", ""),
                        e.get("evidence_date", ""), e.get("reason", "")])
    print(f"{len(rows)} CHANGE rows -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
