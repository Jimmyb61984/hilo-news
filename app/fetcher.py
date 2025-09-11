# app/fetcher.py
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser


# ---------- HTTP client (shared) ----------
_DEFAULT_HEADERS = {
    # Reasonable desktop UA to avoid blocks and get full meta tags
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
}

HTTP_TIMEOUT = 10.0


# ---------- Simple in-memory thumbnail cache ----------
@dataclass
class _CacheEntry:
    value: Optional[str]
    expires_at: datetime


_THUMB_CACHE: Dict[str, _CacheEntry] = {}
_THUMB_TTL = timedelta(hours=24)


def _cache_get(url: str) -> Optional[str]:
    entry = _THUMB_CACHE.get(url)
    if not entry:
        return None
    if datetime.now(timezone.utc) >= entry.expires_at:
        _THUMB_CACHE.pop(url, None)
        return None
    return entry.value


def _cache_set(url: str, value: Optional[str]) -> None:
    _THUMB_CACHE[url] = _CacheEntry(value=value, expires_at=datetime.now(timezone.utc) + _THUMB_TTL)


# ---------- Utilities ----------
_WS_RE = re.compile(r"\s+")
_MAX_SUMMARY_LEN = 220


def _clean_text(s: str) -> str:
    """Strip HTML, decode entities, collapse whitespace, trim."""
    if not s:
        return ""
    # Decode HTML entities first so < &amp; > don't confuse the parser.
    s = html.unescape(s)
    # Strip tags via BeautifulSoup (handles weird fragments too)
    txt = BeautifulSoup(s, "lxml").get_text(" ", strip=True)
    txt = _WS_RE.sub(" ", txt).strip()
    return txt


def _truncate(s: str, limit: int = _MAX_SUMMARY_LEN) -> str:
    if not s:
        return ""
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "…"


def _make_id(*parts: str) -> str:
    h = hashlib.sha1()
    for p in parts:
        if p:
            h.update(p.encode("utf-8", errors="ignore"))
            h.update(b"|")
    return h.hexdigest()


def _parse_date(maybe_date: Any) -> datetime:
    if not maybe_date:
        return datetime.now(timezone.utc)
    try:
        dt = dateparser.parse(str(maybe_date))
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _extract_og_image_from_html(soup: BeautifulSoup) -> Optional[str]:
    # Try common meta tags in priority order
    selectors = [
        ('meta[property="og:image"]', "content"),
        ('meta[name="og:image"]', "content"),
        ('meta[name="twitter:image"]', "content"),
        ('meta[property="twitter:image"]', "content"),
        ('meta[property="og:image:url"]', "content"),
        ("link[rel='image_src']", "href"),
    ]
    for css, attr in selectors:
        tag = soup.select_one(css)
        if tag and tag.get(attr):
            return tag.get(attr).strip()
    # Fallback: first reasonably large <img>
    img = soup.find("img")
    if img and img.get("src"):
        return img["src"].strip()
    return None


def _resolve_absolute(url: str, maybe_relative: Optional[str]) -> Optional[str]:
    if not maybe_relative:
        return None
    try:
        # Simple resolver without importing urllib.parse.urljoin (we can use it too)
        from urllib.parse import urljoin

        return urljoin(url, maybe_relative)
    except Exception:
        return maybe_relative


