"""Pull the COMPLETE source picklist from the customerSource entity (label + ID),
regardless of whether each source has been used on a sale. Source metadata, no PII.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402


def main():
    s = load_settings()
    with client_from_settings(s) as client:
        # search with includeData=1 returns the full records inline.
        resp = client.request("customerSource", "search", {"includeData": 1})
        print("customerSource count:", resp.get("count"))
        records = resp.get("customerSources") or []

    print("records:", len(records))
    if records:
        print("fields on a record:", list(records[0].keys()))

    # Build label -> id from likely field names.
    def field(rec, *names):
        for n in names:
            if n in rec and rec[n] not in (None, ""):
                return rec[n]
        return None

    label_to_id = {}
    for r in records:
        label = field(r, "name", "source", "title", "label")
        sid = field(r, "sourceID", "customerSourceID", "id", "ID")
        if label is not None and sid is not None:
            label_to_id[str(label)] = str(sid)

    # PestRoutes has DUPLICATE labels with different sourceIDs (e.g. two
    # "Source 54": 21 visible, 10136 hidden). Last-wins above can pick the
    # wrong/hidden one — pin the canonical IDs here.
    label_to_id.update({"Source 54": "21"})

    out = ROOT / "data" / "pestroutes_source_inventory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(label_to_id, indent=2), encoding="utf-8")
    print(f"\nwrote {len(label_to_id)} sources -> {out}")

    def sort_key(item):
        digits = "".join(c for c in item[0] if c.isdigit())
        return (0 if item[0].startswith("Source") else 1, int(digits) if digits else 9999, item[0])

    for label, sid in sorted(label_to_id.items(), key=sort_key)[:200]:
        print(f"  {label:>28}  -> {sid}")


if __name__ == "__main__":
    main()
