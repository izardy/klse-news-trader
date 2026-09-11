#!/bin/bash
# ============================================================
# setup_cron.sh — Install cron jobs for KLSE News Trader pipelines
# ============================================================
#
# Jobs installed:
#   1. Price updater:      every 15 minutes
#   2. PDF monitor:        daily at 06:00 MYT (22:00 UTC)
#   3. KLSE universe:      weekly on Sunday at 04:00 MYT (20:00 UTC Saturday)
#   4. Log rotation:       daily at 00:05 MYT (16:05 UTC)
#
# MYT = UTC+8, so:
#   06:00 MYT = 22:00 UTC (previous day)
#   04:00 MYT = 20:00 UTC (previous day)
#   00:05 MYT = 16:05 UTC (previous day)
# ============================================================

set -euo pipefail

PROJECT_DIR="/home/sandbox/klse-news-trader"
PYTHON="/home/sandbox/miniconda3/envs/ceruk/bin/python"
LOG_DIR="/tmp"

# Cron schedule expressions
PRICE_UPDATER_SCHEDULE="*/15 * * * *"           # Every 15 minutes
PDF_MONITOR_SCHEDULE="0 22 * * *"               # 22:00 UTC = 06:00 MYT (next day)
UNIVERSE_SCHEDULE="0 20 * * 0"                  # Sunday 20:00 UTC = Monday 04:00 MYT
LOG_ROTATE_SCHEDULE="5 16 * * *"                # 16:05 UTC = 00:05 MYT (next day)

# Commands
PRICE_UPDATER_CMD="cd ${PROJECT_DIR} && ${PYTHON} scripts/price_updater.py >> ${LOG_DIR}/price_updater.log 2>&1"
PDF_MONITOR_CMD="cd ${PROJECT_DIR} && ${PYTHON} scripts/daily_pdf_monitor.py >> ${LOG_DIR}/daily_pdf_monitor.log 2>&1"
UNIVERSE_CMD="cd ${PROJECT_DIR} && ${PYTHON} scripts/fetch_klse_universe.py >> ${LOG_DIR}/klse_universe.log 2>&1"
LOG_ROTATE_CMD="find ${LOG_DIR} -name '*.log' -size +10M -exec sh -c 'echo \"[rotated \$(date)] \" > \"\$1\"' _ {} \\;"

# Combined cron content
CRON_CONTENT="# KLSE News Trader — automated pipelines
# Generated $(date -u '+%Y-%m-%d %H:%M:%S UTC')
# ============================================================

# Price updater: every 15 minutes (fetches quotes for all KLSE stocks)
${PRICE_UPDATER_SCHEDULE} ${PRICE_UPDATER_CMD}

# PDF monitor: daily at 06:00 MYT (22:00 UTC) — checks for new annual reports
${PDF_MONITOR_SCHEDULE} ${PDF_MONITOR_CMD}

# KLSE universe scraper: weekly on Monday 04:00 MYT (Sunday 20:00 UTC)
${UNIVERSE_SCHEDULE} ${UNIVERSE_CMD}

# Log rotation: daily at 00:05 MYT (16:05 UTC) — truncate logs >10MB
${LOG_ROTATE_SCHEDULE} ${LOG_ROTATE_CMD}

# End of KLSE News Trader cron jobs
"

echo "=== KLSE News Trader — Cron Setup ==="
echo ""

# Check if scripts exist
for script in scripts/price_updater.py scripts/daily_pdf_monitor.py scripts/fetch_klse_universe.py; do
    if [ ! -f "${PROJECT_DIR}/${script}" ]; then
        echo "ERROR: ${PROJECT_DIR}/${script} not found!"
        exit 1
    fi
done

# Check Python exists
if [ ! -x "${PYTHON}" ]; then
    echo "ERROR: Python not found at ${PYTHON}"
    exit 1
fi

echo "Project dir: ${PROJECT_DIR}"
echo "Python: ${PYTHON}"
echo ""

# Backup existing cron
crontab -l > /tmp/crontab_backup_$(date +%Y%m%d_%H%M%S).txt 2>/dev/null || true

# Remove existing KLSE cron entries (if any) and add new ones
(crontab -l 2>/dev/null | grep -v "KLSE News Trader" | grep -v "price_updater.py" | grep -v "daily_pdf_monitor.py" | grep -v "fetch_klse_universe.py" | grep -v "# End of KLSE") > /tmp/crontab_clean.txt 2>/dev/null || true

# Add new entries
echo "${CRON_CONTENT}" >> /tmp/crontab_clean.txt

# Install
crontab /tmp/crontab_clean.txt

echo "Cron jobs installed successfully!"
echo ""
echo "Current crontab:"
echo "---"
crontab -l | grep -A20 "KLSE News Trader"
echo "---"
echo ""
echo "Summary:"
echo "  Price updater:   Every 15 min"
echo "  PDF monitor:     Daily at 06:00 MYT"
echo "  Universe scrape: Weekly (Mon 04:00 MYT)"
echo "  Log rotation:    Daily at 00:05 MYT"
echo ""
echo "Logs: ${LOG_DIR}/price_updater.log, ${LOG_DIR}/daily_pdf_monitor.log"
echo "Backup saved to /tmp/crontab_backup_*.txt"