def _fetch_html_thumbnail(page_url: str) -> Optional[str]:
    """Get (and cache) a representative image for an article page."""
    # Cache first
    cached = _cache_get(page_url)
    if cached is not None:
        return cached

    try:
        with httpx.Client(headers=_DEFAULT_HEADERS, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(page_url)
            if resp.status_code >= 400 or not resp.text:
                _cache_set(page_url, None)
                return None
            soup = BeautifulSoup(resp.text, "lxml")
            img = _extract_og_image_from_html(soup)
            img = _resolve_absolute(resp.url, img)
            _cache_set(page_url, img)
            return img
    except Exception:
        _cache_set(page_url, None)
        return None


def _entry_to_article(
    entry: Any,
    *,
    source_key: str,
    team_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    # Title
    title = _clean_text(getattr(entry, "title", "") or entry.get("title", ""))
    # Summary: try 'summary' then 'description' then first content value
    summary_raw = (
        getattr(entry, "summary", None)
        or entry.get("summary")
        or entry.get("description")
        or (entry.get("content", [{}]) or [{}])[0].get("value")
        or ""
    )
    summary = _truncate(_clean_text(summary_raw))

    # Link
    link = getattr(entry, "link", None) or entry.get("link") or ""
    link = html.unescape(str(link)).strip()

    # Date
    published = (
        entry.get("published")
        or entry.get("updated")
        or getattr(entry, "published", None)
        or getattr(entry, "updated", None)
    )
    published_dt = _parse_date(published)
    published_iso = published_dt.isoformat()

    # Thumbnail from feed if present
    thumb = None
    # Many feeds use media_thumbnail, media_content
    media = entry.get("media_thumbnail") or entry.get("media_content")
    if media and isinstance(media, list) and media[0].get("url"):
        thumb = media[0]["url"]
    # Some put it under 'image'
    if not thumb:
        img = entry.get("image")
        if isinstance(img, dict) and img.get("href"):
            thumb = img["href"]

    # If still no thumb, attempt to fetch from the page (guarded + cached)
    if not thumb and link:
        thumb = _fetch_html_thumbnail(link)

    # Final fallback for summary
    if not summary:
        # If we got no clean text, use the title as the summary fallback
        summary = title

    return {
        "id": _make_id(source_key, title, link),
        "title": title or "(untitled)",
        "source": source_key,
        "summary": summary,
        "url": link,
        "thumbnailUrl": thumb,
        "publishedUtc": published_iso,
        "teams": team_codes or [],
        "leagues": [],
    }


# ---------- PUBLIC: RSS fetcher ----------
def fetch_rss(
    url: str,
    *,
    team_codes: Optional[List[str]] = None,
    source_key: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """
    Back-compat function expected by main.py.
    Parses an RSS/Atom feed and returns normalized articles.
    """
    source = source_key or "rss"
    parsed = feedparser.parse(url)
    items = []
    for entry in (parsed.entries or [])[:limit]:
        try:
            items.append(_entry_to_article(entry, source_key=source, team_codes=team_codes))
        except Exception:
            continue
    return items


# ---------- PUBLIC: Generic HTML headlines scraper ----------
def fetch_html_headlines(
    url: str,
    *,
    item_selector: str,
    title_selector: str,
    link_selector: str,
    summary_selector: Optional[str] = None,
    date_selector: Optional[str] = None,
    thumb_selector: Optional[str] = None,
    team_codes: Optional[List[str]] = None,
    source_key: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Back-compat function expected by main.py.

    Scrapes a page using CSS selectors:
      - item_selector: CSS to select the container per article
      - title_selector: inside each item
      - link_selector: inside each item (href)
      - summary_selector: optional summary text inside each item
      - date_selector: optional publish date text/attr
      - thumb_selector: optional <img> inside each item

    NOTE: This is intentionally generic to match earlier usage.
    """
    source = source_key or "html"

    try:
        with httpx.Client(headers=_DEFAULT_HEADERS, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400 or not resp.text:
                return []
            soup = BeautifulSoup(resp.text, "lxml")

        cards = soup.select(item_selector) if item_selector else []
        out: List[Dict[str, Any]] = []
        for el in cards[:limit]:
            # Title
            title_el = el.select_one(title_selector) if title_selector else None
            title = _clean_text(title_el.get_text(" ", strip=True) if title_el else "")

            # Link
            link_el = el.select_one(link_selector) if link_selector else None
            href = (link_el.get("href") if link_el else "") or ""
            href = _resolve_absolute(url, href) or ""

            # Summary (optional)
            summ = ""
            if summary_selector:
                summ_el = el.select_one(summary_selector)
                if summ_el:
                    summ = _truncate(_clean_text(summ_el.get_text(" ", strip=True)))

            # Date (optional)
            date_txt = ""
            if date_selector:
                date_el = el.select_one(date_selector)
                if date_el:
                    # Some sites store date in attribute like datetime, data-time, etc.
                    date_txt = (
                        date_el.get("datetime")
                        or date_el.get("data-time")
                        or date_el.get("title")
                        or date_el.get_text(" ", strip=True)
                        or ""
                    )
            published_iso = _parse_date(date_txt).isoformat()

            # Thumbnail
            thumb = None
            if thumb_selector:
                img_el = el.select_one(thumb_selector)
                if img_el:
                    thumb = img_el.get("src") or img_el.get("data-src") or img_el.get("content")
                    thumb = _resolve_absolute(url, thumb)
            if not thumb and href:
                thumb = _fetch_html_thumbnail(href)

            # Fallbacks
            if not summ:
                summ = title

            out.append(
                {
                    "id": _make_id(source, title, href),
                    "title": title or "(untitled)",
                    "source": source,
                    "summary": summ,
                    "url": href,
                    "thumbnailUrl": thumb,
                    "publishedUtc": published_iso,
                    "teams": team_codes or [],
                    "leagues": [],
                }
            )

        return out
    except Exception:
        return []


# ---------- Optional convenience used by newer code paths ----------
def clean_summary_text(html_or_text: str, *, limit: int = _MAX_SUMMARY_LEN) -> str:
    """Exported helper for other modules/tests."""
    return _truncate(_clean_text(html_or_text or ""), limit=limit)



