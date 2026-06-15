"""Read-only Genesys probe: authenticate, list queues, inspect recent calls.

Phone numbers (ANI) are masked in output. Run after putting GENESYS_REGION,
GENESYS_CLIENT_ID, GENESYS_CLIENT_SECRET in .env.

Usage:
  python scripts/genesys_probe.py            # list queues (find inside-sales)
  python scripts/genesys_probe.py <queueId>  # pull recent calls for that queue
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.genesys import (  # noqa: E402
    GenesysClient,
    conversation_to_touch,
    extract_ani_dnis,
    _strip_tel,
)
from leadsource.normalize import normalize_phone  # noqa: E402
from leadsource.readers.source_maps import load_source_map_csv  # noqa: E402

MASTER_SHEET = load_settings().master_sheet


def mask(phone: str | None) -> str:
    if not phone:
        return "-"
    digits = "".join(c for c in phone if c.isdigit())
    return f"***{digits[-4:]}" if len(digits) >= 4 else "***"


def main():
    s = load_settings()
    client = GenesysClient(s.genesys_region, s.genesys_client_id, s.genesys_client_secret)
    print(f"region: {s.genesys_region}  (api {client.api_base})")

    queue_id = sys.argv[1] if len(sys.argv) > 1 else (s.genesys_inside_sales_queue_id or "")

    if not queue_id:
        print("\nQueues (find your inside-sales one):")
        for q in client.list_queues():
            print(f"  {q['id']}  {q['name']}")
        print("\nRe-run with: python scripts/genesys_probe.py <queueId>")
        client.close()
        return

    # Pull recent calls for the queue (last 2 days).
    interval = "2026-06-06T00:00:00Z/2026-06-08T23:59:59Z"
    print(f"\nquerying queue {queue_id} over {interval}")
    convs = client.query_conversations(interval, queue_id)
    print(f"conversations: {len(convs)}")

    source_map = load_source_map_csv(MASTER_SHEET) if MASTER_SHEET.exists() else None

    # Inspect structure of the first couple.
    for conv in convs[:2]:
        print(f"\nconv {conv.get('conversationId')} start={conv.get('conversationStart')}")
        for p in conv.get("participants", []):
            for sess in p.get("sessions", []):
                print(f"  purpose={p.get('purpose')} media={sess.get('mediaType')} "
                      f"dir={sess.get('direction')} ani={mask(_strip_tel(sess.get('ani')))} "
                      f"dnis={_strip_tel(sess.get('dnis'))}")

    if source_map:
        touches = [t for t in (conversation_to_touch(c, source_map) for c in convs) if t]
        print(f"\n{len(touches)} of {len(convs)} conversations -> touches")
        for t in touches[:10]:
            print(f"  {t.occurred_at} | {t.source} | caller {mask(t.phone_e164)}")

        # Diagnose the non-touches.
        no_dnis = no_ani = 0
        unmapped_dnis: dict[str, int] = {}
        for c in convs:
            ani, dnis = extract_ani_dnis(c)
            if not normalize_phone(ani):
                no_ani += 1
            if not source_map.source_for_dnis(dnis):
                if dnis:
                    unmapped_dnis[dnis] = unmapped_dnis.get(dnis, 0) + 1
                else:
                    no_dnis += 1
        print(f"\nnon-touches: {no_ani} no/invalid caller ANI, {no_dnis} no DNIS, "
              f"{sum(unmapped_dnis.values())} calls on DNIS not in the sheet")
        if unmapped_dnis:
            print("unmapped tracking numbers (DNIS) - add to sheet if they're lead sources:")
            for d, n in sorted(unmapped_dnis.items(), key=lambda kv: -kv[1])[:25]:
                print(f"  {n:>3}  {d}")

    client.close()


if __name__ == "__main__":
    main()
