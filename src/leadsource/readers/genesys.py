"""Genesys Cloud reader: inside-sales calls -> Touches.

Auth is OAuth **client credentials** (server-to-server): POST to
``https://login.<region>/oauth/token`` with the client id/secret, get a bearer
token, call ``https://api.<region>``.

Calls come from the Analytics **Conversation Detail** query
(``POST /api/v2/analytics/conversations/details/query``), filtered to the
inside-sales queue via a ``segmentFilters`` ``queueId`` predicate. For each call
we read **ANI** (caller's number = the join phone) and **DNIS** (the tracking
number dialed = the source, resolved via the sourcing sheet).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import httpx

from ..models import Channel, Touch
from ..normalize import as_naive_utc, normalize_phone
from .source_maps import SourceMap


class GenesysError(RuntimeError):
    pass


def _strip_tel(addr: str | None) -> str | None:
    """Genesys addresses look like 'tel:+17705551234' or 'sip:...'. Keep digits."""
    if not addr:
        return None
    val = addr.strip()
    for prefix in ("tel:", "sip:"):
        if val.lower().startswith(prefix):
            val = val[len(prefix):]
    # Drop any @host on sip uris.
    return val.split("@", 1)[0]


class GenesysClient:
    def __init__(self, region: str, client_id: str, client_secret: str, timeout: float = 30.0):
        if not region or not client_id or not client_secret:
            raise GenesysError("Missing Genesys region / client id / secret.")
        self.region = region.strip()
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._http = httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return f"GenesysClient(region={self.region!r})"

    @property
    def api_base(self) -> str:
        return f"https://api.{self.region}"

    @property
    def login_base(self) -> str:
        return f"https://login.{self.region}"

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GenesysClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- auth ---------------------------------------------------------------
    def _authenticate(self) -> str:
        resp = self._http.post(
            f"{self.login_base}/oauth/token",
            data={"grant_type": "client_credentials"},
            auth=(self._client_id, self._client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code >= 400:
            raise GenesysError(f"OAuth failed: HTTP {resp.status_code}: {resp.text[:200]}")
        self._token = resp.json().get("access_token")
        if not self._token:
            raise GenesysError("OAuth response had no access_token.")
        return self._token

    def _headers(self) -> dict[str, str]:
        if not self._token:
            self._authenticate()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, **kw) -> Any:
        url = f"{self.api_base}{path}"
        resp = self._http.request(method, url, headers=self._headers(), **kw)
        if resp.status_code == 401:  # token expired -> refresh once
            self._authenticate()
            resp = self._http.request(method, url, headers=self._headers(), **kw)
        if resp.status_code >= 400:
            raise GenesysError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # --- queues -------------------------------------------------------------
    def list_queues(self) -> list[dict[str, str]]:
        """All routing queues as [{id, name}] (paginated)."""
        out: list[dict[str, str]] = []
        page = 1
        while True:
            data = self._request("GET", f"/api/v2/routing/queues?pageSize=100&pageNumber={page}")
            for q in data.get("entities", []):
                out.append({"id": q.get("id"), "name": q.get("name")})
            if page >= int(data.get("pageCount", 1)):
                break
            page += 1
        return out

    def find_queue_id(self, name_contains: str) -> str | None:
        needle = name_contains.lower()
        for q in self.list_queues():
            if needle in (q["name"] or "").lower():
                return q["id"]
        return None

    # --- conversations ------------------------------------------------------
    def query_conversations(self, interval: str, queue_id: str, max_pages: int = 80) -> list[dict]:
        """Conversation Detail records for a queue over an ISO8601 interval
        (e.g. '2026-06-01T00:00:00Z/2026-06-08T00:00:00Z')."""
        conversations: list[dict] = []
        page = 1
        while page <= max_pages:
            body = {
                "interval": interval,
                "order": "asc",
                "segmentFilters": [
                    {"type": "and", "predicates": [{"dimension": "queueId", "value": queue_id}]}
                ],
                "paging": {"pageSize": 100, "pageNumber": page},
            }
            data = self._request(
                "POST", "/api/v2/analytics/conversations/details/query", json=body
            )
            batch = data.get("conversations", [])
            conversations.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return conversations

    def query_conversations_range(
        self, start: datetime, end: datetime, queue_id: str
    ) -> list[dict]:
        """Pull conversations over an arbitrary range, chunked into <=30-day
        windows (Genesys caps a single query at 31 days)."""
        out: list[dict] = []
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=30), end)
            interval = f"{cur:%Y-%m-%dT%H:%M:%SZ}/{nxt:%Y-%m-%dT%H:%M:%SZ}"
            out.extend(self.query_conversations(interval, queue_id))
            cur = nxt
        return out


def extract_ani_dnis(conv: dict) -> tuple[str | None, str | None]:
    """Pull the caller's ANI and the dialed DNIS (digits only) from a call.

    Prefers the ``customer`` participant's voice leg; field paths confirmed
    against live data during the Genesys probe.
    """
    ani = dnis = None
    # Customer leg first (most reliable), then any voice leg.
    for want_customer in (True, False):
        for p in conv.get("participants", []):
            if want_customer and p.get("purpose") != "customer":
                continue
            for s in p.get("sessions", []):
                if s.get("mediaType") and s.get("mediaType") != "voice":
                    continue
                if s.get("ani") and not ani:
                    ani = s.get("ani")
                if s.get("dnis") and not dnis:
                    dnis = s.get("dnis")
        if ani or dnis:
            break
    return _strip_tel(ani), _strip_tel(dnis)


def conversation_to_touch(conv: dict, source_map: SourceMap) -> Touch | None:
    """Extract (caller ANI, dialed DNIS) from a conversation -> Touch."""
    ani, dnis = extract_ani_dnis(conv)
    phone = normalize_phone(ani)
    source = source_map.source_for_dnis(dnis)
    if not phone or not source:
        return None

    from datetime import datetime

    when = None
    occurred = conv.get("conversationStart")
    if occurred:
        try:
            when = as_naive_utc(datetime.fromisoformat(occurred.replace("Z", "+00:00")))
        except ValueError:
            when = None
    return Touch(
        channel=Channel.GENESYS,
        source=source,
        occurred_at=when,
        phone_e164=phone,
        raw_ref=conv.get("conversationId"),
    )
