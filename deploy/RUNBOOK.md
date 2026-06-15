# Deploy runbook — lead-attribution droplet

Run these from **your Windows terminal** (PowerShell), in the project folder. You
SSH from your machine because the droplet firewall allows your IP.

Droplet: `root@64.227.22.254`

## 1. Bundle the project (from the project folder)
Excludes the local venv/caches; includes code, config, secrets, the master sheet,
and the existing touch store so the droplet starts with full history.

```powershell
cd C:\Users\dalli\Projects\Form_Lead_DeDupe
tar czf deploy.tar.gz `
  --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache `
  --exclude="*.pyc" --exclude=email_samples `
  src scripts source_maps requirements.txt pyproject.toml .env secrets data deploy
```

## 2. Copy it up
```powershell
scp deploy.tar.gz root@64.227.22.254:/root/
```

## 3. Unpack + set up (on the droplet)
```powershell
ssh root@64.227.22.254
# --- now on the droplet ---
mkdir -p /opt/lead-attribution
tar xzf /root/deploy.tar.gz -C /opt/lead-attribution
cd /opt/lead-attribution
bash deploy/setup.sh
```

`setup.sh` installs Python + deps, sets Pacific timezone, runs the tests, does a
1-day ingest smoke test, and installs the cron jobs (nightly ingest, weekly
dispute report). Meta will skip until its token is added — expected.

## 4. When the Meta System User token is approved
Edit `.env` on the droplet, set `META_ACCESS_TOKEN=…`, then backfill Meta:
```bash
cd /opt/lead-attribution
.venv/bin/python scripts/ingest_touches.py 50 meta
```

## Updating the code later
Re-run steps 1–3 (the tar overwrites `/opt/lead-attribution`; the `data/` store
and `.env` are preserved if you exclude them from the tar, or overwritten if you
include them — include them only when you intend to).

## Updating the master sheet
The sheet now lives at `source_maps/sourcing_master.csv`. Edit it, then re-copy
just that file:
```powershell
scp source_maps\sourcing_master.csv root@64.227.22.254:/opt/lead-attribution/source_maps/
```

## Checking on it
```bash
tail -f /opt/lead-attribution/logs/ingest.log
ls -la /opt/lead-attribution/data/disputes_*.csv
```
