"""One-time Google Ads OAuth authorization. RUN THIS YOURSELF (opens a browser).

Prereq: paste GOOGLE_ADS_CLIENT_ID and GOOGLE_ADS_CLIENT_SECRET into .env first
(from a Desktop-app OAuth client in Google Cloud Console). This opens a browser,
you approve with the Google account that has MCC access, and it writes
GOOGLE_ADS_REFRESH_TOKEN back into .env. Works regardless of Basic Access status
(OAuth is separate from the developer token's access level).

Usage:  python scripts/google_ads_auth.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
SCOPES = ["https://www.googleapis.com/auth/adwords"]


def env_values(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def set_env(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, found = [], False
    for ln in lines:
        if ln.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(ln)
    if not found:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> int:
    cfg = env_values(ENV)
    cid = cfg.get("GOOGLE_ADS_CLIENT_ID", "").strip()
    csec = cfg.get("GOOGLE_ADS_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        print("Missing GOOGLE_ADS_CLIENT_ID / GOOGLE_ADS_CLIENT_SECRET in .env.")
        print("Create a Desktop-app OAuth client in Google Cloud Console, paste both, re-run.")
        return 1

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("google-auth-oauthlib not installed. Run: pip install google-auth-oauthlib")
        return 1

    client_config = {"installed": {
        "client_id": cid, "client_secret": csec,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }}
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    print("Opening browser for Google consent (approve with your MCC-access account)...")
    try:
        creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    except Exception as e:  # fall back to copy/paste console flow
        print(f"(local browser flow unavailable: {e}) — using manual code flow.")
        creds = flow.run_console(access_type="offline", prompt="consent")

    if not creds.refresh_token:
        print("No refresh token returned. Re-run; ensure you fully approved consent.")
        return 1
    set_env(ENV, "GOOGLE_ADS_REFRESH_TOKEN", creds.refresh_token)
    print(f"\nSuccess. Refresh token written to {ENV}")
    print("Tell your assistant it's done — that's the last LSA credential.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
