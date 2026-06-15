"""Load the sourcing master sheet(s).

Schema (as of 2026-06-08):
    Pestroutes Source , Provider / Channel , DNIS
    e.g.  "Source 1"   , "Aragon Fresno"    , "628-232-0597"

- **Pestroutes Source** (col A) is the CANONICAL source key — it matches a
  PestRoutes picklist label and resolves to a numeric ``sourceID`` for write-back.
- **Provider / Channel** (col B) is the human-friendly name, used for reports and
  for matching Gmail/Meta leads that name their provider.
- **DNIS** (col C) is the tracking number a phone lead dialed (Genesys).

The loader normalizes DNIS to E.164 and builds three lookups:
    dnis -> canonical source, canonical source -> provider, provider -> canonical.
It collects warnings instead of failing on messy rows.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..normalize import normalize_phone


def _norm_provider(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


@dataclass
class SourceMap:
    dnis_to_source: dict[str, str] = field(default_factory=dict)       # E.164 -> "Source N"
    source_to_provider: dict[str, str] = field(default_factory=dict)   # "Source N" -> provider
    provider_to_source: dict[str, str] = field(default_factory=dict)   # norm provider -> "Source N"
    warnings: list[str] = field(default_factory=list)

    def source_for_dnis(self, raw_number: str | None) -> str | None:
        """Resolve a dialed (DNIS) number to its canonical 'Source N'."""
        return self.dnis_to_source.get(normalize_phone(raw_number) or "")

    def source_for_provider(self, provider_name: str | None) -> str | None:
        """Resolve a provider/channel name to its canonical 'Source N'."""
        if not provider_name:
            return None
        return self.provider_to_source.get(_norm_provider(provider_name))

    def provider_for_source(self, source: str | None) -> str | None:
        return self.source_to_provider.get(source or "")

    def merge(self, other: "SourceMap") -> None:
        for e164, src in other.dnis_to_source.items():
            existing = self.dnis_to_source.get(e164)
            if existing and existing != src:
                self.warnings.append(
                    f"DNIS {e164} maps to both '{existing}' and '{src}'; kept '{existing}'."
                )
                continue
            self.dnis_to_source[e164] = src
        self.source_to_provider.update(other.source_to_provider)
        self.provider_to_source.update(other.provider_to_source)
        self.warnings.extend(other.warnings)


def _find_column(headers: list[str], *needles: str) -> str | None:
    for h in headers:
        low = h.strip().lower()
        if any(n in low for n in needles):
            return h
    return None


def load_source_map_csv(path: Path) -> SourceMap:
    """Load one sourcing CSV into a SourceMap."""
    result = SourceMap()
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        # Canonical source = the "Pestroutes Source" column; prefer that exact
        # match, else any column mentioning "source".
        src_col = _find_column(headers, "pestroutes") or _find_column(headers, "source")
        provider_col = _find_column(headers, "provider", "channel")
        dnis_col = _find_column(headers, "dnis", "tracking", "number")
        if not src_col or not dnis_col:
            result.warnings.append(
                f"{path.name}: missing source and/or DNIS columns in {headers}."
            )
            return result

        for lineno, row in enumerate(reader, start=2):
            source = (row.get(src_col) or "").strip()
            provider = (row.get(provider_col) or "").strip() if provider_col else ""
            raw_dnis = (row.get(dnis_col) or "").strip()

            if not source:
                if raw_dnis:
                    result.warnings.append(
                        f"{path.name}:{lineno}: DNIS '{raw_dnis}' has a blank "
                        f"Pestroutes Source - needs a label."
                    )
                continue

            # Provider <-> canonical source (works even with no DNIS, e.g. web forms).
            result.source_to_provider.setdefault(source, provider)
            if provider:
                result.provider_to_source.setdefault(_norm_provider(provider), source)

            if not raw_dnis:
                continue
            e164 = normalize_phone(raw_dnis)
            if not e164:
                result.warnings.append(
                    f"{path.name}:{lineno}: unparseable DNIS '{raw_dnis}' "
                    f"(source '{source}')."
                )
                continue
            existing = result.dnis_to_source.get(e164)
            if existing and existing != source:
                result.warnings.append(
                    f"{path.name}:{lineno}: DNIS {e164} already mapped to "
                    f"'{existing}', also '{source}'; kept '{existing}'."
                )
                continue
            result.dnis_to_source[e164] = source
    return result


def load_source_maps(folder: Path) -> SourceMap:
    """Load and merge every non-example CSV in a folder."""
    combined = SourceMap()
    for path in sorted(Path(folder).glob("*.csv")):
        # Skip examples and the email-provider alias table (different schema).
        if path.name.startswith("example_") or path.name.startswith("email_providers"):
            continue
        combined.merge(load_source_map_csv(path))
    return combined
