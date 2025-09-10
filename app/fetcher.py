import hashlib
from typing import List, Optional
from datetime import datetime, timezone
import re

import feedparser
from dateutil import parser as dtparse
from bs4 import BeautifulSoup

from .models import Article

# -------------------------------------------------
# Thumbnails policy: OFF by default for every source
# Add official sources later, e.g.: {"bbc_sport", "arsenal_official"}
# -------------------------------------------------
ALLOW_THUMBS: set[str] = set()

# -------------------------------------------------
# Team keyword filters (Arsenal-only for now)
# -------------------------------------------------
TEAM_KEYWORDS = {
    "ARS": [
        r"\barsenal\b",
        r"\bthe gunners\b",
        r"\bgooners?\b",
    ]
}
TEAM_REGEX = {tc: [re.compile(p, re.IGNORECASE) for p in pats]
              for tc, pats in TEAM_KEYWORDS.items()}
ARS_URL_HINTS = ["/arsenal", "arsenal.", "/team/arsenal", "/teams/arsenal"]


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
    Strip HTML, decode entities, clamp to ~max_chars without mid-word cuts.
    """
    if not htmlish:
        return ""
    soup = BeautifulSoup(htmlish, "lxml")
    text = soup.get_text(separator=" ", strip=True)
    text = " ".join(text.split())
    if max_chars and len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        if cut == -1:
            cut = max_chars
        text = text[:cut].rstrip() + "…"
    return text

def _is_about_team(team_codes: List[str], title: str, summary: str, link: str) -> bool:
    """
    True if clearly about the requested team(s). For now: Arsenal-only filter.
    """
    tcs = [tc.upper() for tc in team_codes]
    text_blob = f"{title} {summary}".strip()

    lower_link = (link or "").lower()
    if "ARS" in tcs and any(hint in lower_link for hint in ARS_URL_HINTS):
        return True

    for tc in tcs:
        regs = TEAM_REGEX.get(tc)
        if not regs:
            return True  # no filters defined for this team → pass
        if any(r.search(text_blob) for r in regs):
            return True

    return False


# ----------------------------
# Main fetch routine
# ----------------------------

def fetch_rss(url: str, team_codes: List[str], leagues: List[str], source_name: str) -> List[Article]:
    """
    Fetch RSS/Atom and return Article list.
    - Clean summaries (no HTML/entities).
    - NO scraping of images.
    - Thumbnails ONLY if source_name is explicitly whitelisted in ALLOW_THUMBS.
    - Filter generic feeds so Arsenal page shows Arsenal-only items.
    """
    parsed = feedparser.parse(url)
    items: List[Article] = []

    for entry in parsed.entries:
        title = _safe_str(getattr(entry, "title", ""))
        link = _safe_str(getattr(entry, "link", ""))

        if not link or not title:
            continue

        published_txt = _safe_str(getattr(entry, "published", "")) or _safe_str(getattr(entry, "updated", ""))
        published_dt: Optional[datetime] = None
        if published_txt:
            try:
                published_dt = dtparse.parse(published_txt)
            except Exception:
                published_dt = None

        raw_summary = _safe_str(getattr(entry, "summary", ""))
        raw_content = ""
        try:
            if hasattr(entry, "content") and isinstance(entry.content, list) and len(entry.content) > 0:
                raw_content = _safe_str(entry.content[0].value)
        except Exception:
            raw_content = ""

        # Plain-text summary (prefer content if present)
        summary_html = raw_content or raw_summary
        summary = _clean_text(summary_html, max_chars=280)

        # Arsenal filter (or team-aware filter in future)
        if not _is_about_team(team_codes, title, summary, link):
            continue

        # Thumbnails policy: only pass through media:* if the provider is allowed
        thumb = None
        if source_name in ALLOW_THUMBS:
            media_thumbnail = getattr(entry, "media_thumbnail", None)
            if media_thumbnail and isinstance(media_thumbnail, list) and len(media_thumbnail) > 0:
                thumb = media_thumbnail[0].get("url")
            if not thumb:
                media_content = getattr(entry, "media_content", None)
                if media_content and isinstance(media_content, list) and len(media_content) > 0:
                    thumb = media_content[0].get("url")

        aid = _hash_id(link, title)

        items.append(Article(
            id=aid,
            title=title,
            source=source_name,
            summary=summary,
            url=link,
            thumbnailUrl=thumb,   # None unless source whitelisted
            publishedUtc=_iso8601(published_dt),
            teams=team_codes,
            leagues=leagues
        ))

    return items

