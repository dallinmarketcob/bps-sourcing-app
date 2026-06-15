"""Confirm credentials are present WITHOUT printing their values."""
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from leadsource.config import load_settings  # noqa: E402

s = load_settings()


def present(v: str) -> str:
    return f"set ({len(v)} chars)" if v else "MISSING"


host = urlparse(s.pestroutes_base_url).netloc or "(none)"
print("PestRoutes base URL host:", host or "MISSING")
print("PestRoutes auth key:     ", present(s.pestroutes_auth_key))
print("PestRoutes auth token:   ", present(s.pestroutes_auth_token))
