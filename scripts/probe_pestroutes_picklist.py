"""Derive the 'Source N' label -> numeric sourceID map from recent subscriptions.

There's no source-list endpoint, but every subscription carries both ``source``
(label) and ``sourceID`` (numeric). Sampling recent subscriptions surfaces the
sourceIDs for all actively-used sources. Source labels/IDs are not PII.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.pestroutes import client_from_settings  # noqa: E402

SAMPLE = 500


def main():
    s = load_settings()
    with client_from_settings(s) as client:
        search = client.search_subscriptions()
        ids = [str(i) for i in (search.get("subscriptionIDs") or [])]
        recent = ids[-SAMPLE:]
        print(f"sampling {len(recent)} most-recent subscriptions (of {search.get('count')})")
        got = client.get_subscriptions(recent)
        records = got.get("subscriptions") or []

    label_to_id: dict[str, str] = {}
    sub_pairs: set[tuple[str, str]] = set()
    for r in records:
        label = r.get("source")
        sid = str(r.get("sourceID"))
        if label and sid and sid != "0":
            label_to_id.setdefault(label, sid)
        ss_label = r.get("subSource")
        ss_id = str(r.get("subSourceID"))
        if ss_label and ss_id and ss_id != "0":
            sub_pairs.add((ss_label, ss_id))

    print(f"\ndistinct source labels seen: {len(label_to_id)}")
    # Sort by the numeric N in "Source N" when possible.
    def sort_key(item):
        label = item[0]
        digits = "".join(c for c in label if c.isdigit())
        return (int(digits) if digits else 9999, label)

    for label, sid in sorted(label_to_id.items(), key=sort_key):
        print(f"  {label:>14}  ->  sourceID {sid}")

    if sub_pairs:
        print(f"\ndistinct sub-sources seen: {len(sub_pairs)}")
        for label, sid in sorted(sub_pairs):
            print(f"  {label}  -> subSourceID {sid}")


if __name__ == "__main__":
    main()
