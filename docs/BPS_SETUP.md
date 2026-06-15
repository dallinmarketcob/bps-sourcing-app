# BPS Lead-Attribution — Onboarding on Your Own Machine

This is the **Brooks Pest Solutions** instance of the lead-source attribution
engine — fully independent from Brooks Pest Control (its own repo, its own
accounts, its own PestRoutes, its own server). The attribution model is
identical; everything BPS-specific lives in `.env`, `source_maps/`, and
`secrets/` (all gitignored — they never come through git).

For the full phased rollout plan, see [`BPS_ONBOARDING_PLAN.md`](BPS_ONBOARDING_PLAN.md).
This file is just "get it running on a machine."

---

## 0. Prerequisites

- **Python 3.12** (the engine targets 3.12).
- **git**.
- Read access to this private repo: `github.com/dallinmarketcob/bps-sourcing-app`.
- Credentials/accounts as they come in (see `.env.example` and the plan). You can
  set up and run tests with **no credentials** — channels just stay idle until
  their keys are filled.

---

## 1. Clone

```bash
git clone https://github.com/dallinmarketcob/bps-sourcing-app.git
cd bps-sourcing-app
```

## 2. Python environment + dependencies

**Windows (PowerShell):**
```powershell
python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux:**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 3. Create your `.env`

```bash
cp .env.example .env      # Windows: Copy-Item .env.example .env
```

Then open `.env` and fill it in. **Read the header in that file** — every
business-specific key is listed explicitly on purpose, because the code defaults
are Brooks Pest *Control's* values. Leave `DRY_RUN=true` until BPS has signed off
on a dry-run audit. Fill keys as the inventory arrives; blanks are safe (that
channel just won't run).

## 4. Verify the install

```bash
python -m pytest -q          # expect 60 passing
```

If 60 tests pass, the engine is sound on this machine. Everything past here is
wiring up BPS's accounts and lookup maps.

---

## 5. Connect the channels (as credentials arrive)

Each channel is independent — do them in any order, in parallel. Details and the
gotchas-learned-the-hard-way are in the plan; the scripts are:

| Channel | Auth / setup | Verify |
|---|---|---|
| **PestRoutes** | Put subdomain + global key/token in `.env` | `python scripts/probe_offices.py` (lists offices → set `PESTROUTES_OFFICE_IDS`) |
| **Source picklist** | (after PestRoutes) | `python scripts/build_source_id_map.py` → writes `data/pestroutes_source_inventory.json`; **run the duplicate-label audit** (see plan Phase 2) |
| **Gmail** | `python scripts/gmail_auth.py` (one-time browser OAuth on BPS's lead inbox; token → `secrets/`) | `python scripts/gmail_new_senders.py` to discover lead senders |
| **Meta** | `python scripts/meta_page_token.py` (mints a non-expiring Page token; set `META_*` in `.env`) | `python scripts/meta_probe.py` |
| **Genesys** | OAuth client-creds + region in `.env` | `python scripts/genesys_probe.py` (discover queue IDs → `GENESYS_INSIDE_SALES_QUEUE_ID`) |
| **LSA / Google Ads** | `python scripts/google_ads_auth.py` (OAuth refresh token) | `python scripts/lsa_probe.py` |

## 6. Build BPS's source maps

These two files are the heart of the system and are BPS-specific — they are **not**
in git (gitignored). Create them under `source_maps/`:

- **`source_maps/sourcing_master.csv`** — the master sheet. Columns:
  `Pestroutes Source, Provider / Channel, DNIS`. One row per tracking number.
  This resolves **phone** leads (Genesys DNIS → source). Templates to copy from:
  `source_maps/example_tracking_numbers.csv`.
- **`source_maps/email_providers.csv`** — domain/keyword → Source N rules that
  resolve **form** leads (Gmail). Template: `source_maps/example_email_providers.csv`.
  Use `scripts/eforce_domain_audit.py` and `scripts/unmatched_email_audit.py` to
  build and tighten it.

Every master-sheet source must resolve to a PestRoutes sourceID (run the
reconciliation in the plan, Phase 2). Keep the two lookup files in sync as new
tracking numbers / website domains appear.

---

## 7. First dry-run (safe — writes nothing)

```bash
# Pull touches into the local store, then attribute recent sales (no writes):
python scripts/ingest_touches.py 7
python scripts/audit_report.py 14        # review CSV in data/
```

Review the CSV with BPS. Most mismatches are **map gaps** (a missing DNIS,
domain, or queue), not engine bugs — fix the maps, re-ingest, re-run.

---

## 8. Deploy to BPS's server (when ready)

The engine runs nightly on a small Linux VM (the plan recommends DigitalOcean
**Ubuntu 24.04, ≥1 GB RAM** — 512 MB OOMs on backfills).

1. On the server: `git clone` this repo to `/opt/lead-attribution`.
2. Create `/opt/lead-attribution/.env` (copy from `.env.example`, fill in; keep
   `DRY_RUN=true`). Copy the BPS `source_maps/*.csv` and `secrets/` up too (these
   aren't in git). **Never** copy a whole local `.env` over a server `.env` — it
   reverts `DRY_RUN`/creds; edit specific lines.
3. `bash deploy/setup.sh` — installs venv + deps, sets the timezone
   (`America/Los_Angeles` by default — **edit `deploy/setup.sh` if BPS ops are in
   another timezone**), runs the tests, does a 1-day smoke ingest, and installs
   cron (nightly run 3am + weekly dispute Mon 7am).
4. Deploy updates later with `git pull` on the server (then re-run `setup.sh` if
   deps changed). `.env`, `secrets/`, `data/`, and `source_maps/*.csv` are
   gitignored, so a pull never touches them.

**Go-live:** only after BPS signs off on a dry-run audit, flip `DRY_RUN=false` in
the server `.env`. Verify the first live night writes cleanly, then run the
historical re-source sweep (plan Phase 5).

---

## Ops rules that matter (learned the hard way)

- Keep `DRY_RUN=true` until BPS signs off. It gates every write.
- Long server jobs: run with `nohup … & disown` + a `.done` sentinel and poll —
  SSH sessions drop frequently.
- Never `pkill -f <script>` over SSH (it matches your own session) — kill by PID.
- Never `scp` a whole local `.env` onto the server (reverts `DRY_RUN`/creds).
- The credit rule has **no age cutoff** — keep `LOOKBACK_DAYS=365`.
