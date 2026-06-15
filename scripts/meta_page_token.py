"""Mint a PERMANENT Meta Page access token from a short-lived Graph-Explorer
user token, and write it into .env as META_ACCESS_TOKEN.

Why: `leads_retrieval` isn't part of the app's "Create & manage ads" use case,
so the System User token can't carry it. But Meta lets you grant
`leads_retrieval` for your OWN Page via the Graph API Explorer (development
mode, no App Review). The Explorer token is short-lived (~1h) -- so we exchange
it for a long-lived user token, then derive the Page token, which DOES NOT
EXPIRE when it comes from a long-lived user token.

Put these in .env (NOT in chat), then run this script:
    META_APP_ID=...                 # from App Dashboard -> Settings -> Basic
    META_APP_SECRET=...             # same page, click "Show"
    META_SHORT_USER_TOKEN=...       # the token from Graph API Explorer
    META_PAGE_ID=472946656425133    # already set; Brooks Pest Control page

Usage:  python scripts/meta_page_token.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
API = "https://graph.facebook.com/v21.0"


def dotenv_values(path: Path) -> dict[str, str]:
    """Minimal .env reader (KEY=value, ignores blanks/comments)."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _mask(t: str) -> str:
    return f"{t[:6]}...{t[-4:]} ({len(t)} chars)" if t else "(empty)"


def _set_env_var(path: Path, key: str, value: str) -> None:
    """Replace or append KEY=value in .env, preserving everything else."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.strip().startswith(f"{key}=") or ln.strip().startswith(f"{key} ="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    cfg = {**dotenv_values(ENV), **os.environ}
    app_id = cfg.get("META_APP_ID", "").strip()
    app_secret = cfg.get("META_APP_SECRET", "").strip()
    short = cfg.get("META_SHORT_USER_TOKEN", "").strip()
    page_id = cfg.get("META_PAGE_ID", "472946656425133").strip()

    missing = [k for k, v in {
        "META_APP_ID": app_id, "META_APP_SECRET": app_secret,
        "META_SHORT_USER_TOKEN": short}.items() if not v]
    if missing:
        print("Missing in .env: " + ", ".join(missing))
        print("Add them (see this file's docstring) and re-run.")
        return 1

    with httpx.Client(timeout=30) as h:
        # 1) short-lived user token -> long-lived user token (~60 days)
        r = h.get(f"{API}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": app_id,
            "client_secret": app_secret, "fb_exchange_token": short})
        if r.status_code >= 400:
            print(f"[1] exchange failed HTTP {r.status_code}: "
                  f"{r.text.replace(short, '<redacted>')[:300]}")
            return 1
        long_user = r.json()["access_token"]
        print(f"[1] long-lived user token: {_mask(long_user)}")

        # 2) ask the Page node directly for its access_token. (me/accounts is
        # empty for business-owned "new Pages experience" pages, but the page
        # node still hands back a token derived from our long-lived user token,
        # which therefore does not expire.)
        r = h.get(f"{API}/{page_id}",
                  params={"fields": "id,name,access_token", "access_token": long_user})
        if r.status_code >= 400:
            print(f"[2] page {page_id} lookup failed HTTP {r.status_code}: {r.text[:300]}")
            return 1
        page = r.json()
        page_token = page.get("access_token")
        if not page_token:
            print(f"[2] page {page_id} returned no access_token: {str(page)[:200]}")
            return 1
        print(f"[2] page token for {page.get('name')!r}: {_mask(page_token)}")

        # 3) verify leads_retrieval actually works
        r = h.get(f"{API}/{page_id}/leadgen_forms",
                  params={"fields": "id,name,leads_count", "access_token": page_token})
        if r.status_code >= 400:
            print(f"[3] lead-form read FAILED HTTP {r.status_code}: {r.text[:300]}")
            print("    -> the Explorer token probably lacked leads_retrieval; "
                  "regenerate it with that box checked.")
            return 1
        forms = r.json().get("data", [])
        total = sum(int(f.get("leads_count") or 0) for f in forms)
        print(f"[3] OK -- {len(forms)} lead forms visible, {total} leads total. "
              "leads_retrieval works.")

    _set_env_var(ENV, "META_ACCESS_TOKEN", page_token)
    print(f"\n[4] wrote META_ACCESS_TOKEN to {ENV}")
    print("    You can now delete META_SHORT_USER_TOKEN / META_APP_SECRET from .env.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
