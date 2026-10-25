#!/usr/bin/env bash
# Reproducible local run: project-scoped OpenMinion state, longer turn budget for CPU models.
set -euo pipefail
cd "$(dirname "$0")"
export OPENMINION_HOME="$PWD" OPENMINION_DATA_ROOT="$PWD/.openminion"
export OPENMINION_BRAIN_MAX_ELAPSED_MS="${OPENMINION_BRAIN_MAX_ELAPSED_MS:-1500000}"
exec python -m airdrop_brief "$@"
