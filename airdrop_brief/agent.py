"""Campaign brief workflow: fetch -> scan -> brief (agent) -> review (subagent) -> approve -> save.

OpenMinion building blocks used:
- ``Agent`` with a decorated READ_ONLY ``@tool`` (research_campaign)
- ``forced_tools`` so the first step is always evidence collection
- ``output_type`` (Pydantic) for a validated, typed brief
- ``subagent`` for a bounded second-opinion reviewer
- stable ``session_id`` per campaign so reruns share history
- ``approval_callback`` + a ``dangerous=True`` tool for the human approval gate
- ``ProviderError`` / ``AgentOutputValidationError`` / ``TurnTimeoutError`` handling
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from openminion import Agent, AgentOutputValidationError, ProviderError, subagent, tool
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import sources

TRACKER_PATH = Path("output/tracker.jsonl")


class CampaignBrief(BaseModel):
    project: str = Field(description="Project or campaign name")
    reward: str = Field(default="unknown", description="Prize pool / reward exactly as stated, or 'unknown'")
    deadline: str = Field(default="unknown", description="Deadline exactly as stated, or 'unknown'")
    tasks: list[str] = Field(default_factory=list, description="Concrete actions a participant must do")
    requirements: list[str] = Field(default_factory=list, description="Accounts, KYC, holdings, region limits")
    red_flags: list[str] = Field(default_factory=list, description="Risks backed by evidence from the page")
    effort: Literal["low", "medium", "high", "unknown"] = "unknown"
    verdict: Literal["do_it", "maybe", "skip"] = "maybe"
    reason: str = Field(default="", description="One or two sentences explaining the verdict")

    # Small models drift on enums and list shapes; normalize instead of failing.
    @field_validator("tasks", "requirements", "red_flags", mode="before")
    @classmethod
    def _as_list(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() and value.strip().lower() not in {"none", "unknown", "n/a"} else []
        return [str(v) for v in value]  # type: ignore[union-attr]

    @field_validator("project", "reward", "deadline", "reason", mode="before")
    @classmethod
    def _as_text(cls, value: object) -> str:
        if isinstance(value, (list, tuple)):
            return "; ".join(str(v) for v in value)
        return "unknown" if value is None else str(value)

    @model_validator(mode="after")
    def _separate_risks_from_rules(self) -> CampaignBrief:
        """Keep only real risks in red_flags; move rules the model misfiled to requirements."""
        risks: list[str] = []
        for flag in self.red_flags:
            if sources.is_risk(flag):
                risks.append(flag)
            elif not sources.NOTHING_FOUND.match(flag) and flag not in self.requirements:
                self.requirements.append(flag)
        self.red_flags = risks
        return self

    def add_scan_flags(self, red_flag_hits: dict[str, list[str]]) -> None:
        """Add deterministic scanner hits so a model can never drop them."""
        for label, snippets in red_flag_hits.items():
            entry = f"scanner: {label.replace('_', ' ')} ({snippets[0][:120]})"
            if entry not in self.red_flags:
                self.red_flags.append(entry)

    @field_validator("effort", mode="before")
    @classmethod
    def _effort(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        return next((v for v in ("low", "medium", "high") if v in text), "unknown")

    @field_validator("verdict", mode="before")
    @classmethod
    def _verdict(cls, value: object) -> str:
        text = str(value or "").strip().lower().replace(" ", "_")
        if "skip" in text or "avoid" in text:
            return "skip"
        if "do" in text or "yes" in text or "go" in text:
            return "do_it"
        return "maybe"


class Review(BaseModel):
    agrees: bool
    issues: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- tools
_LAST_SNAPSHOTS: dict[str, sources.PageSnapshot] = {}
EVIDENCE_CHARS = 3500


@tool(min_scope="READ_ONLY", idempotent=True, tags=("web", "read"))
def research_campaign(url: str) -> str:
    """Fetch a public campaign page (http/https GET only) and return its title, readable text (truncated),
    social links, and deterministic red-flag / reward / deadline pattern hits."""

    snap = sources.fetch(url)
    _LAST_SNAPSHOTS[url] = snap
    if snap.error:
        return json.dumps({"url": url, "error": snap.error})
    return json.dumps(
        {
            "url": url,
            "status": snap.status,
            "title": snap.title,
            "key_lines": sources.key_lines(snap.text),
            "text": snap.text[:EVIDENCE_CHARS],
            "social_links": sources.social_links(snap.links),
            "signals": sources.scan_signals(snap.text),
        }
    )


@tool(dangerous=True, tags=("write",))
def save_to_tracker(project: str, verdict: str, brief_json: str) -> str:
    """Append an approved campaign brief to the local tracker file. Requires human approval."""

    TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {"saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "project": project, "verdict": verdict, "brief": brief_json}
    with TRACKER_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    return f"saved {project} to {TRACKER_PATH}"


# ---------------------------------------------------------------- workflow
BRIEF_INSTRUCTIONS = """You are a careful crypto campaign analyst for an airdrop farmer.
Call research_campaign once on the given URL, then answer.
Only state facts that appear in the tool output.
requirements = rules or conditions a participant must meet (accounts, eligibility, submission rules).
red_flags = only risks to the participant (asks for seed phrase or wallet signing, upfront fees, fake urgency,
unclear or discretionary payout, KYC with government ID, locked rewards). Rules are NOT red flags. If something is missing write 'unknown'.
Never suggest connecting a wallet, sharing a seed phrase, paying fees or doing KYC.
Return ONE JSON object with fields: project, reward, deadline, tasks (list), requirements (list),
red_flags (list), effort (low|medium|high|unknown), verdict (do_it|maybe|skip), reason."""

REVIEW_INSTRUCTIONS = """You review a campaign brief against the source evidence.
Return ONE JSON object: {"agrees": true|false, "issues": [short strings]}.
Flag any claim in the brief that is not supported by the evidence, and any red flag the brief missed."""


@dataclass
class BriefRun:
    url: str
    brief: CampaignBrief | None = None
    review: Review | None = None
    saved: bool = False
    timings: dict[str, float] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    error: str | None = None


def session_for(url: str, tag: str = "") -> str:
    """Stable per-campaign session id so reruns share history (optionally namespaced)."""
    base = "airdrop-brief-" + hashlib.sha1(url.encode()).hexdigest()[:12]
    return f"{base}-{tag}" if tag else base


def run_brief(
    url: str,
    *,
    agent_id: str | None = None,
    approve: Callable[[str, dict, str], bool] | None = None,
    timeout_seconds: float = 600,
    session_tag: str = "",
    log: Callable[[str], None] = print,
) -> BriefRun:
    result = BriefRun(url=url)
    session_id = session_for(url, session_tag)
    try:
        with Agent(
            name="campaign-analyst",
            agent_id=agent_id,
            instructions=BRIEF_INSTRUCTIONS,
            tools=[research_campaign],
            forced_tools=["research_campaign"],
            output_type=CampaignBrief,
            session_id=session_id,
        ) as analyst:
            t0 = time.monotonic()
            log(f"[1/3] analyst: collecting evidence for {url}")
            try:
                brief_run = analyst.run(
                    f"Write a campaign brief for {url}",
                    timeout_seconds=timeout_seconds,
                    approval_callback=approve,
                )
                result.brief = brief_run.output
                result.run_ids.append(brief_run.run_id or "")
            except AgentOutputValidationError as exc:
                # Small local models often answer in prose. Repair once with a
                # tool-less formatter agent instead of failing the workflow.
                result.events.append("analyst returned prose; ran JSON repair step")
                log("      analyst answered in prose -> repair step")
                result.brief = _repair_brief(exc.raw_text, agent_id, session_id, timeout_seconds)
            result.timings["brief_s"] = round(time.monotonic() - t0, 1)

            evidence = _LAST_SNAPSHOTS.get(url)
            evidence_text = (
                "\n".join(sources.key_lines(evidence.text)) + "\n---\n" + evidence.text[:EVIDENCE_CHARS]
                if evidence
                else ""
            )
            signals = sources.scan_signals(evidence.text) if evidence else {}
            result.brief.add_scan_flags(signals.get("red_flags", {}))

            t1 = time.monotonic()
            log("[2/3] reviewer subagent: checking brief against evidence")
            reviewer = subagent(
                analyst,
                name="brief-reviewer",
                instructions=REVIEW_INSTRUCTIONS,
                timeout_seconds=int(timeout_seconds),
            )
            review_run = reviewer.run(
                "BRIEF:\n" + result.brief.model_dump_json(indent=1)
                + "\n\nSIGNALS:\n" + json.dumps(signals)[:2000]
                + "\n\nEVIDENCE:\n" + evidence_text
            )
            result.timings["review_s"] = round(time.monotonic() - t1, 1)
            result.review = _parse_review(review_run.text)
            result.run_ids.append(review_run.run_id or "")

        # Step 3: approval-gated save through OpenMinion's approval path.
        log("[3/3] approval gate: save to tracker?")
        t2 = time.monotonic()
        before = _tracker_lines()
        with Agent(
            name="tracker-writer",
            agent_id=agent_id,
            instructions="You save campaign briefs. Always call save_to_tracker exactly once, then reply 'done'.",
            tools=[save_to_tracker],
            forced_tools=["save_to_tracker"],
            session_id=session_id + "-save",
        ) as writer:
            writer.run(
                "Call save_to_tracker now with these exact arguments:\n"
                + json.dumps({"project": result.brief.project, "verdict": result.brief.verdict,
                              "brief_json": result.brief.model_dump_json()}),
                approval_callback=approve,
                timeout_seconds=timeout_seconds,
            )
        result.saved = _tracker_lines() > before
        if not result.saved:
            # forced_tools is not always honoured (see findings). Never save silently:
            # ask the same human approval, then write directly.
            result.events.append("writer agent skipped the forced tool; used direct approved save")
            log("      writer skipped save_to_tracker -> direct save after approval")
            if approve is None or approve("save_to_tracker", {"project": result.brief.project}, "direct"):
                save_to_tracker(result.brief.project, result.brief.verdict, result.brief.model_dump_json())
                result.saved = _tracker_lines() > before
        result.timings["save_s"] = round(time.monotonic() - t2, 1)
    except AgentOutputValidationError as exc:
        result.error = f"invalid model output: {exc.raw_text[:300]}"
    except ProviderError as exc:
        result.error = f"provider failed: {exc.code}: {exc.message}"
    except Exception as exc:  # noqa: BLE001 - TurnTimeoutError and anything unexpected become a reported error
        result.error = f"{type(exc).__name__}: {exc}"
    return result


REPAIR_INSTRUCTIONS = """Convert the campaign notes into ONE JSON object and nothing else.
Fields: project (string), reward (string), deadline (string), tasks (list of strings),
requirements (list of strings), red_flags (list of strings), effort (one of low, medium, high, unknown),
verdict (one of do_it, maybe, skip), reason (string). Use 'unknown' for anything not in the notes."""


def _repair_brief(raw_text: str, agent_id: str | None, session_id: str, timeout_seconds: float) -> CampaignBrief:
    with Agent(
        name="brief-formatter",
        agent_id=agent_id,
        instructions=REPAIR_INSTRUCTIONS,
        output_type=CampaignBrief,
        session_id=session_id + "-repair",
    ) as formatter:
        return formatter.run("NOTES:\n" + raw_text[:5000], timeout_seconds=timeout_seconds).output


def _tracker_lines() -> int:
    return len(TRACKER_PATH.read_text().splitlines()) if TRACKER_PATH.exists() else 0


def _parse_review(text: str) -> Review:
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return Review.model_validate_json(text[start : end + 1])
        except ValidationError:
            pass  # fall through to the explicit "unparseable" review below
    return Review(agrees=False, issues=["reviewer returned unparseable output: " + text[:200]])


def render_markdown(run: BriefRun) -> str:
    if run.error or not run.brief:
        return f"# Campaign brief failed\n\nURL: {run.url}\n\nError: `{run.error}`\n"
    b = run.brief
    lines = [
        f"# {b.project}",
        f"Source: {run.url}",
        "",
        f"**Verdict:** {b.verdict}  |  **Effort:** {b.effort}",
        f"**Reward:** {b.reward}",
        f"**Deadline:** {b.deadline}",
        "",
        "## Tasks",
        *[f"- {t}" for t in b.tasks or ["unknown"]],
        "",
        "## Requirements",
        *[f"- {r}" for r in b.requirements or ["none stated"]],
        "",
        "## Red flags",
        *[f"- {r}" for r in b.red_flags or ["none found"]],
        "",
        f"**Why:** {b.reason}",
        "",
        "## Reviewer subagent",
        f"Agrees: {run.review.agrees if run.review else 'n/a'}",
        *[f"- {i}" for i in (run.review.issues if run.review else [])],
        "",
        f"Saved to tracker: {run.saved}  |  timings: {run.timings}",
    ]
    return "\n".join(lines) + "\n"
