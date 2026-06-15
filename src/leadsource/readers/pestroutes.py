"""PestRoutes / FieldRoutes API client.

Office-keyed REST API at ``https://<subdomain>.pestroutes.com/api``. Auth is via
``authenticationKey`` + ``authenticationToken`` params. Each entity exposes
``/search`` (returns matching IDs, or full records with ``includeData=1``) and
``/get`` (full records by ID).

Secret-safe: credentials are sent as request params but are never logged or
included in ``repr``/exceptions raised here.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..models import Subscription
from ..normalize import as_naive_utc, normalize_email, normalize_phone

# PestRoutes returns datetimes in the office's local timezone. Brooks HQ is
# Pacific; localize so sold dates are comparable (as naive UTC) to call/email
# touches. TODO: read the office timezone from the API to be exact.
_OFFICE_TZ = ZoneInfo("America/Los_Angeles")


class PestRoutesError(RuntimeError):
    """An API call failed or returned an unsuccessful payload."""


def _scrub_auth(data: Any) -> Any:
    """Redact the credentials PestRoutes echoes back in ``params`` so they never
    reach logs, prints, or transcripts."""
    if isinstance(data, dict):
        params = data.get("params")
        if isinstance(params, dict):
            for k in ("authenticationKey", "authenticationToken"):
                if k in params:
                    params[k] = "<redacted>"
    return data


class PestRoutesClient:
    def __init__(
        self,
        base_url: str,
        auth_key: str,
        auth_token: str,
        timeout: float = 30.0,
    ) -> None:
        if not base_url or not auth_key or not auth_token:
            raise PestRoutesError("Missing PestRoutes base URL / key / token.")
        self.base_url = base_url.rstrip("/")
        # Kept private so they never show up in repr/logs.
        self._auth = {
            "authenticationKey": auth_key,
            "authenticationToken": auth_token,
        }
        self._client = httpx.Client(timeout=timeout)

    def __repr__(self) -> str:  # never leak creds
        return f"PestRoutesClient(base_url={self.base_url!r})"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PestRoutesClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- low-level ---------------------------------------------------------
    def request(
        self,
        entity: str,
        action: str,
        params: dict[str, Any] | None = None,
        method: str = "GET",
    ) -> Any:
        """Call ``/{entity}/{action}`` and return the parsed JSON.

        Raises PestRoutesError on transport errors or a non-2xx status, with the
        URL path (never the credentials) in the message.
        """
        url = f"{self.base_url}/{entity}/{action}"
        query = {**self._auth, **(params or {})}
        try:
            resp = self._client.request(method, url, params=query)
        except httpx.HTTPError as e:
            raise PestRoutesError(f"{method} {entity}/{action} failed: {e}") from e
        if resp.status_code >= 400:
            raise PestRoutesError(
                f"{method} {entity}/{action} -> HTTP {resp.status_code}: "
                f"{resp.text[:300]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise PestRoutesError(
                f"{entity}/{action} returned non-JSON: {resp.text[:300]}"
            ) from e
        return _scrub_auth(data)

    # --- high-level (shapes confirmed during Phase 0 probe) ----------------
    def list_customer_sources(self) -> dict[str, str]:
        """The full source picklist as ``{label -> sourceID}`` (all 199, incl.
        unused). Source lives on the ``customerSource`` entity; ``includeData=1``
        returns the records inline. Each record: ``source`` (label) + ``sourceID``.
        """
        resp = self.request("customerSource", "search", {"includeData": 1})
        records = resp.get("customerSources") if isinstance(resp, dict) else None
        label_to_id: dict[str, str] = {}
        for r in records or []:
            label = r.get("source")
            sid = r.get("sourceID")
            if label not in (None, "") and sid not in (None, ""):
                label_to_id[str(label)] = str(sid)
        return label_to_id

    def search_subscriptions(self, params: dict[str, Any] | None = None) -> Any:
        return self.request("subscription", "search", params)

    @staticmethod
    def _id_param(ids: list[str] | str) -> str:
        """Comma-join IDs. PestRoutes QUIRK: a get with exactly ONE id returns
        nothing — so we pad a lone id to two (deduped server-side)."""
        items = [ids] if isinstance(ids, str) else [str(i) for i in ids]
        if len(items) == 1:
            items = items * 2
        return ",".join(map(str, items))

    def get_subscriptions(self, subscription_ids: list[str] | str) -> Any:
        return self.request("subscription", "get",
                            {"subscriptionIDs": self._id_param(subscription_ids)})

    def get_customers(self, customer_ids: list[str] | str) -> Any:
        return self.request("customer", "get",
                            {"customerIDs": self._id_param(customer_ids)})

    def update_subscription(self, subscription_id: str, fields: dict[str, Any]) -> Any:
        """Update fields on one subscription (e.g. ``{'sourceID': 10064}``).

        WRITE operation. DANGER: an update that omits a field can BLANK it, so we
        refuse empty/None values — never accidentally clear a source.
        """
        clean = {k: v for k, v in fields.items() if v not in (None, "")}
        if not clean:
            raise PestRoutesError("update_subscription: no non-empty fields to set.")
        params = {"subscriptionID": str(subscription_id), **clean}
        return self.request("subscription", "update", params, method="POST")


def client_from_settings(settings: Any) -> PestRoutesClient:
    return PestRoutesClient(
        base_url=settings.pestroutes_base_url,
        auth_key=settings.pestroutes_auth_key,
        auth_token=settings.pestroutes_auth_token,
    )


def _chunks(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _parse_dt(value: str | None) -> datetime | None:
    if not value or value.startswith("0000"):
        return None
    try:
        local = datetime.fromisoformat(value).replace(tzinfo=_OFFICE_TZ)
    except ValueError:
        return None
    return as_naive_utc(local)


# Initial-appointment statuses worth sourcing: a real appointment that's either
# already serviced or still pending (future). Skips never-scheduled / cancelled /
# no-show — those aren't sales to source.
SOURCEABLE_INITIAL_STATUS = frozenset({"Completed", "Pending"})


def pull_sold_subscriptions(
    client: PestRoutesClient,
    since: str,
    until: str | None = None,
    office_ids: list[str] | None = None,
    sourceable_only: bool = True,
    chunk: int = 200,
) -> list[Subscription]:
    """Pull subscriptions added in a date window across offices and join each to
    its customer for the phone/email join keys.

    The global key's subscription SEARCH only returns one office at a time, so we
    poll each office in ``office_ids`` and merge. ``get`` works globally.
    ``sourceable_only`` keeps only Completed/Pending initial appointments.
    Returns engine-native Subscription objects; ``current_source`` is the
    PestRoutes label ("Source N").
    """
    if until:
        date_filter = json.dumps({"operator": "BETWEEN", "value": [since, until]})
    else:
        date_filter = json.dumps({"operator": ">=", "value": since})

    # 1. Per-office search to collect every subscription ID in the window.
    sub_ids: list[str] = []
    for oid in (office_ids or [None]):
        params: dict[str, Any] = {"dateAdded": date_filter}
        if oid is not None:
            params["officeIDs"] = str(oid)
        search = client.search_subscriptions(params)
        sub_ids.extend(str(i) for i in (search.get("subscriptionIDs") or []))
    sub_ids = list(dict.fromkeys(sub_ids))  # dedup, keep order
    if not sub_ids:
        return []

    # 2. Fetch subscription records (get is global).
    sub_records: list[dict] = []
    for batch in _chunks(sub_ids, chunk):
        got = client.get_subscriptions(batch)
        sub_records.extend(got.get("subscriptions") or [])

    # 3. Keep only sourceable (scheduled) subscriptions.
    if sourceable_only:
        sub_records = [
            r for r in sub_records
            if r.get("initialStatusText") in SOURCEABLE_INITIAL_STATUS
        ]

    # Fetch the customers referenced, for phone1/phone2/email.
    cust_ids = sorted({str(r.get("customerID")) for r in sub_records if r.get("customerID")})
    customers: dict[str, dict] = {}
    for batch in _chunks(cust_ids, chunk):
        got = client.get_customers(batch)
        for c in got.get("customers") or []:
            customers[str(c.get("customerID"))] = c

    out: list[Subscription] = []
    for r in sub_records:
        cid = str(r.get("customerID"))
        cust = customers.get(cid, {})
        out.append(
            Subscription(
                subscription_id=str(r.get("subscriptionID")),
                customer_id=cid,
                sold_at=_parse_dt(r.get("dateAdded")),
                phone1_e164=normalize_phone(cust.get("phone1")),
                phone2_e164=normalize_phone(cust.get("phone2")),
                email=normalize_email(cust.get("email")),
                current_source=(r.get("source") or None),
                office_id=(str(r.get("officeID")) if r.get("officeID") else None),
            )
        )
    return out
