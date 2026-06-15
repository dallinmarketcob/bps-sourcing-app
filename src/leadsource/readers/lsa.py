"""Google Local Services Ads (LSA) reader — pulls leads via the Google Ads API.

LSA *calls* are already captured via Genesys (the call routes to our line with
the real caller ID). What this closes is the LSA *message/booking* leads that
otherwise vanish into Podium behind a masked number. The Google Ads API's
``local_services_lead`` resource returns every lead with the consumer's REAL
name + phone, which we turn into a Touch (all credited to ``Source 145``).

Auth chain: OAuth refresh-token -> access-token; requests carry the developer
token + login-customer-id (the MCC). One MCC + token covers all child accounts.

Field names / API version are validated by scripts/lsa_probe.py against the live
API once Basic Access is granted (parsing here is defensive).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..models import Channel, Touch
from ..normalize import as_naive_utc, normalize_email, normalize_phone

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Google Ads creation_date_time is in the account's timezone (no offset given).
# All branches are Pacific (CA/WA/OR), matching how we treat PestRoutes times.
_LSA_TZ = ZoneInfo("America/Los_Angeles")


class LSAError(RuntimeError):
    pass


class GoogleAdsClient:
    def __init__(self, developer_token: str, login_customer_id: str, client_id: str,
                 client_secret: str, refresh_token: str, api_version: str = "v18",
                 timeout: float = 30.0):
        for name, val in [("developer_token", developer_token), ("login_customer_id", login_customer_id),
                          ("client_id", client_id), ("client_secret", client_secret),
                          ("refresh_token", refresh_token)]:
            if not val:
                raise LSAError(f"Missing Google Ads credential: {name}")
        self._dev = developer_token
        self._login = "".join(ch for ch in str(login_customer_id) if ch.isdigit())
        self._cid = client_id
        self._csec = client_secret
        self._refresh = refresh_token
        self.base = f"https://googleads.googleapis.com/{api_version}"
        self._http = httpx.Client(timeout=timeout)
        self._access: str | None = None

    def __enter__(self) -> "GoogleAdsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def _access_token(self) -> str:
        if self._access:
            return self._access
        r = self._http.post(OAUTH_TOKEN_URL, data={
            "client_id": self._cid, "client_secret": self._csec,
            "refresh_token": self._refresh, "grant_type": "refresh_token"})
        if r.status_code >= 400:
            raise LSAError(f"OAuth token refresh failed HTTP {r.status_code}: {r.text[:200]}")
        self._access = r.json()["access_token"]
        return self._access

    def _headers(self) -> dict[str, str]:
        return {"developer-token": self._dev, "login-customer-id": self._login,
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json"}

    def search(self, customer_id: str, query: str) -> list[dict]:
        """Run a GAQL query against one account; returns the result rows."""
        cid = "".join(ch for ch in str(customer_id) if ch.isdigit())
        url = f"{self.base}/customers/{cid}/googleAds:searchStream"
        try:
            r = self._http.post(url, headers=self._headers(), json={"query": query})
        except httpx.HTTPError as e:
            raise LSAError(f"search {cid} failed: {e}") from e
        if r.status_code >= 400:
            raise LSAError(f"search {cid} -> HTTP {r.status_code}: {r.text[:400]}")
        out: list[dict] = []
        data = r.json()
        # searchStream returns a list of batches, each with a "results" array.
        for batch in (data if isinstance(data, list) else [data]):
            out.extend(batch.get("results", []))
        return out

    def child_accounts(self) -> list[str]:
        """Non-manager account IDs under the MCC (the LSA accounts to query)."""
        rows = self.search(self._login,
                           "SELECT customer_client.id, customer_client.manager, "
                           "customer_client.descriptive_name FROM customer_client "
                           "WHERE customer_client.level <= 1")
        ids = []
        for row in rows:
            cc = row.get("customerClient", {})
            if not cc.get("manager"):
                ids.append(str(cc.get("id")))
        return ids


def _contact(lead: dict) -> tuple[str | None, str | None]:
    """Pull (phone_e164, email) from a lead's contact_details (defensive)."""
    details = lead.get("contactDetails") or lead.get("contact_details") or []
    if isinstance(details, dict):
        details = [details]
    phone = email = None
    for d in details:
        phone = phone or normalize_phone(d.get("phoneNumber") or d.get("phone_number"))
        email = email or normalize_email(d.get("email"))
    return phone, email


def _when(lead: dict) -> datetime | None:
    raw = lead.get("creationDateTime") or lead.get("creation_date_time")
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:          # account-local (Pacific) -> UTC
                dt = dt.replace(tzinfo=_LSA_TZ)
            return as_naive_utc(dt)
        except ValueError:
            continue
    return None


def lead_to_touch(lead: dict, source: str) -> Touch | None:
    phone, email = _contact(lead)
    if not phone and not email:
        return None
    return Touch(
        channel=Channel.LSA, source=source, occurred_at=_when(lead),
        phone_e164=phone, email=email,
        raw_ref=str(lead.get("id") or lead.get("resourceName") or ""),
    )


def pull_lsa_touches(client: GoogleAdsClient, source: str, since: str,
                     account_ids: list[str] | None = None) -> list[Touch]:
    """Pull LSA leads created since ``since`` (``YYYY-MM-DD HH:MM:SS``) across all
    child accounts (auto-discovered if ``account_ids`` not given) -> Touches."""
    accounts = account_ids or client.child_accounts()
    query = (
        "SELECT local_services_lead.id, local_services_lead.lead_type, "
        "local_services_lead.contact_details, local_services_lead.lead_status, "
        "local_services_lead.creation_date_time FROM local_services_lead "
        f"WHERE local_services_lead.creation_date_time >= '{since}'"
    )
    touches: list[Touch] = []
    for cid in accounts:
        try:
            rows = client.search(cid, query)
        except LSAError:
            continue  # one account failing shouldn't kill the rest
        for row in rows:
            t = lead_to_touch(row.get("localServicesLead", {}), source)
            if t:
                touches.append(t)
    return touches


def client_from_settings(s: Any) -> GoogleAdsClient:
    return GoogleAdsClient(
        s.google_ads_developer_token, s.google_ads_login_customer_id,
        s.google_ads_client_id, s.google_ads_client_secret,
        s.google_ads_refresh_token, s.google_ads_api_version)
