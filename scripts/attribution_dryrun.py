"""End-to-end attribution DRY-RUN (reads only, writes nothing).

Pulls recently sold subscriptions from PestRoutes + recent Gmail and Genesys
touches, runs the attribution brain, and prints 'sale -> source' results.
Phones masked. Usage: python scripts/attribution_dryrun.py [sub_days] [touch_days]
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.attribution import attribute_all  # noqa: E402
from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.email_providers import load_email_providers  # noqa: E402
from leadsource.readers.genesys import GenesysClient, conversation_to_touch  # noqa: E402
from leadsource.readers.gmail import GmailReader  # noqa: E402
from leadsource.readers.meta import MetaClient, pull_lead_touches  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings, pull_sold_subscriptions  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

MASTER_SHEET = load_settings().master_sheet


def mask(phone):
    if not phone:
        return "-"
    d = "".join(c for c in phone if c.isdigit())
    return f"***{d[-4:]}" if len(d) >= 4 else "***"


def main():
    sub_days = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    touch_days = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    gmail_max = 250

    s = load_settings()
    source_map = load_source_map_csv(MASTER_SHEET)
    rules = load_email_providers(ROOT / "source_maps" / "email_providers.csv")
    now = datetime.now(timezone.utc)
    since_subs = (now - timedelta(days=sub_days)).strftime("%Y-%m-%d %H:%M:%S")
    since_touch = now - timedelta(days=touch_days)
    interval = f"{since_touch:%Y-%m-%dT%H:%M:%SZ}/{now:%Y-%m-%dT%H:%M:%SZ}"

    # 1) Sold subscriptions.
    with client_from_settings(s) as pr:
        subs = [x for x in pull_sold_subscriptions(pr, since_subs) if x.sold_at]
    print(f"sold subscriptions (last {sub_days}d): {len(subs)}")

    # 2) Touches: Genesys calls + Gmail form leads.
    touches = []
    with GenesysClient(s.genesys_region, s.genesys_client_id, s.genesys_client_secret) as gx:
        convs = gx.query_conversations(interval, s.genesys_inside_sales_queue_id)
    g_touches = [t for t in (conversation_to_touch(c, source_map) for c in convs) if t]
    touches += g_touches
    print(f"Genesys touches (last {touch_days}d): {len(g_touches)} (of {len(convs)} calls)")

    gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
    results = gm.fetch_lead_results(f"newer_than:{touch_days}d", rules, source_map, max_results=gmail_max)
    e_touches = [r.touch for r in results if r.ok]
    touches += e_touches
    print(f"Gmail touches (last {touch_days}d): {len(e_touches)} (of {len(results)} emails)")

    from leadsource.pipeline import pull_all_meta_touches
    m_touches = pull_all_meta_touches(s, int(since_touch.timestamp()))
    touches += m_touches
    print(f"Meta touches (last {touch_days}d): {len(m_touches)}")

    # --- diagnostics: source mix + phone overlap ignoring time ---
    from collections import Counter
    from leadsource.attribution import TouchIndex

    mix = Counter((x.current_source or "(none)") for x in subs)
    print("\nsub source mix (top):")
    for src, n in mix.most_common(12):
        print(f"  {n:>4}  {src}")

    idx = TouchIndex(touches)
    overlap = Counter()
    for x in subs:
        hit = None
        for key in (x.phone1_e164, x.phone2_e164):
            for t in idx.for_phone(key):
                hit = t.channel.value
                break
            if hit:
                break
        if not hit:
            for t in idx.for_email(x.email):
                hit = t.channel.value
                break
        overlap[hit or "no match (any time)"] += 1
    print("\nphone/email overlap IGNORING sold-date cutoff (isolates join vs timing):")
    for k, n in overlap.most_common():
        print(f"  {n:>4}  {k}")

    # 3) Attribute (with protected sources honored).
    out = attribute_all(subs, touches, stale_window_days=s.stale_window_days,
                        same_day_cluster_hours=s.same_day_cluster_hours,
                        protected_sources=s.protected_source_set)
    attributed = [r for r in out if r.status.value == "attributed"]
    unsourced = [r for r in out if r.is_unsourced]
    writes = [r for r in out if r.needs_write]
    protected = [r for r in attributed if r.protected]
    print(f"\n=== RESULTS ===")
    print(f"{len(attributed)}/{len(out)} attributed | {len(writes)} WOULD WRITE "
          f"(fill/correct) | {len(protected)} protected (kept) | {len(unsourced)} unsourced")
    print("\nwrites the engine would make (sub -> source):")
    by_id = {x.subscription_id: x for x in subs}
    for r in writes[:20]:
        sub_ = by_id.get(r.subscription_id)
        wt = r.winning_touch
        print(f"  sub {r.subscription_id} | current={sub_.current_source or '(blank)':>9} "
              f"-> {r.attributed_source} via {r.matched_key.value} ({wt.channel.value})")

    print("\nprotected (kept as-is despite a touch):")
    for r in protected[:10]:
        sub_ = by_id.get(r.subscription_id)
        print(f"  sub {r.subscription_id} | kept {sub_.current_source} "
              f"(would've been {r.attributed_source})")


if __name__ == "__main__":
    main()
