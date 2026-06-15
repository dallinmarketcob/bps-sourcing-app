"""Pull recent form-lead emails live from Gmail and parse them.

Non-interactive: requires a token from scripts/gmail_auth.py. PII stays local.

Usage:
  python scripts/gmail_pull.py [gmail_query] [max_results]
  e.g. python scripts/gmail_pull.py "newer_than:7d" 50
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.email_providers import load_email_providers  # noqa: E402
from leadsource.readers.gmail import GmailReader  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

MASTER_SHEET = load_settings().master_sheet


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "newer_than:7d"
    max_results = int(sys.argv[2]) if len(sys.argv) > 2 else 60

    s = load_settings()
    if not Path(s.gmail_token_file).exists():
        print("No Gmail token yet. Run: python scripts/gmail_auth.py")
        return

    rules = load_email_providers(ROOT / "source_maps" / "email_providers.csv")
    source_map = load_source_map_csv(MASTER_SHEET) if MASTER_SHEET.exists() else None
    reader = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)

    print(f"query: {query!r}  (max {max_results})")
    results = reader.fetch_lead_results(query, rules, source_map, max_results=max_results)
    print(f"fetched {len(results)} messages\n")

    ok = 0
    no_contact = 0
    unknown_sources: dict[str, int] = {}
    for r in results:
        if r.ok:
            ok += 1
        elif r.source is None:
            # Truly unidentified source -> candidate for the alias table.
            key = r.parsed.from_domain or "(no domain)"
            unknown_sources[key] = unknown_sources.get(key, 0) + 1
        else:
            no_contact += 1  # source known, but no phone/email (report/masked call)
        flag = "OK " if r.ok else "FLAG"
        print(f"[{flag}] {r.parsed.date:%Y-%m-%d} | {r.source or '-':>10} | "
              f"{r.parsed.subject[:48]!r}")
        if not r.ok:
            print(f"        from {r.parsed.from_addr[:60]} | {r.problem}")

    unknown = sum(unknown_sources.values())
    print(f"\n{ok}/{len(results)} usable touches | "
          f"{no_contact} known-source-but-no-contact | {unknown} truly unidentified")
    if unknown_sources:
        print("Unidentified senders (candidates for the alias table):")
        for dom, n in sorted(unknown_sources.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>3}  {dom}")


if __name__ == "__main__":
    main()
