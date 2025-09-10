import hashlib
from typing import List, Optional
from datetime import datetime, timezone
import re

import feedparser
from dateutil import parser as dtparse
from bs4 import BeautifulSoup

from .models import Article

# ----------------------------
# Team keyword filters (expand later)
# ----------------------------
# Only ARS defined now; other teams pass-through unchanged.
TEAM_KEYWORDS = {
    "ARS": [
        r"\barsenal\b",
        r"\bthe gunners\b",
        r"\bgooners?\b",
        # common URL/path checks handled separately
    ]
}

# compile regexes once
TEAM_REGEX = {
    code: [re.compile(pat, re.IGNORECASE) for pat in pats]
    for code, pats in TEAM_KEYWORDS.items()
}

# If a URL contains these fragments, we treat it as Arsenal-related even if text is short.
ARS_URL_HINTS = [
    "/arsenal", "arsenal.", "/team/arsenal", "/teams/arsenal"
]


# ----------------------------
# Helpers
# ----------------------------

def _safe_str(x: Optional[str]) -> str:
    return x or ""

def _iso8601(dt: Optional[datetime]) -> str:
    if not dt:
        return datetime.now(timezone.utc).isoformat()
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def _hash_id(url: str, title: str) -> str:
    h = hashlib.sha1()
    h.update((url + "|" + title).encode("utf-8", errors="ignore"))
    return h.hexdigest()

def _clean_text(htmlish: str, max_chars: int = 280) -> str:
    """
    Strip all HTML, decode entities, and clamp to ~max_chars without mid-word cuts.
    """
    if not htmlish:
        return ""
    soup = BeautifulSoup(htmlish, "lxml")
    text = soup.get_text(separator=" ", strip=True)
    text = " ".join(text.split())  # collapse whitespace

    if max_chars and len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        if cut == -1:
            cut = max_chars
        text = text[:cut].rstrip() + "…"
    return text


def _is_about_team(team_codes: List[str], source_name: str, title: str, summary: str, link: str) -> bool:
    """
    Returns True if the item is clearly about the requested team(s).
    - Fan sites (already team-specific) pass-through.
    - BBC team feed (built URL) will already be Arsenal-only.
    - For generic providers, require Arsenal signals in title/summary/url.
    """
    # If any requested code has filters, apply them; otherwise pass-through.
    tcs = [tc.upper() for tc in team_codes]
    text_blob = f"{title} {summary}".strip()

    # Short-circuit: if link path clearly contains /arsenal, accept.
    lower_link = (link or "").lower()
    if any(hint in lower_link for hint in ARS_URL_HINTS) and "ARS" in tcs:
        return True

    # If we have regexes for the team, require a match in title/summary.
    for tc in tcs:
        regs = TEAM_REGEX.get(tc)
        if not regs:
            # No filters defined for this team → accept (fan sites, future teams)
            return True
        if any(r.search(text_blob) for r in regs):
            return True

    return False


# ----------------------------
# Main fetch routine
# ----------------------------

def fetch_rss(url: str, team_codes: List[str], leagues: List[str], source_name: str) -> List[Article]:
    """
    Download an RSS/Atom feed and return a list of Article objects.
    - Cleans summary to plain text (no HTML/entities).
    - DOES NOT force thumbnails: only passes through media:* if present.
    - Filters generic feeds so Arsenal page only shows Arsenal-related items.
    """
    parsed = feedparser.parse(url)
    items: List[Article] = []

    for entry in parsed.entries:
        title = _safe_str(getattr(entry, "title", ""))
        link = _safe_str(getattr(entry, "link", ""))

        # published > updated > now
        published_txt = _safe_str(getattr(entry, "published", "")) or _safe_str(getattr(entry, "updated", ""))
        published_dt: Optional[datetime] = None
        if published_txt:
            try:
                published_dt = dtparse.parse(published_txt)
            except Exception:
                published_dt = None

        # Raw HTML-ish fields we might receive
        raw_summary = _safe_str(getattr(entry, "summary", ""))
        raw_content = ""
        try:
            if hasattr(entry, "content") and isinstance(entry.content, list) and len(entry.content) > 0:
                raw_content = _safe_str(entry.content[0].value)
        except Exception:
            raw_content = ""

        # Clean, human-friendly summary (prefer content if present)
        summary_html = raw_content or raw_summary
        summary = _clean_text(summary_html, max_chars=280)

        # Thumbnails: ONLY respect media:thumbnail or media:content (no scraping)
        thumb = None
        media_thumbnail = getattr(entry, "media_thumbnail", None)
        if media_thumbnail and isinstance(media_thumbnail, list) and len(media_thumbnail) > 0:
            thumb = media_thumbnail[0].get("url")

        if not thumb:
            media_content = getattr(entry, "media_content", None)
            if media_content and isinstance(media_content, list) and len(media_content) > 0:
                thumb = media_content[0].get("url")

        # Skip broken rows
        if not link or not title:
            continue

        # Arsenal filter (or team-aware filter in future)
        if not _is_about_team(team_codes, source_name, title, summary, link):
            continue

        # Build a stable id
        aid = _hash_id(link, title)

        items.append(Article(
            id=aid,
            title=title,
            source=source_name,
            summary=summary,          # plain text
            url=link,
            thumbnailUrl=thumb,       # None unless provider includes media:*
            publishedUtc=_iso8601(published_dt),
            teams=team_codes,
            leagues=leagues
        ))

    return items
