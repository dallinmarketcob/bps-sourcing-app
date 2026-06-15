# Brooks Pest Solutions — Lead Attribution Onboarding Plan & Scope

**Goal:** stand up an independent copy of the Brooks Pest Control lead-source attribution
engine for Brooks Pest Solutions (BPS), on their own droplet, against their own accounts,
writing to their own PestRoutes — with the **identical attribution model** and all the
hardening lessons already baked in.

**What stays identical:** the code, the credit rule (first lead to hit the CRM wins; holds
until a new lead arrives ≥7 days later; **no age cutoff**), email-as-fallback matching,
protected-source guard, Meta-beats-near-simultaneous-form tiebreak (2 min), 365-day touch
lookback, nightly cadence, dry-run-first safety, write verification + audit trail.

**What is BPS-specific (the actual work):** every credential, every office ID, every
Source-N → provider → DNIS mapping, every email domain/keyword, the Meta page, the LSA
accounts, the Genesys queues, the protected-source list, and the pay-per-lead provider
list. ~90% of this project is **building BPS's lookup maps correctly** — the code needs
almost nothing.

---

## Phase 0 — Pre-work (do BEFORE touching anything)

### 0.1 Small code tasks on the shared codebase (one-time, benefits both companies)
- [ ] **Parameterize the dispute report.** `scripts/dispute_report.py` hardcodes
      Brooks's pay-per-lead providers (`Elocal/DoLead/Flow Bridge/ElectGen`). Change it to
      read `pay_per_lead_providers` from config (the field already exists, unused).
- [ ] **Put the codebase in git** (private repo). Today Brooks deploys by tar/scp. With
      two companies, un-versioned copies WILL drift — every future bug fix (and we found
      real ones weekly) should be one commit deployed to two droplets, not hand-copied.
      Init repo → push → both droplets `git pull` to deploy. (.env, secrets/, data/,
      source_maps/ stay untracked/per-instance — verify .gitignore covers them.)
- [ ] **Config sweep rule:** BPS's `.env` must set EVERY business-specific key explicitly
      — never rely on code defaults (they're Brooks's values):
      `PESTROUTES_*`, `PESTROUTES_OFFICE_IDS`, `META_PAGE_ID`, `META_LEAD_SOURCE`,
      `WEBSITE_FORM_SOURCE`, `GENESYS_*` (incl. queue IDs), `GMAIL_LEAD_SENDERS`,
      `GOOGLE_ADS_*`, `LSA_SOURCE`, `PROTECTED_SOURCES`, `PAY_PER_LEAD_PROVIDERS`,
      `STALE_WINDOW_DAYS=7`, `LOOKBACK_DAYS=365`, `DRY_RUN=true`.

### 0.2 Credential & account inventory (gather all of it up front)
| System | What's needed | Notes / lead time |
|---|---|---|
| PestRoutes | API authenticationKey + authenticationToken (global), their API subdomain (`<bps>.pestroutes.com/api`) | Confirm key is **global** across their offices |
| Genesys Cloud | OAuth client-credentials (Supervisor role: analytics:conversationDetail:view + routing:queue:view), region | May be the same Genesys org as Brooks or separate — verify |
| Gmail | Their lead inbox login; Google Cloud OAuth Desktop client (can reuse the existing internal app), one-time browser auth | Refresh token doesn't expire (internal app) |
| Meta | Admin on their FB Business + Page; an app with `leads_retrieval` (the standalone "Lead Attribution" app 981711627997334 can likely be reused) | **Do NOT chase a System User token** — use the Graph-Explorer → permanent Page-token path (`scripts/meta_page_token.py`) |
| Google Ads / LSA | Their MCC manager account; developer token + **Basic Access** | ⚠️ **Apply for Basic Access on DAY 1** — it's the only multi-day approval. If BPS LSA accounts sit under the SAME MCC as Brooks, the existing token covers them — check this first, it saves the whole step |
| DigitalOcean | Their account; 1 droplet **Ubuntu 24.04, ≥1 GB RAM** ($6–12/mo); an SSH key | 512 MB is not enough (backfills OOM'd at 1 GB until chunked — don't go smaller) |

