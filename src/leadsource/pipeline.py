"""Reusable pipeline steps shared by the nightly run and manual scripts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import store
from .readers.email_providers import load_email_providers
from .readers.genesys import GenesysClient, conversation_to_touch
from .readers.gmail import GmailReader
from .readers.meta import MetaClient, pull_lead_touches
from .readers.pestroutes import pull_sold_subscriptions
from .readers.source_maps import load_source_map_csv


def load_maps(s):
    source_map = load_source_map_csv(s.master_sheet)
    rules = load_email_providers(s.source_maps_dir / "email_providers.csv")
    return source_map, rules


def pull_all_meta_touches(s, since_unix: int, *, on_error=None) -> list:
    """Pull Meta lead touches across ALL configured pages (s.meta_page_configs).

    Each page gets its own MetaClient (its own token). One page failing — e.g.
    its token can't read that page — does NOT stop the others; the failure is
    reported via ``on_error`` (or printed). If EVERY configured page fails, the
    error is raised so the caller records the channel as errored.
    """
    configs = s.meta_page_configs
    touches: list = []
    errors: list[str] = []
    for pc in configs:
        try:
            with MetaClient(pc["token"]) as mc:
                touches.extend(pull_lead_touches(
                    mc, pc["page_id"], pc["source"], since_unix=since_unix))
        except Exception as e:
            errors.append(f'page {pc["page_id"]}: {str(e)[:120]}')
    if errors:
        note = "meta: " + "; ".join(errors)
        (on_error or print)(note)
        if configs and len(errors) == len(configs):
            raise RuntimeError(note)
    return touches


def ingest(conn, s, days: int = 7, channels=("genesys", "gmail", "meta", "lsa")) -> dict:
    """Pull recent touches from each channel and upsert into the store.
    Each channel is independent — one failing (e.g. expired token) doesn't stop
    the others. Returns a per-channel {pulled, added} / {error} summary."""
    source_map, rules = load_maps(s)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    since = now - timedelta(days=days)
    out: dict[str, dict] = {}

    def run(name, fn):
        try:
            touches = fn()
            out[name] = {"pulled": len(touches),
                         "added": store.ingest_touches(conn, touches, now_iso)}
        except Exception as e:
            out[name] = {"error": str(e)[:160]}

    if "genesys" in channels:
        def genesys():
            convs = []
            with GenesysClient(s.genesys_region, s.genesys_client_id,
                               s.genesys_client_secret) as gx:
                for qid in s.genesys_queue_ids:  # Inside Sales + Spanish
                    convs.extend(gx.query_conversations_range(since, now, qid))
            return [t for t in (conversation_to_touch(c, source_map) for c in convs) if t]
        run("genesys", genesys)

    if "gmail" in channels:
        def gmail():
            gm = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
            q = f"{s.gmail_lead_query_prefix} newer_than:{days}d".strip()
            res = gm.fetch_lead_results(q, rules, source_map, max_results=8000)
            return [r.touch for r in res if r.ok]
        run("gmail", gmail)

    if "meta" in channels:
        def meta():
            return pull_all_meta_touches(s, int(since.timestamp()))
        run("meta", meta)

    if "lsa" in channels and s.google_ads_refresh_token:
        def lsa():
            from .readers.lsa import client_from_settings as lsa_client
            from .readers.lsa import pull_lsa_touches
            with lsa_client(s) as lc:
                return pull_lsa_touches(
                    lc, s.lsa_source, since.strftime("%Y-%m-%d %H:%M:%S"))
        run("lsa", lsa)

    store.record_run(conn, now_iso, "ingest", f"days={days} channels={','.join(channels)}")
    return out


def pull_recent_subscriptions(client, s, days: int = 2):
    """Sold subscriptions added in the last ``days`` (all offices, scheduled only)."""
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    until = now.strftime("%Y-%m-%d %H:%M:%S")
    subs = pull_sold_subscriptions(
        client, since, until, office_ids=s.office_id_list, sourceable_only=True)
    return [x for x in subs if x.sold_at]
