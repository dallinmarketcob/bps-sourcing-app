# Weekly Duplicate-Dispute Reports

Some lead providers are **pay-per-lead** — you pay for each lead they send. When
a provider sends you a lead you had *already* received (from a different source)
within the last few days, that's a **duplicate** you can dispute and not pay for.

`scripts/dispute_reports.py` builds these reports automatically once a week: for
each provider **group**, it finds this week's leads whose phone or email already
reached you under a **different** source within the prior 7 days, and emails a
per-group CSV (with the timestamped prior touches as **proof**) via Resend.

This runs off the same touch store the attribution engine already fills nightly —
it's essentially free once ingestion is running.

---

## The key concept you must build: source GROUPS

A single provider often shows up in your PestRoutes picklist as **many** sources
(e.g. one per city, or per campaign). For a sensible report you want **one email
per provider**, not one per source. That mapping is
`source_maps/dispute_groups.csv` — **you build this from your own sources.**

Copy the template and fill it in:
```bash
cp source_maps/example_dispute_groups.csv source_maps/dispute_groups.csv
```

It has three columns:

| Column | Meaning |
|---|---|
| `source` | The exact PestRoutes source label, e.g. `Source 20` (must match your picklist / master sheet) |
| `provider` | Friendly name (for readability), e.g. `Example Web San Jose` |
| `group` | The **report bucket**. All rows with the same `group` are reported together in one email |

Example — three city sites from one web network collapse into one report:
```csv
source,provider,group
Source 20,Example Web City A,Example Web Network
Source 21,Example Web City B,Example Web Network
Source 22,Example Web City C,Example Web Network
Source 11,ElectGen,ElectGen
```

### Which sources to include — and which to leave OUT

- **Include** every pay-per-lead provider you'd actually dispute (affiliates,
  paid-lead vendors, paid directories, etc.). Group each provider's sources under
  one group name.
- **Leave OUT** your **owned / no-dispute** sources — website forms, referrals,
  door-to-door, renewals, upsells, organic, "saw our truck", etc. You'd never
  dispute those, so they aren't report groups.
  - **Important:** leaving them out of the file does **not** ignore them. Any
    source *not* in a group still counts as **prior evidence** against the groups
    that *are* reported. So if a customer filled out your website form on Monday
    and a paid provider "sold" you the same lead Wednesday, the website-form touch
    is exactly the proof that flags the provider's lead as a duplicate.

That's the whole trick: **report the providers you pay; count everything as prior
evidence.**

---

## The duplicate rule

A group's lead this week is flagged as a duplicate when the **same phone (either
number) or email** was seen under a **different** source within the **7 days**
before that lead.

- **Cross-provider only.** A prior touch from the group's *own* sources is a
  same-provider repeat (providers dedupe their own), so it's ignored — we only
  report leads you'd *already gotten from someone else*.
- The 7-day prior window is `PRIOR_WINDOW_DAYS` at the top of
  `scripts/dispute_reports.py` (change it there if you want a different window).
- Groups with zero duplicates are skipped (no empty emails).

---

## Configure email delivery (Resend)

The reports are emailed via [Resend](https://resend.com). Set these in `.env`:

```ini
RESEND_API_KEY=re_...                       # from your Resend dashboard
RESEND_FROM=noreply@yourdomain.com          # a Resend-verified sender on your domain
RESEND_TO=you@yourco.com,teammate@yourco.com  # internal recipients
COMPANY_NAME=Your Company, Marketing        # signature line on the emails
```

Send the reports to **yourselves**, not directly to providers — you review each
CSV and forward it to the provider with your dispute.

---

## Run it

```bash
# Build the CSVs locally but DON'T email (safe to run anytime):
python scripts/dispute_reports.py --dry-run

# Send just to yourself to test the email path:
python scripts/dispute_reports.py --to you@yourco.com --days 7

# What cron runs — previous full Sun–Sat week, emailed to RESEND_TO:
python scripts/dispute_reports.py --week

# Limit to some groups:
python scripts/dispute_reports.py --dry-run --only "Example Web Network,ElectGen"
```

CSVs are also written to `data/dispute_reports/` regardless of email.

## Schedule

`deploy/setup.sh` installs a weekly cron entry (Mondays 7am) that runs
`dispute_reports.py --week`. `--week` = the previous full **Sunday–Saturday**
calendar week (not a rolling 7 days), so each Monday's report covers the week
that just ended.

---

## Checklist to turn this on for a new instance

1. `cp source_maps/example_dispute_groups.csv source_maps/dispute_groups.csv` and
   fill it with **your** sources → groups (leave owned sources out).
2. Set `RESEND_*` and `COMPANY_NAME` in `.env`.
3. `python scripts/dispute_reports.py --dry-run` — confirm the groups resolve and
   the counts look sane.
4. `python scripts/dispute_reports.py --to you@yourco.com` — confirm the email +
   attachment arrive.
5. Deploy; the Monday cron takes over.
