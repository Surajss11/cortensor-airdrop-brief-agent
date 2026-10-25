"""Deterministic tests: no model, no network."""

from airdrop_brief import sources
from airdrop_brief.agent import CampaignBrief, _parse_review, session_for


def test_red_flags_detect_seed_phrase_and_connect_to_claim():
    text = "Enter your 12-word seed phrase. Connect your wallet to claim the airdrop now!"
    hits = sources.scan_signals(text)["red_flags"]
    assert "seed_phrase" in hits
    assert "connect_to_claim" in hits


def test_positive_signals_detect_prize_and_deadline():
    text = "Prize pool: $7,500 USDC for the top 100. Submission deadline Nov 3 2026."
    pos = sources.scan_signals(text)["positives"]
    assert "prize_pool" in pos and "deadline" in pos and "leaderboard" in pos


def test_clean_page_has_no_red_flags():
    assert sources.scan_signals("Build an agent with the Python SDK and open a PR.")["red_flags"] == {}


def test_fetch_rejects_non_http_schemes():
    assert sources.fetch("file:///etc/passwd").error == "only http(s) URLs are allowed"


def test_strip_html_extracts_title_text_and_links():
    title, text, links = sources._strip_html(
        "<html><title>Quest</title><script>x()</script><p>Hello</p><a href='https://x.com/p'>x</a></html>"
    )
    assert title == "Quest" and "Hello" in text and "x()" not in text and links == ["https://x.com/p"]


def test_brief_normalizes_small_model_drift():
    brief = CampaignBrief.model_validate(
        {"project": "P", "reward": None, "deadline": ["Nov", "3"], "tasks": "Post a thread",
         "requirements": "none", "effort": "Medium effort", "verdict": "Do it!", "reason": "ok"}
    )
    assert brief.tasks == ["Post a thread"] and brief.requirements == []
    assert brief.effort == "medium" and brief.verdict == "do_it" and brief.reward == "unknown"
    assert brief.deadline == "Nov; 3"


def test_review_parser_tolerates_prose_and_garbage():
    assert _parse_review('Sure: {"agrees": true, "issues": []} done').agrees is True
    assert _parse_review("no json").agrees is False


def test_session_ids_are_stable_per_campaign():
    assert session_for("https://a") == session_for("https://a") != session_for("https://b")
    assert session_for("https://a", "r2").endswith("-r2")


def test_requirements_misfiled_as_red_flags_move_to_requirements():
    """Regression: the 3B model put submission rules under red_flags (run 2)."""
    brief = CampaignBrief.model_validate(
        {"project": "Hackathon #5", "reward": "$1,150", "deadline": "December 18, 2026",
         "tasks": ["Build an agent"],
         "requirements": ["Ensure the agent is reproducible and functioning"],
         "red_flags": [
             "Ensure the agent is reproducible and functioning",
             "Use properly licensed code and data, disclose external dependencies",
             "Awards are discretionary and half is in a one-month staking lock",
             "Asks you to enter your seed phrase to claim",
             "No specific red flags mentioned.",
         ],
         "effort": "medium", "verdict": "do_it", "reason": "ok"}
    )
    assert brief.red_flags == [
        "Awards are discretionary and half is in a one-month staking lock",
        "Asks you to enter your seed phrase to claim",
    ]
    assert brief.requirements == [
        "Ensure the agent is reproducible and functioning",
        "Use properly licensed code and data, disclose external dependencies",
    ]


def test_scanner_red_flags_are_always_kept():
    brief = CampaignBrief.model_validate({"project": "P", "tasks": [], "red_flags": []})
    hits = sources.scan_signals("Enter your 12-word seed phrase here")["red_flags"]
    brief.add_scan_flags(hits)
    brief.add_scan_flags(hits)
    assert len(brief.red_flags) == 1 and brief.red_flags[0].startswith("scanner: seed phrase")
