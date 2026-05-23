#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPYCACHEPREFIX="${TMPDIR:-/tmp}/ai_stock_scorer_pycache"

cd "$ROOT_DIR"
python3 -m unittest -v
