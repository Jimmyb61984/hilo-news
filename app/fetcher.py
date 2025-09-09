import hashlib
from typing import List, Optional
from datetime import datetime, timezone

import feedparser
from dateutil import parser as dtparse

from .models import Article

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

def fetch_rss(url: str, team_codes: List[str], leagues: List[str], source_name: str) -> List[Article]:
    """
    Download an RSS/Atom feed and return a list of Article objects.
    This is intentionally simple and robust for MVP.
    """
    parsed = feedparser.parse(url)

    items: List[Article] = []
    for entry in parsed.entries:
        title = _safe_str(getattr(entry, "title", ""))
        link = _safe_str(getattr(entry, "link", ""))

        # Try published > updated > now
        published_txt = _safe_str(getattr(entry, "published", "")) or _safe_str(getattr(entry, "updated", ""))
        published_dt: Optional[datetime] = None
        if published_txt:
            try:
                published_dt = dtparse.parse(published_txt)
            except Exception:
                published_dt = None

        summary = _safe_str(getattr(entry, "summary", ""))

        # Thumbnail if present (media:thumbnail or links)
        thumb = None
        media_thumbnail = getattr(entry, "media_thumbnail", None)
        if media_thumbnail and isinstance(media_thumbnail, list) and len(media_thumbnail) > 0:
            thumb = media_thumbnail[0].get("url")
        if not thumb:
            media_content = getattr(entry, "media_content", None)
            if media_content and isinstance(media_content, list) and len(media_content) > 0:
                thumb = media_content[0].get("url")

        # Build a stable id
        aid = _hash_id(link, title)

        if not link or not title:
            # skip broken rows
            continue

        items.append(Article(
            id=aid,
            title=title,
            source=source_name,
            summary=summary,
            url=link,
            thumbnailUrl=thumb,
            publishedUtc=_iso8601(published_dt),
            teams=team_codes,
            leagues=leagues
        ))

    return items
