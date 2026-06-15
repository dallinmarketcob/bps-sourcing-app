# Lead-Source Attribution Engine — Brooks Pest Solutions (BPS)

Batch engine for **Brooks Pest Solutions** (independent instance; identical model
to the Brooks Pest Control engine). Every morning it pulls sold subscriptions
from PestRoutes, gathers lead touches across Meta, Gmail form leads, Genesys
calls, and Google LSA, decides which **source** earned each sale, and writes it
back. A weekly **dispute report** flags pay-per-lead duplicates.

**→ To onboard on your own machine, start with [`docs/BPS_SETUP.md`](docs/BPS_SETUP.md).**
Full rollout plan: [`docs/BPS_ONBOARDING_PLAN.md`](docs/BPS_ONBOARDING_PLAN.md).

## How attribution works

Every stream carries a phone number; normalized to E.164, that's the join key.
For each sale:

1. **Matching cascade** — find touches by `phone1`, then `phone2`, then `email`
   (handles the landlord/tenant case where the number on file is never a lead).
2. **Streaks** — order touches in time, split wherever the gap exceeds the stale
   window (BPS uses 7 days, set in `.env`). A cold gap resets the clock.
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

## Status (BPS instance)

The engine is complete and proven on the Brooks instance (60 tests passing).
BPS onboarding is **configuration + lookup-map work**, not a rebuild — track it
against [`docs/BPS_ONBOARDING_PLAN.md`](docs/BPS_ONBOARDING_PLAN.md):

- [ ] Credentials + accounts inventoried (`.env` filled)
- [ ] Source picklist pulled + duplicate-label audit done
- [ ] Master sheet + `email_providers.csv` built (BPS-specific)
- [ ] Channels probed (PestRoutes, Gmail, Meta, Genesys, LSA)
- [ ] Backfill + dry-run audit signed off by BPS
- [ ] Live write-back enabled on BPS's server
