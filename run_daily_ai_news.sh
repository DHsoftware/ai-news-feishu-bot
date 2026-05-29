#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "Start daily AI news run in: ${SCRIPT_DIR}"

if ! git pull; then
  echo "Warning: git pull failed. Continue with local JSON cache." >&2
fi

python scripts/daily_ai_news.py
