"""SQLite persistent touch store.

Leads precede sales by weeks, so we can't rely on a short live pull — we
accumulate touches over time. Each daily ingest upserts that day's touches
(deduped by ``channel:raw_ref``); attribution then reads a deep rolling history.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from collections.abc import Iterable

from .models import Channel, Touch

_SCHEMA = """
CREATE TABLE IF NOT EXISTS touches (
    dedup_key   TEXT PRIMARY KEY,
    channel     TEXT NOT NULL,
    source      TEXT NOT NULL,
    occurred_at TEXT,
    phone_e164  TEXT,
    email       TEXT,
    raw_ref     TEXT,
    ingested_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_touch_phone ON touches(phone_e164);
CREATE INDEX IF NOT EXISTS idx_touch_email ON touches(email);
CREATE INDEX IF NOT EXISTS idx_touch_time  ON touches(occurred_at);

CREATE TABLE IF NOT EXISTS runs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at  TEXT NOT NULL,
    kind    TEXT,
    detail  TEXT
);

-- Audit trail of every source write-back (old -> new) for full reversibility.
CREATE TABLE IF NOT EXISTS writes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ran_at          TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    customer_name   TEXT,
    old_source      TEXT,
    old_source_id   TEXT,
    new_source      TEXT,
    new_source_id   TEXT,
    decision        TEXT,
    status          TEXT,   -- DRY_RUN | WRITTEN | WRITE_FAILED | NO_SOURCE_ID
    dry_run         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_writes_sub ON writes(subscription_id);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _dedup_key(t: Touch) -> str:
    ref = t.raw_ref or f"{t.phone_e164}|{t.occurred_at}|{t.source}"
    return f"{t.channel.value}:{ref}"


def ingest_touches(conn: sqlite3.Connection, touches: Iterable[Touch], now_iso: str) -> int:
    """Upsert touches; returns the number of NEW rows added (dupes ignored)."""
    before = conn.total_changes
    rows = [
        (
            _dedup_key(t), t.channel.value, t.source,
            t.occurred_at.isoformat() if t.occurred_at else None,
            t.phone_e164, t.email, t.raw_ref, now_iso,
        )
        for t in touches
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO touches "
        "(dedup_key, channel, source, occurred_at, phone_e164, email, raw_ref, ingested_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def load_touches(conn: sqlite3.Connection, since: datetime | None = None) -> list[Touch]:
    """Load touches (optionally only those on/after ``since``) as Touch objects."""
    if since is not None:
        cur = conn.execute(
            "SELECT * FROM touches WHERE occurred_at >= ?", (since.isoformat(),)
        )
    else:
        cur = conn.execute("SELECT * FROM touches")
    out: list[Touch] = []
    for r in cur.fetchall():
        out.append(
            Touch(
                channel=Channel(r["channel"]),
                source=r["source"],
                occurred_at=datetime.fromisoformat(r["occurred_at"]) if r["occurred_at"] else None,
                phone_e164=r["phone_e164"],
                email=r["email"],
                raw_ref=r["raw_ref"],
            )
        )
    return out


def counts_by_channel(conn: sqlite3.Connection) -> dict[str, int]:
    cur = conn.execute("SELECT channel, COUNT(*) n FROM touches GROUP BY channel")
    return {r["channel"]: r["n"] for r in cur.fetchall()}


def record_run(conn: sqlite3.Connection, ran_at: str, kind: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO runs (ran_at, kind, detail) VALUES (?,?,?)", (ran_at, kind, detail)
    )
    conn.commit()


def record_write(conn: sqlite3.Connection, ran_at: str, row: dict) -> None:
    """Append a write-back to the audit trail (old -> new)."""
    conn.execute(
        "INSERT INTO writes (ran_at, subscription_id, customer_name, old_source, "
        "old_source_id, new_source, new_source_id, decision, status, dry_run) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (ran_at, row.get("subscription_id"), row.get("customer_name"),
         row.get("old_source"), row.get("old_source_id"), row.get("new_source"),
         row.get("new_source_id"), row.get("decision"), row.get("status"),
         1 if row.get("dry_run") else 0),
    )
    conn.commit()
