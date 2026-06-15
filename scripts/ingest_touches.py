"""Pull recent touches from every channel and upsert into the SQLite store.

Run daily (or now, to backfill). Deduped, so re-running is safe. Each channel is
independent — if one API errors (e.g. an expired token), the others still ingest.

Usage: python scripts/ingest_touches.py [days]   (default 30)
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource import store  # noqa: E402
from leadsource.readers.email_providers import load_email_providers  # noqa: E402
from leadsource.readers.genesys import GenesysClient, conversation_to_touch  # noqa: E402
from leadsource.readers.gmail import GmailReader  # noqa: E402
from leadsource.readers.meta import MetaClient, pull_lead_touches  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

MASTER_SHEET = load_settings().master_sheet


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    only = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else {"genesys", "gmail", "meta"}
    s = load_settings()
    source_map = load_source_map_csv(MASTER_SHEET)
    rules = load_email_providers(ROOT / "source_maps" / "email_providers.csv")
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    since = now - timedelta(days=days)
    interval = f"{since:%Y-%m-%dT%H:%M:%SZ}/{now:%Y-%m-%dT%H:%M:%SZ}"

    conn = store.connect(s.db_path)

    def ingest(name, fn):
        try:
            touches = fn()
            added = store.ingest_touches(conn, touches, now_iso)
            print(f"  {name:8} pulled {len(touches):5} | added {added:5} new")
        except Exception as e:
            print(f"  {name:8} ERROR: {str(e)[:140]}")

    print(f"ingesting last {days} days of touches into {s.db_path}")

    def genesys():
        with GenesysClient(s.genesys_region, s.genesys_client_id, s.genesys_client_secret) as gx:
            convs = gx.query_conversations_range(since, now, s.genesys_inside_sales_queue_id)
        return [t for t in (conversation_to_touch(c, source_map) for c in convs) if t]

    def gmail():
        gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
        # Query only mail FROM known lead senders (the inbox is far too busy to
        # skim the most-recent N messages and still catch every form).
        query = f"{s.gmail_lead_query_prefix} newer_than:{days}d".strip()
        results = gm.fetch_lead_results(query, rules, source_map, max_results=8000)
        return [r.touch for r in results if r.ok]

    def meta():
        with MetaClient(s.meta_access_token) as mc:
            return pull_lead_touches(mc, s.meta_page_id, s.meta_lead_source,
                                     since_unix=int(since.timestamp()))

    if "genesys" in only:
        ingest("genesys", genesys)
    if "gmail" in only:
        ingest("gmail", gmail)
    if "meta" in only:
        ingest("meta", meta)

    store.record_run(conn, now_iso, "ingest", f"days={days}")
    totals = store.counts_by_channel(conn)
    print("store totals by channel:", totals, "| grand total:", sum(totals.values()))


if __name__ == "__main__":
    main()
