"""ONE-TIME full historical backfill of every lead channel into the touch store.

Pulls Genesys + Meta + LSA over the whole range, and Gmail MONTH-BY-MONTH (a
single 5-month Gmail pull would exceed the 8000-message cap and silently drop
the oldest mail — monthly windows keep every chunk well under it). Idempotent:
the store dedups by channel:raw_ref, so re-running is safe.

Usage:  python -u scripts/backfill_all.py [since=2026-01-01] [channels=genesys,meta,lsa,gmail]
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource import store  # noqa: E402
from leadsource.config import load_settings  # noqa: E402
from leadsource.pipeline import load_maps  # noqa: E402
from leadsource.readers.genesys import GenesysClient, conversation_to_touch  # noqa: E402
from leadsource.readers.gmail import GmailReader  # noqa: E402
from leadsource.readers.lsa import client_from_settings as lsa_client  # noqa: E402
from leadsource.readers.lsa import pull_lsa_touches  # noqa: E402
from leadsource.readers.meta import MetaClient, pull_lead_touches  # noqa: E402


def month_starts(since: datetime, until: datetime):
    cur = since
    while cur < until:
        nxt = (cur.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield cur, min(nxt, until)
        cur = nxt


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
    since = datetime.strptime(arg, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    channels = (set(sys.argv[2].split(",")) if len(sys.argv) > 2
                else {"genesys", "meta", "lsa", "gmail"})
    s = load_settings()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    conn = store.connect(s.db_path)
    source_map, rules = load_maps(s)
    print(f"=== BACKFILL since {since:%Y-%m-%d} -> {now:%Y-%m-%d} "
          f"channels={sorted(channels)} ===", flush=True)

    def ingest(name, touches):
        added = store.ingest_touches(conn, touches, now_iso)
        print(f"  {name:8} pulled {len(touches):6} | added {added:6} new", flush=True)

    # --- GENESYS: 30-day windows per queue, ingest each chunk (keeps memory low
    #     on the 1GB box; a single 161-day pull got OOM-killed) ---
    if "genesys" in channels:
        try:
            import gc
            g_pulled = g_added = 0
            with GenesysClient(s.genesys_region, s.genesys_client_id,
                               s.genesys_client_secret) as gx:
                cur = since
                while cur < now:
                    nxt = min(cur + timedelta(days=30), now)
                    interval = f"{cur:%Y-%m-%dT%H:%M:%SZ}/{nxt:%Y-%m-%dT%H:%M:%SZ}"
                    for qid in s.genesys_queue_ids:  # Inside Sales + Spanish
                        convs = gx.query_conversations(interval, qid)
                        ts = [t for t in (conversation_to_touch(c, source_map) for c in convs) if t]
                        a = store.ingest_touches(conn, ts, now_iso)
                        g_pulled += len(ts); g_added += a
                        print(f"  genesys {cur:%Y-%m-%d} q={qid[:8]}: pulled {len(ts):5} | added {a:5}", flush=True)
                        del convs, ts
                        gc.collect()
                    cur = nxt
            print(f"  genesys    TOTAL pulled {g_pulled} | added {g_added} new", flush=True)
        except Exception as e:
            print(f"  genesys  ERROR: {str(e)[:160]}", flush=True)

    # --- META (follows paging, no cap) ---
    if "meta" in channels:
        try:
            from leadsource.pipeline import pull_all_meta_touches
            ts = pull_all_meta_touches(s, int(since.timestamp()))
            ingest("meta", ts)
        except Exception as e:
            print(f"  meta     ERROR: {str(e)[:160]}", flush=True)

    # --- LSA (GAQL date filter, all accounts) ---
    if "lsa" in channels:
        try:
            with lsa_client(s) as lc:
                ts = pull_lsa_touches(lc, s.lsa_source, since.strftime("%Y-%m-%d %H:%M:%S"))
            ingest("lsa", ts)
        except Exception as e:
            print(f"  lsa      ERROR: {str(e)[:160]}", flush=True)

    # --- GMAIL: month by month so no chunk hits the 8000 cap ---
    if "gmail" in channels:
        gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
        g_pulled = g_added = 0
        for a, b in month_starts(since, now):
            q = f"{s.gmail_lead_query_prefix} after:{a:%Y/%m/%d} before:{b:%Y/%m/%d}".strip()
            try:
                res = gm.fetch_lead_results(q, rules, source_map, max_results=8000)
                ts = [r.touch for r in res if r.ok]
                added = store.ingest_touches(conn, ts, now_iso)
                g_pulled += len(ts); g_added += added
                print(f"  gmail {a:%Y-%m}: pulled {len(ts):5} | added {added:5}"
                      + ("  (!) HIT 8000 CAP — split finer" if len(res) >= 8000 else ""), flush=True)
            except Exception as e:
                print(f"  gmail {a:%Y-%m} ERROR: {str(e)[:120]}", flush=True)
        print(f"  gmail    TOTAL pulled {g_pulled} | added {g_added} new", flush=True)

    store.record_run(conn, now_iso, "backfill",
                     f"since={since:%Y-%m-%d} channels={','.join(sorted(channels))}")
    print("store totals by channel:", store.counts_by_channel(conn), flush=True)
    print("=== BACKFILL DONE ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
