"""One-time Gmail authorization. RUN THIS YOURSELF (it opens a browser).

Prereq: a Google OAuth client-secret JSON saved at the path in .env
(GMAIL_CREDENTIALS_FILE, default secrets/credentials.json). See the setup steps
your assistant provided. This saves a token to GMAIL_TOKEN_FILE so later pulls
need no browser.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from leadsource.config import load_settings  # noqa: E402
from leadsource.readers.gmail import GmailReader  # noqa: E402


def main():
    s = load_settings()
    creds = Path(s.gmail_credentials_file)
    if not creds.exists():
        print(f"Missing OAuth client secret at: {creds.resolve()}")
        print("Download it from Google Cloud Console (Desktop app OAuth client) "
              "and save it there, then re-run.")
        return
    reader = GmailReader(s.gmail_credentials_file, s.gmail_token_file, s.gmail_label)
    print("Opening browser for Google consent...")
    email = reader.authorize()
    print(f"\nAuthorized as: {email}")
    print(f"Token saved to: {Path(s.gmail_token_file).resolve()}")
    print("You can close the browser. Tell your assistant it's done.")


if __name__ == "__main__":
    main()
