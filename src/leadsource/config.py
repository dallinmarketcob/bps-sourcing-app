"""Configuration & secrets, loaded from environment / .env.

Every tunable the engine needs lives here so behavior can change without code
edits. Secrets are read from env vars (or a cloud secret store in production)
and never committed.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- PestRoutes / FieldRoutes ---
    pestroutes_base_url: str = ""
    pestroutes_auth_key: str = ""
    pestroutes_auth_token: str = ""
    # Global key, but subscription SEARCH must be polled one office at a time.
    pestroutes_office_ids: str = "1,2,3,4,5,6,8,9,10"

    @property
    def office_id_list(self) -> list[str]:
        return [o.strip() for o in self.pestroutes_office_ids.split(",") if o.strip()]

    # --- Meta / Facebook Ads ---
    meta_access_token: str = ""
    meta_ad_account_id: str = ""
    meta_page_id: str = "472946656425133"   # Brooks Pest Control page
    meta_lead_source: str = "Source 144"     # all Meta instant-form leads -> Source 144
    website_form_source: str = "Source 55"   # main brookspest.com website form
    # When a Meta lead and the website form land within this many minutes, the
    # Meta ad wins (it drove them to the site); the form email lags anyway.
    meta_form_tiebreak_minutes: float = 2

    # --- Genesys Cloud ---
    genesys_region: str = "mypurecloud.com"
    genesys_client_id: str = ""
    genesys_client_secret: str = ""
    # One or more queue IDs to ingest, comma-separated (Inside Sales + Inside
    # Sales Spanish). Customer-service queues stay excluded by design.
    genesys_inside_sales_queue_id: str = ""

    @property
    def genesys_queue_ids(self) -> list[str]:
        return [q.strip() for q in self.genesys_inside_sales_queue_id.split(",") if q.strip()]

    # --- Gmail ---
    gmail_credentials_file: Path = Path("secrets/credentials.json")
    gmail_token_file: Path = Path("secrets/token.json")
    gmail_label: str = "form-leads"
    # Senders/domains that send form leads. The inbox is too busy to skim the
    # last N messages, so we query Gmail for mail FROM these senders only.
    gmail_lead_senders: str = (
        "pestnet.com,dolead.com,baton.app,brookspest.com,multiscreensite.com,"
        "goodzer.com,electgenmarketing@gmail.com,localservices-noreply@google.com,"
        "zapiermail.com"  # DoLead (and others) relay leads through Zapier
    )

    @property
    def gmail_lead_query_prefix(self) -> str:
        """Gmail search 'from:(...)' clause built from the lead senders."""
        senders = [s.strip() for s in self.gmail_lead_senders.split(",") if s.strip()]
        return "(" + " OR ".join(f"from:{s}" for s in senders) + ")" if senders else ""

    # --- Google Ads / Local Services Ads (LSA) ---
    google_ads_developer_token: str = ""
    google_ads_login_customer_id: str = ""   # manager (MCC) id, digits only
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_api_version: str = "v21"
    lsa_source: str = "Source 145"           # all LSA leads -> Source 145 (one bucket)

    # --- Engine tunables ---
    stale_window_days: int = 30
    same_day_cluster_hours: int = 24
    # How far back the nightly loads touches for attribution. Must be long —
    # the credit rule has NO age cutoff, so old-but-unsuperseded leads count.
    lookback_days: int = 365
    source_maps_dir: Path = Path("source_maps")
    master_sheet: Path = Path("source_maps/sourcing_master.csv")  # the DNIS/source sheet
    db_path: Path = Path("data/attribution.sqlite")
    dry_run: bool = True

    pay_per_lead_providers: str = ""

    # Internal/natural-growth/process sources that must NEVER be overwritten.
    # Comma-separated source names (matched case-insensitively).
    protected_sources: str = (
        "Door to Door,Clark Door to Door,Additional Property,Customer Referral,"
        "Technician Referral,Customer Service Referral,Refer a Friend Campaign,"
        "Upsell,Mosquito upsell Campaign,Outbound,Saw our truck,"
        "Reactivation Email Campaign,Email Campaign,Inside Sales Renewal,"
        "Renewal - In Contract,Renewal - No Contract,Renewal - Out of Contract,"
        "Rate Raise Renewal,ZOLD - Renewal,Bad Debt Renewal,Conditions,SNS Campaign"
    )

    @property
    def pay_per_lead_provider_set(self) -> set[str]:
        """Normalized set of provider names flagged as pay-per-lead."""
        return {
            p.strip().lower()
            for p in self.pay_per_lead_providers.split(",")
            if p.strip()
        }

    @property
    def protected_source_set(self) -> frozenset[str]:
        """Lowercased set of sources that must never be overwritten."""
        return frozenset(
            p.strip().lower() for p in self.protected_sources.split(",") if p.strip()
        )


def load_settings() -> Settings:
    """Load settings from environment / .env."""
    return Settings()
