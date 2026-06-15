"""Build the definitive source map: every canonical 'Source N' in the sheet ->
its PestRoutes numeric sourceID, plus the friendly provider name.

Read-only, no PII. Uses the customerSource picklist (all 199 sources, incl.
unused) instead of sampling sales, so coverage is complete. Writes
data/source_id_map.json and reports any sheet source missing from PestRoutes.
"""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

SHEET = load_settings().master_sheet


def sheet_sources():
    """Return {canonical 'Source N' -> provider name} from the master sheet."""
    out: dict[str, str] = {}
    with open(SHEET, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            src = (row.get("Pestroutes Source") or "").strip()
            prov = (row.get("Provider / Channel") or "").strip()
            if src:
                out.setdefault(src, prov)
    return out


def main():
    sources = sheet_sources()
    s = load_settings()
    with client_from_settings(s) as client:
        inventory = client.list_customer_sources()  # {label -> sourceID}

    # PestRoutes has DUPLICATE labels with different sourceIDs (two "Source 54":
    # 21 visible, 10136 hidden) — last-wins picks the wrong one. Pin canonical IDs.
    inventory.update({"Source 54": "21"})

    inv_path = ROOT / "data" / "pestroutes_source_inventory.json"
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(f"PestRoutes picklist: {len(inventory)} sources -> {inv_path}")

    resolved, unresolved = {}, []
    for source, provider in sources.items():
        sid = inventory.get(source)
        if sid:
            resolved[source] = {"provider": provider, "source_id": sid}
        else:
            unresolved.append((source, provider))

    out = ROOT / "data" / "source_id_map.json"
    out.write_text(json.dumps(resolved, indent=2), encoding="utf-8")
    print(f"RESOLVED {len(resolved)} / {len(sources)} sheet sources -> {out}")

    if unresolved:
        print(f"\nUNRESOLVED ({len(unresolved)}) - in sheet but not in PestRoutes picklist:")
        for src, prov in unresolved:
            print(f"  {src}  ({prov})")
    else:
        print("All sheet sources resolve to a PestRoutes sourceID.")

    # Sanity-check the loaded source map too.
    sm = load_source_map_csv(SHEET)
    print(f"\nsource map: {len(sm.dnis_to_source)} DNIS -> source, "
          f"{len(sm.provider_to_source)} provider names, {len(sm.warnings)} warnings")
    for w in sm.warnings[:10]:
        print("  -", w)


if __name__ == "__main__":
    main()
