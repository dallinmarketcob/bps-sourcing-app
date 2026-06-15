"""Apply attributed sources back to PestRoutes — the ONLY write path.

Safety design (learned the hard way):
- Honors ``dry_run`` (default ON): plans the write, changes nothing.
- Only touches subscriptions where the engine has a NEW source AND the current
  source isn't protected (``result.needs_write``).
- Resolves the "Source N" label to a numeric ``sourceID`` from the picklist
  inventory; if it can't resolve, it SKIPS (never writes a bad/empty value).
- Verifies each real write by reading the value back.
- Logs every change (old -> new) to the ``writes`` table for a reversible trail.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import store
from .models import AttributionResult, Subscription
from .readers.pestroutes import PestRoutesClient


@dataclass
class WriteRecord:
    subscription_id: str
    customer_name: str
    old_source: str
    old_source_id: str | None
    new_source: str
    new_source_id: str | None
    decision: str
    status: str  # DRY_RUN | WRITTEN | WRITE_FAILED | NO_SOURCE_ID
    dry_run: bool

    def as_row(self) -> dict:
        return {**self.__dict__}


def apply_writeback(
    client: PestRoutesClient,
    results: list[AttributionResult],
    subs_by_id: dict[str, Subscription],
    inventory: dict[str, str],   # {"Source N": "<sourceID>"}
    conn,
    ran_at: str,
    names: dict[str, str] | None = None,  # customer_id -> name (for the log)
    dry_run: bool = True,
) -> list[WriteRecord]:
    """Write each needs_write result back to PestRoutes (or simulate if dry_run)."""
    names = names or {}
    records: list[WriteRecord] = []

    for r in results:
        if not r.needs_write:
            continue
        sub = subs_by_id.get(r.subscription_id)
        if sub is None:
            continue

        new_label = r.attributed_source or ""
        new_id = inventory.get(new_label)
        old_label = sub.current_source or ""
        old_id = inventory.get(old_label) if old_label else ""

        if not new_id:
            status = "NO_SOURCE_ID"
        elif dry_run:
            status = "DRY_RUN"
        else:
            # PestRoutes scopes writes to the sub's office — must pass officeID
            # or the update is rejected ("does not belong to office 1").
            client.update_subscription(
                sub.subscription_id, {"sourceID": new_id, "officeID": sub.office_id})
            recs = client.get_subscriptions([sub.subscription_id]).get("subscriptions") or []
            after = str(recs[0].get("sourceID")) if recs else None
            status = "WRITTEN" if after == str(new_id) else "WRITE_FAILED"

        rec = WriteRecord(
            subscription_id=sub.subscription_id,
            customer_name=names.get(sub.customer_id, ""),
            old_source=old_label, old_source_id=old_id or "",
            new_source=new_label, new_source_id=new_id or "",
            decision="FILL" if not old_label else "CHANGE",
            status=status, dry_run=dry_run,
        )
        store.record_write(conn, ran_at, rec.as_row())
        records.append(rec)

    return records
