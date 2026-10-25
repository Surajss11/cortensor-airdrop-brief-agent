"""Deterministic, network-bounded helpers used by the agent tools.

Nothing here talks to a wallet or signs anything. It only reads public pages
over HTTPS GET with a size and time limit.
"""

from __future__ import annotations

import html
import re
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse

USER_AGENT = "airdrop-brief-agent/0.1 (+https://github.com/openminion/openminion)"
MAX_BYTES = 400_000
TIMEOUT_S = 20

# Phrases that are common in wallet drainers and fake airdrops. A hit is not a
# verdict on its own; it is evidence the human reviewer must look at.
RED_FLAG_PATTERNS: dict[str, str] = {
    "seed_phrase": r"\b(seed phrase|recovery phrase|private key|mnemonic|12[- ]word|24[- ]word)\b",
    "connect_to_claim": r"\b(connect (your )?wallet to (claim|receive|verify)|claim (your )?airdrop now)\b",
    "upfront_payment": r"\b(send|deposit|pay)\b[^.]{0,40}\b(fee|gas fee|eth|usdt|sol|bnb)\b[^.]{0,40}\b(to (receive|claim|unlock))\b",
    "urgency": r"\b(only \d+ (spots|hours) left|last chance|expires in \d+ (minutes|hours))\b",
    "guaranteed_returns": r"\b(guaranteed|risk[- ]free)\b[^.]{0,30}\b(profit|returns?|reward)\b",
    "approve_unlimited": r"\b(set ?approval ?for ?all|unlimited approval|approve all tokens)\b",
}

POSITIVE_PATTERNS: dict[str, str] = {
    "prize_pool": r"(\$\s?\d[\d,]*(\.\d+)?\s?(k|K)?|\d[\d,]*\s?(USDC|USDT|USD))",
    "deadline": r"\b(deadline|ends?|closes?|until|submission)\b[^.\n]{0,60}\b(20\d\d|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    "leaderboard": r"\b(leaderboard|rank(ed|ing)?|top \d+)\b",
    "official_docs": r"\b(docs|documentation|terms|rules)\b",
}


@dataclass
class PageSnapshot:
    url: str
    status: int
    title: str
    text: str
    links: list[str] = field(default_factory=list)
    error: str | None = None


def _strip_html(raw: str) -> tuple[str, str, list[str]]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    title = html.unescape(title_match.group(1).strip()) if title_match else ""
    links = re.findall(r'href=["\'](https?://[^"\'#\s]+)', raw, re.IGNORECASE)
    body = re.sub(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
    body = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", body, flags=re.IGNORECASE)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body)
    return title, body.strip(), list(dict.fromkeys(links))


def fetch(url: str) -> PageSnapshot:
    """GET one public http(s) page and return readable text."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return PageSnapshot(url=url, status=0, title="", text="", error="only http(s) URLs are allowed")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            raw_bytes = response.read(MAX_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
            status = int(getattr(response, "status", 200))
    except (OSError, ValueError) as exc:  # network errors are data for the agent, not crashes
        return PageSnapshot(url=url, status=0, title="", text="", error=f"{type(exc).__name__}: {exc}")
    raw = raw_bytes.decode(charset, errors="replace")
    if "<html" in raw[:2000].lower() or "<!doctype" in raw[:200].lower():
        title, text, links = _strip_html(raw)
    else:
        title, text, links = "", raw, re.findall(r"https?://[^\s)\]>\"']+", raw)
    return PageSnapshot(url=url, status=status, title=title, text=text, links=links)


def scan_signals(text: str) -> dict[str, list[str]]:
    """Return matched red-flag and positive snippets, deterministically."""

    def _hits(patterns: dict[str, str]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for label, pattern in patterns.items():
            snippets = []
            for match in re.finditer(pattern, text, re.IGNORECASE):
                start, end = max(0, match.start() - 50), min(len(text), match.end() + 50)
                snippets.append(" ".join(text[start:end].split()))
                if len(snippets) >= 3:
                    break
            if snippets:
                found[label] = snippets
        return found

    return {"red_flags": _hits(RED_FLAG_PATTERNS), "positives": _hits(POSITIVE_PATTERNS)}


def social_links(links: list[str]) -> list[str]:
    keep = ("x.com", "twitter.com", "discord", "t.me", "github.com", "docs.", "gitbook", "medium.com", "mirror.xyz")
    return [link for link in links if any(k in link for k in keep)][:15]


KEY_LINE_PATTERN = re.compile(
    r"(\$\s?\d|usdc|usdt|prize|reward|deadline|ends?\b|until|timeline|submission|winners?|leaderboard|kyc|wallet|seed)",
    re.IGNORECASE,
)


def key_lines(text: str, limit: int = 25) -> list[str]:
    """Lines most likely to hold reward, deadline and risk facts, wherever they sit on the page."""

    lines = [" ".join(line.split()) for line in text.splitlines()]
    return [line[:220] for line in lines if line and KEY_LINE_PATTERN.search(line)][:limit]


# A red flag is a risk to the participant, not a rule they must follow. Small
# models often copy requirements ("keep secrets out of submissions") into the
# red-flag list; anything without a risk cue is treated as a requirement.
RISK_CUE = re.compile(
    r"(scam|phish|drain|fraud|fake|impersonat|suspicious|unverified|unaudited|anonymous team|"
    r"seed phrase|recovery phrase|private key|mnemonic|connect (your )?wallet|sign (a |the )?(message|transaction)|"
    r"approv(e|al) (all|unlimited)|upfront|deposit|pay(ment)? (a )?fee|gas fee|send (eth|usdt|sol|bnb|funds)|"
    r"guaranteed|risk[- ]free|too good|urgen|last chance|expires in|no (official|contact|docs|terms)|"
    r"kyc|government id|passport|discretionary|may not (be )?(paid|awarded)|unclear (reward|payout|terms)|"
    r"lock(ed|up)?|vesting|staking lock|not guaranteed|red flag|risk)",
    re.IGNORECASE,
)
NOTHING_FOUND = re.compile(r"^\s*(none|no (specific |obvious )?red flags?.*|n/?a|unknown)\s*\.?\s*$", re.IGNORECASE)


def is_risk(text: str) -> bool:
    return bool(RISK_CUE.search(text)) and not NOTHING_FOUND.match(text)
