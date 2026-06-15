# Lead-Source Attribution Engine

Batch engine for Brooks Pest Control. Every morning it pulls sold subscriptions
from PestRoutes, gathers lead touches across Meta, Gmail form leads, and Genesys
calls, decides which **source** earned each sale, and writes it back. A weekly
**dispute report** flags pay-per-lead duplicates. Full plan in
`.claude/plans/eventual-finding-seal.md`.

## How attribution works

Every stream carries a phone number; normalized to E.164, that's the join key.
For each sale:

1. **Matching cascade** — find touches by `phone1`, then `phone2`, then `email`
   (handles the landlord/tenant case where the number on file is never a lead).
2. **Streaks** — order touches in time, split wherever the gap exceeds the stale
   window (default 30 days, configurable). A cold gap resets the clock.
3. **Credit** — the sale closes in its most recent streak; the **earliest**
   source in that streak wins. Same-day cluster → first one there. Revived after
   a stale gap → the reviver wins.

## Layout

```
src/leadsource/
  config.py        settings & secrets (env / .env)
  normalize.py     phone (E.164) + email join-key normalization
  models.py        Touch, Subscription, AttributionResult
  attribution.py   the brain (pure logic) — fully unit-tested
  readers/         PestRoutes, Meta, Gmail, Genesys, CSV source maps  (Phase 0+)
source_maps/       CSV lookups: tracking number / id -> source
tests/             unit tests for normalize + attribution
```

## Setup

```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then fill in credentials
python -m pytest -q           # run the test suite
```

## Status

- [x] Core: config, normalization, models, attribution brain + tests (15 passing)
- [ ] Phase 0: live readers, prove cross-system phone join on real data
- [ ] Phase 1: storage + dry-run review sheet
- [ ] Phase 2: write-back + cloud scheduling
- [ ] Phase 3: weekly dispute report
