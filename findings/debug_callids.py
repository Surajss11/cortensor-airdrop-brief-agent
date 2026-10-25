"""Log provider tool-call ids before OpenMinion assigns fallback ids."""
import sys
from openminion.modules.llm import client_call as cc
orig = cc.llm_response_kwargs
def patched(*, resp, **kw):
    if resp.tool_calls:
        print("PROVIDER_TOOL_CALLS", [(tc.name, repr(tc.id), getattr(tc, "source", None)) for tc in resp.tool_calls], file=sys.stderr, flush=True)
    return orig(resp=resp, **kw)
cc.llm_response_kwargs = patched
import openminion.services.brain.client as bc
if hasattr(bc, "llm_response_kwargs"): bc.llm_response_kwargs = patched
exec(open("findings/debug_idempotency.py").read())
