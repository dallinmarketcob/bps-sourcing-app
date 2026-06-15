"""Run the email parser over real .eml samples and report what it understood.

Reads every .eml in email_samples/, identifies the source (via the alias table +
source map), extracts phone/email, and flags anything unparseable -- so we can
tune the parser and fill in source_maps/email_providers.csv. PII stays local.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.readers.email_providers import load_email_providers  # noqa: E402
from leadsource.readers.gmail import build_touch_from_email  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

SAMPLES = ROOT / "email_samples"
MASTER_SHEET = load_settings().master_sheet


def main():
    rules = load_email_providers(ROOT / "source_maps" / "email_providers.csv")
    source_map = load_source_map_csv(MASTER_SHEET) if MASTER_SHEET.exists() else None
    n_names = len(source_map.provider_to_source) if source_map else 0
    print(f"loaded {len(rules)} provider rules, {n_names} sheet provider names\n")

    files = sorted(SAMPLES.glob("*.eml")) + sorted(SAMPLES.glob("*.txt"))
    if not files:
        print("No samples found. Drop .eml files into email_samples/ "
              "(see email_samples/README.md).")
        return

    ok = 0
    for f in files:
        res = build_touch_from_email(f.read_bytes(), rules, source_map, raw_ref=f.name)
        status = "OK " if res.ok else "FLAG"
        if res.ok:
            ok += 1
        print(f"[{status}] {f.name}")
        print(f"        from:    {res.parsed.from_addr}")
        print(f"        subject: {res.parsed.subject}")
        print(f"        source:  {res.source}   ({res.reason})")
        print(f"        phones:  {res.phones}")
        print(f"        emails:  {res.emails}")
        if res.problem:
            print(f"        PROBLEM: {res.problem}")
        print()

    print(f"{ok}/{len(files)} produced a usable touch.")


if __name__ == "__main__":
    main()
