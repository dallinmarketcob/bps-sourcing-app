"""Meta (Facebook/Instagram) Lead Ads reader.

Meta instant-form leads don't pass through Gmail (they're delivered via the
Marketing API / Zapier), so we pull them directly from the Graph API:

  token -> managed Pages -> each Page's leadgen_forms -> each form's leads.

Each lead's ``field_data`` carries the phone/email/name. The form (or its
campaign) maps to a Source N -- mapping built from real data, like the Gmail
provider table.

Auth: a Page or User access token with ``leads_retrieval`` +
``pages_show_list``/``pages_read_engagement``. Tokens are sent as a query param
and never logged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import Channel, Touch
from ..normalize import as_naive_utc, normalize_email, normalize_phone


class MetaError(RuntimeError):
    pass


class MetaClient:
    def __init__(self, access_token: str, api_version: str = "v21.0", timeout: float = 30.0):
        if not access_token:
            raise MetaError("Missing Meta access token.")
        self._token = access_token
        self.base = f"https://graph.facebook.com/{api_version}"
        self._http = httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return "MetaClient(...)"

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MetaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get(self, path: str, params: dict[str, Any] | None = None, token: str | None = None) -> Any:
        q = {"access_token": token or self._token, **(params or {})}
        try:
            resp = self._http.get(f"{self.base}/{path.lstrip('/')}", params=q)
        except httpx.HTTPError as e:
            raise MetaError(f"GET {path} failed: {e}") from e
        if resp.status_code >= 400:
            # Strip the echoed token from any error text.
            text = resp.text.replace(token or self._token, "<redacted>")[:300]
            raise MetaError(f"GET {path} -> HTTP {resp.status_code}: {text}")
        return resp.json()

    def paged(self, path: str, params: dict[str, Any] | None = None, token: str | None = None):
        """Yield items across Graph API pages."""
        data = self.get(path, params, token)
        while True:
            for item in data.get("data", []):
                yield item
            nxt = (data.get("paging") or {}).get("next")
            if not nxt:
                return
            # Follow the absolute 'next' URL (already has token + cursor).
            resp = self._http.get(nxt)
            if resp.status_code >= 400:
                return
            data = resp.json()

    # --- identity / structure ----------------------------------------------
    def me(self) -> dict:
        return self.get("me", {"fields": "id,name"})

    def list_pages(self) -> list[dict]:
        """Pages the token can manage, each with its own page access_token."""
        return list(self.paged("me/accounts", {"fields": "id,name,access_token"}))

    def list_lead_forms(self, page_id: str, page_token: str) -> list[dict]:
        return list(self.paged(
            f"{page_id}/leadgen_forms", {"fields": "id,name,status"}, token=page_token
        ))

    def get_leads(self, form_id: str, page_token: str, since_unix: int | None = None) -> list[dict]:
        params: dict[str, Any] = {"fields": "id,created_time,field_data,campaign_name,ad_name"}
        if since_unix:
            params["filtering"] = (
                f'[{{"field":"time_created","operator":"GREATER_THAN","value":{since_unix}}}]'
            )
        return list(self.paged(f"{form_id}/leads", params, token=page_token))


def _field(lead: dict, *names: str) -> str | None:
    wanted = {n.lower() for n in names}
    for f in lead.get("field_data", []):
        if (f.get("name") or "").lower() in wanted:
            vals = f.get("values") or []
            if vals:
                return vals[0]
    return None


def pull_lead_touches(
    client: "MetaClient",
    page_id: str,
    source: str,
    since_unix: int | None = None,
) -> list[Touch]:
    """Pull recent leads from a page's lead forms -> Touches (all credited to
    ``source``). ``since_unix`` limits to leads created after that time."""
    page_token = client.get(page_id, {"fields": "access_token"}).get("access_token")
    forms = list(client.paged(
        f"{page_id}/leadgen_forms", {"fields": "id,leads_count"}, token=page_token
    ))
    touches: list[Touch] = []
    for f in forms:
        if not f.get("leads_count"):
            continue
        for lead in client.get_leads(f["id"], page_token, since_unix):
            t = lead_to_touch(lead, source)
            if t:
                touches.append(t)
    return touches


def lead_to_touch(lead: dict, source: str) -> Touch | None:
    """Build a Touch from a Meta lead, given its resolved canonical source."""
    phone = normalize_phone(_field(lead, "phone_number", "phone", "work_phone_number"))
    email = normalize_email(_field(lead, "email", "work_email"))
    if not phone and not email:
        return None
    when = None
    ct = lead.get("created_time")
    if ct:
        try:
            when = as_naive_utc(datetime.fromisoformat(ct.replace("Z", "+00:00")))
        except ValueError:
            when = None
    return Touch(
        channel=Channel.META,
        source=source,
        occurred_at=when,
        phone_e164=phone,
        email=email,
        raw_ref=lead.get("id"),
    )
