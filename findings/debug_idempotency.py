"""Wrap EventStore to print which fields differ when the idempotency conflict fires."""
import json, sys
from openminion.modules.session.storage import events as ev
orig = ev.EventStore.insert_session_event_tx
def patched(self, **kw):
    try:
        return orig(self, **kw)
    except ValueError as exc:
        if "idempotency conflict" in str(exc):
            existing = self.get_event_by_id(kw.get("event_id"))
            print("CONFLICT", kw.get("event_type"), file=sys.stderr)
            for k in ("session_id","event_type","actor_type","actor_id","trace_id","span_id","task_id","parent_event_id","payload","refs","importance","redaction"):
                new = kw.get(k); old = existing.get(k)
                if k=="payload": new = dict(new or {})
                if k=="refs": new = dict(new or {})
                if new != old:
                    print(" DIFF", k, "\n  old:", json.dumps(old, default=str)[:1500], "\n  new:", json.dumps(new, default=str)[:1500], file=sys.stderr)
        raise
ev.EventStore.insert_session_event_tx = patched
sys.argv = ["airdrop_brief", *sys.argv[1:]]
from airdrop_brief.__main__ import main
raise SystemExit(main())
