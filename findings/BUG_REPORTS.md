# OpenMinion findings (Cortensor Hackathon #5)

Environment: OpenMinion 0.1.11 (source checkout, `main` @ 34ea2c60, 2026-10-24),
Python 3.11.14, Linux x86_64, provider `ollama` (Ollama 0.40.11, `qwen2.5:3b` Q4_K_M, CPU only),
default `tool_call_strategy: fallback` written by `openminion setup --provider ollama`.

---

## Bug 1: id-less tool calls reuse `call_1` across model steps, failing the turn with "session event idempotency conflict"

**Severity:** high for local models: any multi-step tool turn on Ollama (default fallback strategy) can die.

**Steps to reproduce**
1. `openminion setup --provider ollama --model qwen2.5:3b --no-chat`
2. Run an `Agent` with two decorated tools and `forced_tools=[first]` and ask for a task that needs both
   (this repo: `python findings/debug_idempotency.py <url> --yes --session-tag dbgN`).
3. Step 1 calls `fetch_campaign_page` (id assigned `call_1`). The closure gate returns
   `next_action: continue`, the loop re-enters through `entry` in the **same turn scope**, and the model
   calls `scan_campaign_text`, which is again assigned `call_1`.

**Expected:** the second call gets its own id and the turn continues.

**Actual:** `brain.done` with `RUNTIME_ERROR: session event idempotency conflict: <event_id>`, surfaced to the SDK
as the reply body (see Bug 2). Evidence: `findings/debug_idempotency.log`

```
CONFLICT tool.call.requested
 DIFF payload
  old: {"call_id": "call_1", "canonical_name": "fetch_campaign_page", "turn_scope_id": "172b4304…"}
  new: {"call_id": "call_1", "canonical_name": "scan_campaign_text", "turn_scope_id": "172b4304…"}
```

**Root cause:** Ollama's fallback (text-parsed) tool calls carry no id. `llm_response_kwargs()` in
`src/openminion/modules/llm/client_call.py` assigns `f"call_{index + 1}"`, which is unique only inside one
provider response. `event_writes._stable_tool_event_id()` derives the event id from
`(session_id, turn_scope_id, call_id, phase)`, so a later step in the same turn collides.

**Fix (prepared locally, branch `fix/unique-fallback-tool-call-ids`, commit 08b82886):** add a per-response nonce,
`call_<8 hex>_<index>`, plus a regression test that two responses never share an id. `tests/llm` and
tool-calling suites: 736 passed; the 6 failures are pre-existing on `main` (provider-catalog name checks and a
missing proof artifact). With the fix the same workflow runs to completion.

**Workaround without the fix:** set the Ollama connection's `tool_call_strategy` to `native` (Ollama returns real ids),
or give the agent a single tool so a repeat call has an identical payload.

---

## Bug 2: runtime failures come back as reply text, so `output_type` users get a misleading `AgentOutputValidationError`

When the turn ends with a policy prompt, a runtime error or the time budget, `Agent.run()` returns the message as
`result.text` (prefixed `openminion: `) and then tries to validate it against `output_type`. The caller sees
`AgentOutputValidationError` whose `raw_text` is e.g. `openminion: State machine error: RUNTIME_ERROR: …` or
`openminion: Turn time budget exhausted …`. Expected: a typed error (`TurnTimeoutError`, a runtime error class, or
`result.run_state == "error"` checked before coercion). `AgentRunResult.run_state` is available but `_coerce_output`
ignores it. Evidence: `findings/run3.log`, `findings/run_patched_sdk.log`.

Also: when the whole-text parse fails and the extracted JSON object **also** fails, the error message reports the
first (whole-text) `ValidationError`, hiding the real field error from the extracted object.

---

## Rough edge 3: decorated tools default to `WRITE_SAFE`, so a pure read tool hits a confirmation prompt

`@tool` with `idempotent=True` and no `min_scope` registers as `min_scope_default="WRITE_SAFE"`. Without an
`approval_callback`, the run ends with a "Policy confirmation required … Risk: write" message as the reply (then Bug 2).
`examples/sdk/tool_agent.py` has the same shape. Suggest: default `idempotent=True` tools to `READ_ONLY`, or document
`min_scope="READ_ONLY"` in the decorator docstring and the SDK examples. Evidence: first run in this repo's history.

---

## Rough edge 4: `Agent.run(timeout_seconds=…)` does not extend the brain's 300 s elapsed budget

`timeout_seconds=600` still ends at ~300 s with "Turn time budget exhausted" because `BrainBudgets.max_elapsed_ms`
defaults to 300 000 and is only overridable via `OPENMINION_BRAIN_MAX_ELAPSED_MS`. CPU-hosted models need longer.
Expected: the per-run timeout raises the turn budget (or the docs say it does not). `run.sh` sets the env var.

---

## Rough edge 5: `forced_tools` was not honoured once the entry step chose `respond`

Run `fix4`, session `…-fix4-save`: `Agent(tools=[save_to_tracker], forced_tools=["save_to_tracker"])` received a
JSON payload. The entry step logged `brain.entry.path_detected {"path": "respond", "tool_call_names": []}` and the
turn ended with a prose reply. No tool call, no error, `run()` returned normally, so the caller cannot tell the
"forced" tool never ran. Expected: the tool is called, or the run fails / reports `forced_tool_unsatisfied`.
The agent now detects this (tracker line count) and falls back to an explicit human-approved save.