### 0.3 The ONE thing only BPS can produce: the Sourcing Master Sheet
This is the heart of the whole system and the longest-lead human task. Hand them the
template now (3 columns, same as Brooks's `source_maps/sourcing_master.csv`):

| Pestroutes Source | Provider / Channel | DNIS |
|---|---|---|
| Source 1 | (provider name) | 555-555-0100 |

Rules learned the hard way:
- **Column A is the canonical key** — the exact PestRoutes source label.
- One row per tracking number; many numbers → one source is fine.
- Include EVERY tracking number that rings their sales queues. Unmapped DNIS = invisible
  leads (we ship an unmapped-DNIS report to catch stragglers, but start complete).
- Non-phone sources (website forms, chat) have no DNIS — they resolve via email domains.
- **Exclude forwarding/overflow numbers** (Brooks lesson: a Podium-overflow number looked
  like a 31-call/3-day "source" — it's re-routed existing leads, not a source).

### 0.4 Decisions BPS must make (bring to the onboarding meeting)
1. **Protected sources** — their list of never-overwrite internal sources (Door to Door,
   renewals, referrals, upsells, additional property, etc.). Pull their picklist and have
   them mark each. Default-protect anything ambiguous.
2. **Pay-per-lead providers** — which providers get the weekly duplicate/dispute report.
3. **Backfill start date** — how far back to ingest lead history (Brooks: Jan 1). Bound by
   Gmail retention and Genesys/Meta/LSA data availability.
4. **Historical re-source sweep scope** — after go-live, which date range of past sales to
   correct (Brooks: ~6 weeks).
5. **Their "Meta leads" source** and **"website form" source** labels in PestRoutes
   (equivalents of Brooks's Source 144 / Source 55) — needed for config + tiebreak.
6. **Genesys queues in scope** — sales queues only; include Spanish/secondary sales queues
   (Brooks missed theirs at first); EXCLUDE customer-service queues.
7. **Go-live gate** — who at BPS audits the dry-run sheet and signs off (Brooks's gate was
   a manual ~50-sale audit + a week of dry-run reports; agreement benchmark ~94%).
8. **Report delivery** — where daily sourcing + weekly dispute CSVs go (droplet pickup vs
   email — email delivery is still an open build item for Brooks too).

---

## Phase 1 — Infrastructure (Day 1, ~half a day)

1. Create the droplet (their DO account): Ubuntu 24.04, ≥1 GB, their SSH key + ours.
2. Deploy code: `git clone` (per 0.1) or tar/scp to `/opt/lead-attribution`.
3. Write BPS's `.env` from the Phase-0 sweep checklist — **`DRY_RUN=true`**.
4. `bash deploy/setup.sh` — installs venv + deps, sets **America/Los_Angeles** TZ
   (adjust if BPS ops are elsewhere), installs cron (nightly 3am + weekly dispute), runs
   the test suite (expect 60 passing).
5. Empty per-instance dirs: `data/`, `secrets/`, `source_maps/` (BPS's own files only —
   never copy Brooks's store, inventory, or sheets).

**Ops rules that MUST carry over:** long jobs run `nohup … & disown` with a `.done`
sentinel file + polling (SSH sessions drop constantly); never `pkill -f <script>` over SSH
(it matches your own session — kill by PID); never scp a whole local `.env` onto the
droplet (it reverts `DRY_RUN`/creds — append or edit specific lines).

**Done when:** tests pass on the droplet, cron installed, `DRY_RUN=true` confirmed.

## Phase 2 — PestRoutes foundation (Day 1–2)

1. **Probe the API** (read-only): confirm auth, list offices → set
   `PESTROUTES_OFFICE_IDS` (search is per-office; get is global — code handles).
2. **Pull the source picklist**: `customerSource/search?includeData=1` →
   `scripts/build_source_id_map.py` writes `data/pestroutes_source_inventory.json`.
3. ⚠️ **DUPLICATE-LABEL AUDIT (do not skip):** scan the picklist for labels that appear
   with 2+ sourceIDs (Brooks had two "Source 54"s — one hidden — and every engine write
   used the wrong one for a month). For each dupe, ask BPS which ID is canonical
   (usually the *Visible* one) and **pin it** in the generator scripts' override dict.
4. **Reconcile the master sheet**: every sheet Source-N must resolve to a sourceID
   (Brooks benchmark: 98/98). Fix label typos now.
5. **Protected sources**: set `PROTECTED_SOURCES` in `.env` from decision 0.4-1; spot-check
   the names match picklist labels exactly (matching is case-insensitive on the label).
6. **Initial-status filter**: confirm BPS uses the same scheduled-only semantics
   (`initialStatusText` ∈ {Completed, Pending}); adjust `SOURCEABLE_INITIAL_STATUS` if
   their workflow differs.

**Done when:** inventory resolves 100% of sheet sources, zero unexplained duplicate labels,
protected list confirmed in writing by BPS.

## Phase 3 — Channel connections (Day 2–4; channels are independent — parallelize)

### 3A. Genesys (phone)
1. Create/confirm OAuth client; set region + creds.
2. **Queue discovery:** `GET /api/v2/routing/queues` → list every queue to BPS; they mark
   the sales queues (don't forget Spanish/overflow *sales* queues; exclude customer
   service). Set `GENESYS_INSIDE_SALES_QUEUE_ID` (comma-separated, multi-queue supported).
3. Pull 3 days of calls; run the DNIS coverage check: every DNIS seen → sheet source?
   Produce the **unmapped-DNIS report**; BPS fills gaps in the sheet; iterate to ~0.
4. Sanity: ANI extraction rate (Brooks ~87%; blocked caller IDs are the unavoidable gap).

### 3B. Gmail (form leads)
1. OAuth (`scripts/gmail_auth.py`) against THEIR lead inbox; token to `secrets/`.
2. **Sender discovery:** scan the inbox for high-volume lead senders
   (gmail_new_senders-style metadata scan) → build `GMAIL_LEAD_SENDERS`.
   ⚠️ Ask each provider how they deliver: Brooks's DoLead also arrived via a
   **Zapier relay** (`zapiermail.com`) that the sender filter missed — 600+ leads/60d
   invisible. Hunt for relay senders (zapier, make.com, etc.) explicitly.
3. **Build `source_maps/email_providers.csv`** (domain + keyword rules → Source N) from
   real samples. Their website-platform network (Brooks: 66 Eforce/Solutions domains for
   ~40 cities!) needs the full domain list — run the domain-audit script pattern
   (`scripts/eforce_domain_audit.py`) against their inbox and have BPS review the proposal
   CSV, exactly like Brooks did.
4. **Unmatched audit loop:** `scripts/unmatched_email_audit.py` until NO-PROVIDER ≈ 0.
   (Name-fallback matching stays OFF — a curated alias table only; wrong source is worse
   than unidentified.)

### 3C. Meta (lead ads)
1. Confirm their Page ID + an app with `leads_retrieval` (reuse app 981711627997334 if
   their page can be granted to it; else 5-min Graph-Explorer grant on their own app).
2. Mint the **permanent Page token**: `scripts/meta_page_token.py` (Explorer token →
   long-lived → page-node token; `expires_at=0`). Gotchas baked into the script: app
   ID/secret must match the token's app; `me/accounts` is empty for business pages (we hit
   the page node directly); check the page box in the consent popup.
3. Inventory their lead forms + campaigns; set `META_PAGE_ID` and `META_LEAD_SOURCE`
   (their "Meta leads" Source N). If campaign names carry city/region and BPS wants
   per-region sources later, that's a phase-2 enhancement — start with one source like
   Brooks.
4. Note for the calendar: Meta `data_access_expires_at` ≈ 90 days — if Meta ingest 403s
   ~3 months in, re-run the token script.

### 3D. LSA (Google Local Services Ads)
1. **Day 1 action:** check if BPS accounts live under the SAME MCC as Brooks. If yes —
   reuse the dev token + OAuth, just enumerate their customer IDs. If no — apply for
   their own dev token + **Basic Access** immediately (the one approval that takes days;
   the design-doc PDF template from Brooks's application is reusable:
   `Brooks_LSA_API_Design_Doc.html` — swap names).
2. OAuth refresh token: `scripts/google_ads_auth.py` (reusable Desktop client).
3. Enumerate child accounts (`child_accounts()`), confirm against their LSA email
   notifications' "Customer ID"s; set `LSA_SOURCE` to their LSA Source N.
4. Verify with `scripts/lsa_probe.py` (handles the v21 API version, microsecond
   timestamps, Pacific-localization — all already fixed).
5. Known gap to disclose to BPS: LSA *message* leads via texting tools are only captured
   through this API (masked numbers otherwise); LSA call leads also arrive via Genesys.

**Done when (per channel):** a 3–7 day probe ingest runs clean, per-channel touch counts
look plausible to BPS, and the unmapped/unmatched reports are ~empty.

## Phase 4 — Backfill + validation (Day 4–6)

1. **Full backfill** to the chosen start date: `scripts/backfill_all.py <since>` on the
   droplet (memory-safe: Genesys 30-day windows per queue, Gmail monthly windows under the
   8000-message cap, watch for the cap warning). Use nohup+sentinel; expect 30–90 min.
2. **End-to-end dry-run** over a recent 1–2 week window: `scripts/resource_range.py
   <start> <end>` (NO --write). Produces the review CSV.
3. **Human audit with BPS** (the go-live gate): they review the DISAGREE/FILL rows against
   reality, like Brooks's 50-sale + full-week audits. Targets from Brooks's run:
   AGREE-rate ≳ 90% on sourced sales, every DISAGREE explainable by the evidence column.
4. Iterate: most audit misses are MAP gaps (missing DNIS, missing domain, queue not
   included), not engine bugs. Fix maps → re-ingest → re-run dry-run.
5. Re-confirm protected behavior on their data (find a few D2D/renewal sales in the
   report; verify "PROTECTED (kept)").

**Done when:** BPS signs off on a dry-run sheet; decisions tally looks sane
(NO-EVIDENCE ≈ their field-sales share; UNSOURCED near zero and explained).

## Phase 5 — Write-back enablement (Day 6–7)

1. **Round-trip write test on ONE subscription** (`scripts/test_writeback.py` pattern):
   read → no-op write → change → verify → revert. MUST include `officeID` (writes are
   office-scoped; omitting it fails on every non-default office — Brooks found this the
   hard way) and never send empty sourceID (blanks the field; client refuses).
2. Optionally 1–2 more nights of dry-run nightly reports for BPS comfort.
3. **Flip `DRY_RUN=false`** on their droplet. First live night: verify `writes (LIVE):
   {'WRITTEN': N}` with zero `WRITE_FAILED`, spot-check 3 subs in their PestRoutes UI.
4. **Historical re-source sweep** (per decision 0.4-4): dry-run `resource_range.py` over
   the agreed window → BPS reviews CSV → `--write`. Every write is read-back verified and
   logged to the `writes` audit table (reversible).

## Phase 6 — Steady-state operations

- **Nightly 3am:** ingest all channels (7d) → source last 2 days vs 365-day touch window →
  live write-back → daily sourcing CSV. **Weekly Mon 7am:** dispute report (their
  pay-per-lead list).
- **Maintenance playbook** (same as Brooks):
  - New website domains / providers appear constantly → re-run the domain audit + the
    unmatched-email audit monthly; new tracking numbers → unmapped-DNIS report.
  - New Genesys sales queue → append ID to `.env` (comma-separated), backfill that queue.
  - Meta ingest 403 after ~90 days → re-run `meta_page_token.py`.
  - Watch UNSOURCED in the daily report — every one is either a real no-lead sale or a
    map gap; investigate the way we did (it's how Brooks found the Spanish queue, the
    Zapier relay, and the 60-day-window bug).
  - Source-volume anomalies (a dormant source suddenly spiking) → run the SLP-style audit
    before paying invoices.
- **Open items inherited from Brooks (same for BPS):** emailed report delivery (tokens
  are read-only; needs a send path), cron-failure alerting.

---

## Timeline (calendar week, assuming creds on Day 1)

| Day | Work |
|---|---|
| 1 | Droplet up, code deployed, .env drafted, PestRoutes probe, picklist + dupe audit, **LSA Basic-Access application submitted**, sheet template handed to BPS |
| 2 | Master sheet (BPS) + inventory reconciliation; Genesys queues + first pull |
| 3 | Gmail OAuth + sender discovery + provider table v1; Meta page token + form inventory |
| 4 | LSA wiring (or same-MCC shortcut); unmatched/unmapped iteration; start full backfill |
| 5 | Dry-run over recent window → audit sheet to BPS |
| 6 | Audit iteration; write round-trip test; (optional) dry-run nightly overnight |
| 7 | Flip live; first live nightly verified; historical sweep dry-run queued |

The long pole is **BPS producing the master sheet and reviewing audits** — everything
technical is scripted. If LSA needs a fresh Basic-Access approval, LSA may land a day or
two behind the others; the system runs fine with the other three channels meanwhile.

## Risk register

| Risk | Mitigation |
|---|---|
| Duplicate picklist labels → writes to wrong/hidden source | Phase-2 dupe audit + pinned overrides (Brooks: "Source 54" lesson) |
| Provider delivery paths we don't know (relays) | Explicit relay hunt + unmatched-email audit until ~0 |
| Missed sales queues (Spanish etc.) | Queue discovery reviewed BY BPS, not assumed |
| Office-scoped writes | officeID plumbed through already; round-trip test proves it on their org |
| Old-lead blindness | LOOKBACK_DAYS=365 from day one (Brooks shipped with 60 and missed sales) |
| Droplet OOM on backfills | ≥1 GB + chunked backfill script (already memory-safe) |
| Code drift between the two companies | Git repo, one codebase, two .envs |
| Token expiries (Meta ~90d data-access) | Calendar reminder + documented re-mint scripts |
| LSA approval delay | Apply Day 1; check same-MCC shortcut first |

## Acceptance criteria (definition of "onboarded")

1. All four channels ingest nightly with zero channel errors for 3 consecutive nights.
2. Touch store backfilled to the agreed start date; per-channel counts reviewed by BPS.
3. Dry-run audit signed off by BPS (agreement rate ≳90%, all changes evidence-backed).
4. Live write-back on for ≥2 nights with 0 WRITE_FAILED; spot-checks pass in their UI.
5. Historical sweep completed and its review CSV delivered.
6. Dispute report configured for their pay-per-lead providers and generating weekly.
7. Maintenance playbook + credentials runbook handed to whoever owns it at BPS.
