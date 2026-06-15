"""Core domain models shared by readers, the brain, and storage.

These are plain dataclasses so the attribution brain stays pure and trivially
testable — no I/O, no framework objects.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Channel(str, Enum):
    """Where a touch was observed."""

    META = "meta"
    GMAIL = "gmail"
    GENESYS = "genesys"
    LSA = "lsa"  # Google Local Services Ads leads (message/booking; calls via Genesys)
    PESTROUTES = "pestroutes"  # the CRM's own pre-existing source, if any


class MatchKey(str, Enum):
    """Which customer identifier matched the touches for a sale."""

    PHONE1 = "phone1"
    PHONE2 = "phone2"
    EMAIL = "email"
    NONE = "none"


class AttributionStatus(str, Enum):
    ATTRIBUTED = "attributed"          # a source was chosen
    NO_TOUCHES = "no_touches"          # nothing matched; leave CRM untouched, flag


@dataclass(frozen=True)
class Touch:
    """A single observed lead interaction, already normalized.

    ``source`` is the attributable provider/campaign name. ``phone_e164`` and
    ``email`` are the normalized join keys (either may be ``None``).
    """

    channel: Channel
    source: str
    occurred_at: datetime
    phone_e164: str | None = None
    email: str | None = None
    raw_ref: str | None = None  # upstream id, for audit/debug


@dataclass(frozen=True)
class Subscription:
    """A sold subscription pulled from PestRoutes — the thing to attribute."""

    subscription_id: str
    customer_id: str
    sold_at: datetime
    phone1_e164: str | None = None
    phone2_e164: str | None = None
    email: str | None = None
    current_source: str | None = None
    office_id: str | None = None  # PestRoutes scopes writes to the sub's office


@dataclass
class AttributionResult:
    """Outcome of running the brain on one subscription."""

    subscription_id: str
    status: AttributionStatus
    attributed_source: str | None = None
    matched_key: MatchKey = MatchKey.NONE
    winning_touch: Touch | None = None
    streak: list[Touch] = field(default_factory=list)
    is_change: bool = False  # chosen source differs from the CRM's current source
    protected: bool = False  # current source is internal/process -> never overwrite
    reason: str = ""

    @property
    def needs_write(self) -> bool:
        """True when we have a new source AND the current one isn't protected."""
        return (
            self.status is AttributionStatus.ATTRIBUTED
            and self.is_change
            and not self.protected
        )

    @property
    def is_unsourced(self) -> bool:
        """No touch matched on any key — goes on the weekly unsourced report."""
        return self.status is AttributionStatus.NO_TOUCHES
