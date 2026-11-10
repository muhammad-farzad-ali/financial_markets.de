#!/usr/bin/env bash
# Run the complete German markets pipeline.
#
# Usage:
#   ./scripts/run.sh                 # cached collection, then analyse/report
#   ./scripts/run.sh --refresh       # force fresh downloads
#   ./scripts/run.sh --skip-collect  # reuse existing data; no network
#   ./scripts/run.sh --only DAX,SAP.DE
#   ./scripts/run.sh --skip-fundamentals
#   ./scripts/run.sh --fundamentals-only SAP.DE
#   ./scripts/run.sh --skip-quarterly
#
# The script changes into the repository root first, so it can be called from
# any directory.  It uses `exec` so the exit code of the Python pipeline is the
# exit code of this script.
set -euo pipefail

cd "$(dirname "$0")/.."
exec python3 scripts/run_all.py "$@"
