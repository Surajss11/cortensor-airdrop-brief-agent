# Airdrop Campaign Brief Agent (OpenMinion 0.1)

Cortensor Hackathon #5 submission. Paste a crypto campaign / airdrop / quest URL and get a short,
evidence-backed brief: reward, deadline, tasks, requirements, red flags, effort and a do-it / maybe / skip verdict,
double-checked by a reviewer subagent and saved to a local tracker only after a human approves.

Airdrop farmers check dozens of campaign pages a week. Most of the time goes on reading the same things: what's the
prize, when does it end, what do I have to do, and is this a wallet drainer? This agent does that check the same way
every time and never touches a wallet.

## Workflow

```
URL ──► [1] analyst Agent ── forced tool: research_campaign (READ_ONLY)
              │                 HTTPS GET + deterministic red-flag / reward / deadline scan
              ▼
        CampaignBrief (Pydantic output_type) ── if the model answers in prose ─► JSON repair Agent
              ▼
        [2] reviewer subagent ── checks every claim against the fetched evidence
              ▼
        [3] tracker-writer Agent ── save_to_tracker (dangerous=True) ── approval_callback (human y/N)
              ▼
        output/<slug>.md  output/<slug>.json  output/tracker.jsonl
```

### OpenMinion building blocks used

| Building block | Where | Result |
| --- | --- | --- |
| `Agent` + `@tool` (decorated Python tools) | `research_campaign`, `save_to_tracker` | works; read tools need `min_scope="READ_ONLY"` (see findings) |
| `forced_tools` | step 1 always collects evidence first | works |
| `output_type` (Pydantic) | `CampaignBrief` | works with a lenient model + repair step for small models |
| `subagent` (delegation) | `brief-reviewer` | works |
| Sessions | stable `session_id` per campaign URL, `--session-tag` to namespace reruns | works |
| Permissions / approvals | `dangerous=True` tool + `approval_callback` | works, prompt or `--yes` |
| Error types | `ProviderError`, `AgentOutputValidationError` | runtime errors arrive as output-validation errors (bug 2) |
| Providers | Ollama local (`qwen2.5:3b`); Cortensor Portal preset ready (`cortensor-portal`, `oss-20b`) | Portal needs a `CORTENSOR_API_KEY` |

## Setup

```bash
git clone <this repo> && cd cortensor-agent
python3.11 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

export OPENMINION_HOME="$PWD" OPENMINION_DATA_ROOT="$PWD/.openminion"   # keep state in the project
```

Pick a model provider (any one):

```bash
# Local, free: Ollama
ollama pull qwen2.5:3b            # or: ollama create qwen2.5:3b -f Modelfile from the HF GGUF
openminion setup --provider ollama --model qwen2.5:3b --no-chat

# Cortensor Portal (hosted, needs access)
export CORTENSOR_API_KEY=...
openminion setup --provider cortensor-portal --model oss-20b --no-chat

openminion doctor --check-turn
```

CPU-hosted Ollama models need a longer provider timeout than the 60 s that `setup` writes (see rough edge 6):

```bash
python - <<'PY'
import json; p = ".openminion/agents.json"; d = json.load(open(p))
d["providers"]["ollama"]["timeout_seconds"] = 600; json.dump(d, open(p, "w"), indent=2)
PY
```

## Run

```bash
./run.sh https://example.com/campaign            # asks before saving to the tracker
./run.sh URL1 URL2 --yes                          # batch, auto-approve saves
./run.sh URL --agent my-agent --session-tag rerun # pick a configured agent, namespace the session
python -m pytest -q tests                         # deterministic tests, no model or network
```

`run.sh` sets `OPENMINION_BRAIN_MAX_ELAPSED_MS=1500000`: a CPU-only 3B model needs more than the default
300 s turn budget.

## Demo

- `demo/demo.mp4`: terminal recording of a real run (tests, provider check, one model-backed brief with the reviewer
  subagent and the approval-gated tracker save). Waiting on the model is sped up; nothing else is edited.
- `demo/demo.cast`: the same recording as an asciinema cast (`asciinema play demo/demo.cast`).
- `demo/demo.sh`: the exact script that was recorded, so anyone can reproduce it.

## Example output

See `examples/` for a real run's Markdown brief and JSON (model: `qwen2.5:3b` on CPU via Ollama).

## Building Blocks Tested

See the table above and `findings/BUG_REPORTS.md` for full reproduction steps:

1. **Bug:** tool calls without provider ids reuse `call_1` across model steps in the same turn and kill it with
   `session event idempotency conflict` (Ollama default `fallback` strategy). Reported in
   [openminion#410](https://github.com/openminion/openminion/issues/410), fix PR
   [openminion#411](https://github.com/openminion/openminion/pull/411) with a persistence-path regression test.
2. **Bug:** runtime errors (policy prompts, state-machine errors, time budget) come back as reply text and surface as
   `AgentOutputValidationError` when `output_type` is set.
3. **Rough edge:** `@tool` defaults to `WRITE_SAFE`, so read-only tools trigger a write confirmation.
4. **Rough edge:** `Agent.run(timeout_seconds=…)` does not raise the brain's 300 s elapsed budget.
5. **Rough edge:** `forced_tools` is silently skipped when the entry step chooses `respond`.
6. **Rough edge:** `setup --provider ollama` writes a 60 s provider timeout, too short for CPU models.

## What We Learned

- Small local models drift on enums and list shapes; a lenient Pydantic model plus a one-shot repair agent made
  structured output reliable without a bigger model.
- One tool that does fetch + deterministic scan is faster and more reliable on a 3B model than two chained tools.
- Keeping the scam checks deterministic (regex evidence) and letting the model only summarize gives repeatable
  red-flag results; the reviewer subagent catches claims that are not in the evidence.
- The same 3B model for analyst and reviewer misses omissions: in the recorded demo the brief reported only the
  1st-place prize ($500) as the reward and the reviewer agreed. A deterministic reward/prize scan in the evidence
  catches it; a different or larger reviewer model should be used when available (Portal).
- Product feedback: check `run_state` before coercing output; default read tools to `READ_ONLY`; make per-run timeouts
  cover the turn budget; generate unique ids for id-less tool calls.

## Safety

Read-only HTTPS GET with size and time limits. No wallet connections, signing, payments or KYC. The tracker write is
the only side effect and it is approval-gated.

## License

Apache-2.0
