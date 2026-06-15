"""Gmail form-lead reader.

Turns a raw form-lead email into a normalized ``Touch``:
  raw email -> ParsedEmail -> (source via alias table) + (phone/email extraction).

The parsing is fully testable offline (see ``build_touch_from_email``); the live
Gmail API fetch (``GmailReader``) is a thin layer that hands raw messages to the
same path. Google libraries are imported lazily so this module loads without
OAuth configured.
"""
from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Channel, Touch
from .email_parse import ParsedEmail, extract_emails, extract_phones, parse_email
from .email_providers import ProviderRule, identify_source, load_email_providers
from .source_maps import SourceMap


@dataclass
class EmailLeadResult:
    """Outcome of parsing one email, with diagnostics for review."""

    parsed: ParsedEmail
    source: str | None = None
    reason: str = ""
    phones: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    touch: Touch | None = None

    @property
    def ok(self) -> bool:
        return self.touch is not None

    @property
    def problem(self) -> str | None:
        if self.source is None:
            return "unidentified source"
        if not self.phones and not self.emails:
            return "no phone or email found"
        return None


def build_touch_from_email(
    raw: bytes | str,
    rules: list[ProviderRule],
    source_map: SourceMap | None = None,
    raw_ref: str | None = None,
) -> EmailLeadResult:
    """Parse one raw email into an EmailLeadResult (Touch if usable)."""
    parsed = parse_email(raw, raw_ref=raw_ref)
    source, reason = identify_source(parsed, rules, source_map)

    exclude = {parsed.from_domain} if parsed.from_domain else set()
    phones = extract_phones(parsed.text)
    emails = extract_emails(parsed.text, exclude_domains=exclude)

    result = EmailLeadResult(
        parsed=parsed, source=source, reason=reason, phones=phones, emails=emails
    )

    # A usable touch needs a source AND at least one contact key.
    if source and (phones or emails):
        result.touch = Touch(
            channel=Channel.GMAIL,
            source=source,
            occurred_at=parsed.date,
            phone_e164=phones[0] if phones else None,
            email=emails[0] if emails else None,
            raw_ref=raw_ref or parsed.raw_ref,
        )
    return result


def load_provider_rules(source_maps_dir: Path) -> list[ProviderRule]:
    """Load the email provider alias table from the source-maps folder."""
    return load_email_providers(Path(source_maps_dir) / "email_providers.csv")


# --------------------------------------------------------------------------
# Live Gmail fetch (skeleton) — completed after OAuth is set up + samples seen.
# --------------------------------------------------------------------------
class GmailReader:
    """Fetch raw form-lead messages from Gmail and parse them to Touches.

    Requires OAuth: a downloaded client-secret JSON and a stored token. Google
    libraries are imported lazily inside methods so importing this module never
    requires them.
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    def __init__(self, credentials_file: Path, token_file: Path, label: str = ""):
        self.credentials_file = Path(credentials_file)
        self.token_file = Path(token_file)
        self.label = label

    def authorize(self) -> str:  # pragma: no cover - interactive OAuth
        """Run the one-time consent flow (opens a browser) and save the token.

        Returns the authorized email address. Run this once, interactively.
        """
        service = self._service(allow_interactive=True)
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "")

    def _service(self, allow_interactive: bool = False):  # pragma: no cover
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = None
        if self.token_file.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_file), self.SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            elif allow_interactive:
                from google_auth_oauthlib.flow import InstalledAppFlow

                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_file), self.SCOPES
                )
                # Don't auto-open a browser (unreliable from a background run).
                # Print the URL so it can be opened manually; the local server
                # still catches the redirect.
                creds = flow.run_local_server(
                    port=0,
                    open_browser=False,
                    authorization_prompt_message=(
                        "\n>>> OPEN THIS URL IN YOUR BROWSER TO AUTHORIZE:\n{url}\n"
                    ),
                )
            else:
                raise RuntimeError(
                    f"No valid Gmail token at {self.token_file}. "
                    f"Run scripts/gmail_auth.py once to authorize."
                )
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(creds.to_json(), encoding="utf-8")
        return build("gmail", "v1", credentials=creds)

    def fetch_raw_messages(
        self, query: str, max_results: int = 200
    ) -> Iterator[tuple[str, bytes]]:  # pragma: no cover - needs live OAuth
        """Yield ``(message_id, raw_bytes)`` for messages matching a Gmail query.

        ``query`` is Gmail search syntax, e.g. ``"newer_than:7d"`` or
        ``"label:form-leads newer_than:30d"``.
        """
        service = self._service()
        page_token = None
        fetched = 0
        while fetched < max_results:
            resp = (
                service.users()
                .messages()
                .list(userId="me", q=query, pageToken=page_token,
                      maxResults=min(100, max_results - fetched))
                .execute()
            )
            for m in resp.get("messages", []):
                # A transient Google error on ONE message must not kill the whole
                # channel: retry a few times, then skip that message and move on.
                raw = None
                for attempt in range(3):
                    try:
                        full = (
                            service.users().messages()
                            .get(userId="me", id=m["id"], format="raw")
                            .execute()
                        )
                        raw = base64.urlsafe_b64decode(full["raw"].encode("ascii"))
                        break
                    except Exception:
                        if attempt < 2:
                            time.sleep(1.5 * (attempt + 1))
                if raw is None:
                    continue  # skip messages that keep failing
                yield m["id"], raw
                fetched += 1
                if fetched >= max_results:
                    return
            page_token = resp.get("nextPageToken")
            if not page_token:
                return

    def list_senders(self, query: str, max_results: int = 1500) -> list[str]:  # pragma: no cover
        """Return the From header of recent messages (metadata only = fast).
        Used to detect lead senders we're not yet pulling."""
        service = self._service()
        page_token = None
        fetched = 0
        out: list[str] = []
        while fetched < max_results:
            resp = (
                service.users().messages()
                .list(userId="me", q=query, pageToken=page_token,
                      maxResults=min(100, max_results - fetched))
                .execute()
            )
            for m in resp.get("messages", []):
                md = (
                    service.users().messages()
                    .get(userId="me", id=m["id"], format="metadata",
                         metadataHeaders=["From"])
                    .execute()
                )
                headers = {h["name"]: h["value"] for h in md.get("payload", {}).get("headers", [])}
                out.append(headers.get("From", ""))
                fetched += 1
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    def fetch_lead_results(
        self,
        query: str,
        rules: list[ProviderRule],
        source_map: SourceMap | None = None,
        max_results: int = 200,
    ) -> list[EmailLeadResult]:  # pragma: no cover - needs live OAuth
        """Fetch + parse recent messages into EmailLeadResults."""
        out: list[EmailLeadResult] = []
        for msg_id, raw in self.fetch_raw_messages(query, max_results=max_results):
            out.append(build_touch_from_email(raw, rules, source_map, raw_ref=msg_id))
        return out
