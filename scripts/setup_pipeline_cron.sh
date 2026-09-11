#!/bin/bash
# KLSE Pipeline Cron Setup
# Sets up cron jobs for the data pipeline schedule:
#   - daily_pdf_monitor: 06:00 MYT (22:00 UTC)
#   - sync + bedrock + drain: 06:30 MYT (22:30 UTC)
#   - price_updater: every 15 min (existing)
#   - universe_scraper: weekly Monday 04:00 MYT (20:00 UTC Sunday)
#
# Usage: bash scripts/setup_pipeline_cron.sh

set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="/home/sandbox/miniconda3/envs/ceruk/bin/python"
LOG_DIR="/tmp/klse-pipeline-logs"
PIPELINE_LOG="$LOG_DIR/pipeline.log"
MONITOR_LOG="$LOG_DIR/monitor.log"

mkdir -p "$LOG_DIR"

# Build cron entries
CRON_ENTRIES=$(cat <<EOF
# KLSE News Trader Pipeline
# Daily PDF Monitor — 06:00 MYT (22:00 UTC)
0 22 * * * cd $PROJECT_DIR && $PYTHON scripts/daily_pdf_monitor.py >> $MONITOR_LOG 2>&1

# Pipeline: sync + bedrock + drain — 06:30 MYT (22:30 UTC)
30 22 * * * cd $PROJECT_DIR && $PYTHON scripts/run_full_pipeline.py >> $PIPELINE_LOG 2>&1

# Price Updater — every 15 min
*/15 * * * * cd $PROJECT_DIR && $PYTHON scripts/price_updater.py >> $LOG_DIR/price.log 2>&1

# Universe Scraper — weekly Monday 04:00 MYT (Sunday 20:00 UTC)
0 20 * * 0 cd $PROJECT_DIR && $PYTHON scripts/fetch_klse_universe.py >> $LOG_DIR/universe.log 2>&1
EOF
)

echo "=== KLSE Pipeline Cron Setup ==="
echo ""
echo "This will install the following cron jobs:"
echo "  1. daily_pdf_monitor:    06:00 MYT daily (22:00 UTC)"
echo "  2. run_full_pipeline:    06:30 MYT daily (22:30 UTC)"
echo "  3. price_updater:        every 15 min"
echo "  4. fetch_klse_universe:  Monday 04:00 MYT (Sun 20:00 UTC)"
echo ""
echo "Python: $PYTHON"
echo "Project: $PROJECT_DIR"
echo "Logs: $LOG_DIR"
echo ""

# Check if these cron entries already exist
EXISTING=$(crontab -l 2>/dev/null || true)
if echo "$EXISTING" | grep -q "klse-news-trader\|run_full_pipeline\|daily_pdf_monitor"; then
    echo "WARNING: KLSE cron entries already exist."
    echo "Current KLSE entries:"
    echo "$EXISTING" | grep -E "klse|pipeline|monitor|run_full" || true
    echo ""
    read -p "Replace existing KLSE entries? (y/n): " CONFIRM
    if [ "$CONFIRM" != "y" ]; then
        echo "Aborted."
        exit 0
    fi
    # Remove existing KLSE entries
    EXISTING=$(echo "$EXISTING" | grep -v "klse-news-trader\|run_full_pipeline\|daily_pdf_monitor\|fetch_klse_universe" | grep -v "KLSE News Trader Pipeline" | grep -v "^# KLSE")
fi

# Install new cron entries
echo "$EXISTING" "$CRON_ENTRIES" | crontab -
echo "Cron jobs installed successfully."
echo ""
echo "Current crontab:"
crontab -l | grep -A1 "KLSE News"
