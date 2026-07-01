#!/usr/bin/env bash
# Lead Attribution — droplet setup. Idempotent; safe to re-run.
# Run from the app directory:  bash deploy/setup.sh
set -euo pipefail
APP="$(cd "$(dirname "$0")/.." && pwd)"
cd "$APP"
echo ">> app dir: $APP"

echo ">> timezone -> America/Los_Angeles"
timedatectl set-timezone America/Los_Angeles || true

echo ">> system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y -q
apt-get install -y -q python3-venv python3-pip

echo ">> python venv + dependencies"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

mkdir -p data logs

echo ">> installing cron schedule (Pacific time)"
cat > /etc/cron.d/lead-attribution <<CRON
SHELL=/bin/bash
# nightly 3am: full run = ingest touches + re-source the last 14 days' sales +
# daily report. The 14-day re-source window is self-healing: late-arriving leads
# and backdated/late-entered subscriptions get re-evaluated for ~2 weeks instead
# of slipping permanently (a 2-day window let DoLead leads near the sale time get
# missed). Write-back is gated by DRY_RUN in .env.
0 3 * * *  root  cd $APP && .venv/bin/python scripts/nightly_run.py 7 14 >> logs/nightly.log 2>&1
# weekly: duplicate-dispute reports (one email per provider group), Monday 7am.
# --week = the previous full Sun-Sat week. Needs source_maps/dispute_groups.csv
# and RESEND_* in .env (see docs/DISPUTE_REPORTS.md); no-ops safely if unset.
0 7 * * 1   root  cd $APP && .venv/bin/python scripts/dispute_reports.py --week >> logs/dispute.log 2>&1
CRON
chmod 0644 /etc/cron.d/lead-attribution
systemctl restart cron 2>/dev/null || service cron restart 2>/dev/null || true

echo ">> running test suite"
.venv/bin/python -m pytest -q || echo "(!) tests reported issues — review above"

echo ">> smoke test: ingest 1 day (Meta will skip until its token is added)"
.venv/bin/python scripts/ingest_touches.py 1 || echo "(!) ingest smoke test failed — check .env / tokens"

echo ">> DONE."
echo "   logs:  $APP/logs/{ingest,dispute}.log"
echo "   cron:  /etc/cron.d/lead-attribution"
echo "   run manually:  cd $APP && .venv/bin/python scripts/dispute_reports.py --dry-run"
