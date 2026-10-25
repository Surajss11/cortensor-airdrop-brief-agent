#!/usr/bin/env bash
# Terminal demo used for the hackathon video: tests, provider check, then one real model-backed run.
set -uo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate
export OPENMINION_HOME="$PWD" OPENMINION_DATA_ROOT="$PWD/.openminion"
step() { printf '\n\033[1;36m$ %s\033[0m\n' "$*"; }
step "python -m pytest -q tests"; python -m pytest -q tests
step "ollama list"; ollama list
step "grep timeout_seconds (ollama provider)"; python -c "import json;print(json.load(open('.openminion/agents.json'))['providers']['ollama']['timeout_seconds'])"
step "./run.sh ${DEMO_URL:-https://docs.cortensor.network/community-and-ecosystem/hackathon/hackathon-5.md} --yes --session-tag demo-$(date +%H%M)"
time ./run.sh "${DEMO_URL:-https://docs.cortensor.network/community-and-ecosystem/hackathon/hackathon-5.md}" --yes --session-tag "demo-$(date +%H%M)"
step "tail -n 1 output/tracker.jsonl"; tail -n 1 output/tracker.jsonl | cut -c1-300
