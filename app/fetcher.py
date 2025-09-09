import hashlib
from typing import List, Optional
from datetime import datetime, timezone

import feedparser
from dateutil import parser as dtparse
from bs4 import BeautifulSoup

from .models import Article


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
    # Parse & strip tags/entities
    soup = BeautifulSoup(htmlish, "lxml")
    text = soup.get_text(separator=" ", strip=True)
    text = " ".join(text.split())  # collapse whitespace

    if max_chars and len(text) > max_chars:
        cut = text.rfind(" ", 0, max_chars)
        if cut == -1:
            cut = max_chars
        text = text[:cut].rstrip() + "…"
    return text

def _first_img_src(htmlish: str) -> Optional[str]:
    if not htmlish:
        return None
    soup = BeautifulSoup(htmlish, "lxml")
    img = soup.find("img")
    if img:
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if src and isinstance(src, str) and src.strip():
            return src.strip()
    return None


# ----------------------------
# Main fetch routine
# ----------------------------

def fetch_rss(url: str, team_codes: List[str], leagues: List[str], source_name: str) -> List[Article]:
    """
    Download an RSS/Atom feed and return a list of Article objects.
    Now normalizes summary to plain text and extracts a thumbnail if needed.
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
        # Some feeds put full HTML in entry.content[0].value
        try:
            if hasattr(entry, "content") and isinstance(entry.content, list) and len(entry.content) > 0:
                raw_content = _safe_str(entry.content[0].value)
        except Exception:
            raw_content = ""

        # Clean, human-friendly summary
        # Prefer content if present; fall back to summary
        summary_html = raw_content or raw_summary
        summary = _clean_text(summary_html, max_chars=280)

        # Thumbnail: media:thumbnail/media:content first; else first <img> in content/summary
        thumb = None

        media_thumbnail = getattr(entry, "media_thumbnail", None)
        if media_thumbnail and isinstance(media_thumbnail, list) and len(media_thumbnail) > 0:
            thumb = media_thumbnail[0].get("url")

        if not thumb:
            media_content = getattr(entry, "media_content", None)
            if media_content and isinstance(media_content, list) and len(media_content) > 0:
                thumb = media_content[0].get("url")

        if not thumb:
            thumb = _first_img_src(raw_content) or _first_img_src(raw_summary)

        # Build a stable id
        aid = _hash_id(link, title)

        # Skip broken rows
        if not link or not title:
            continue

        items.append(Article(
            id=aid,
            title=title,
            source=source_name,
            summary=summary,          # <-- plain text now (no tags, no &#8217;)
            url=link,
            thumbnailUrl=thumb,       # <-- found via media:* or first <img> fallback
            publishedUtc=_iso8601(published_dt),
            teams=team_codes,
            leagues=leagues
        ))

    return items

