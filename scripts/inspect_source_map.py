"""Quick probe: load a sourcing CSV and report what the loader sees.

Usage: python scripts/inspect_source_map.py "<path-to-csv>"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402


def main(path: str) -> None:
    m = load_source_map_csv(Path(path))
    print("DNIS -> source mappings:", len(m.dnis_to_source))
    print("distinct canonical sources:", len(set(m.dnis_to_source.values())))
    print("provider names -> source:", len(m.provider_to_source))
    print("warnings:", len(m.warnings))
    for w in m.warnings:
        print("  -", w)
    print("\nsample DNIS lookups:")
    for raw in ["628-232-0597", "951 963 9018", "9519639015", "(833) 411-0427"]:
        print(f"  {raw!r:18} -> {m.source_for_dnis(raw)}")
    print("\nsample provider lookups:")
    for name in ["Pestnet", "Aragon Fresno", "Website Forms"]:
        print(f"  {name!r:16} -> {m.source_for_provider(name)}")


if __name__ == "__main__":
    main(sys.argv[1])
