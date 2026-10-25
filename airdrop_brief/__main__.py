"""CLI: python -m airdrop_brief URL [URL ...] [--agent ID] [--yes]"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .agent import render_markdown, run_brief


def main() -> int:
    parser = argparse.ArgumentParser(description="Turn airdrop/campaign pages into verified briefs.")
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--agent", default=None, help="Configured OpenMinion agent id")
    parser.add_argument("--yes", action="store_true", help="Auto-approve tracker saves (non-interactive)")
    parser.add_argument("--out", default="output")
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--session-tag", default="", help="Namespace the per-campaign session id")
    args = parser.parse_args()

    def approve(tool_name: str, tool_args: dict, approval_id: str) -> bool:
        if args.yes:
            print(f"  approval {approval_id}: {tool_name} auto-approved (--yes)")
            return True
        answer = input(f"  Approve {tool_name} for {tool_args.get('project')}? [y/N] ")
        return answer.strip().lower() == "y"

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failures = 0
    for url in args.urls:
        run = run_brief(url, agent_id=args.agent, approve=approve, timeout_seconds=args.timeout, session_tag=args.session_tag)
        slug = re.sub(r"[^a-z0-9]+", "-", url.lower()).strip("-")[:60]
        (out / f"{slug}.md").write_text(render_markdown(run))
        (out / f"{slug}.json").write_text(json.dumps({
            "url": run.url, "brief": run.brief.model_dump() if run.brief else None,
            "review": run.review.model_dump() if run.review else None, "saved": run.saved,
            "timings": run.timings, "run_ids": run.run_ids, "error": run.error,
        }, indent=2))
        print(render_markdown(run))
        failures += bool(run.error)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
