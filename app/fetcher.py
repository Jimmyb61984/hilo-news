# app/fetcher.py
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

# ---------- HTTP client (shared) --------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    )
}
HTTP_TIMEOUT = 8

_session = requests.Session()
_session.headers.update(DEFAULT_HEADERS)

# ---------- Helpers: time parsing for ArsenalOfficial ----------
def _to_utc_iso(dt: datetime) -> str:
    """Return an ISO8601 string in UTC for a datetime that may be naive or tz-aware."""
    if not isinstance(dt, datetime):
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def _extract_arsenal_published(soup: BeautifulSoup) -> Optional[str]:
    """Try multiple selectors on arsenal.com pages to find the real published time."""
    if not soup:
        return None
    candidates = [
        'meta[property="article:published_time"]',
        'meta[name="article:published_time"]',
        'meta[property="og:article:published_time"]',
        'meta[name="og:article:published_time"]',
        'meta[name="publish-date"]',
        'meta[property="article:modified_time"]',
        'time[datetime]',
        'span[itemprop="datePublished"]',
    ]
    for sel in candidates:
        el = soup.select_one(sel)
        if not el:
            continue
        val = el.get("content") or el.get("datetime") or el.get_text(strip=True) or ""
        if not val:
            continue
        try:
            dt = dateparser.parse(val)
            if dt:
                return _to_utc_iso(dt)
        except Exception:
            continue
    return None

# ---------- Tiny cache (optional) ----------
_cache: Dict[str, Tuple[float, Any]] = {}

def _cache_key(prefix: str, *parts: str) -> str:
    h = hashlib.sha1(("|".join([prefix, *parts])).encode("utf-8")).hexdigest()
    return f"{prefix}:{h}"

def _cache_get(key: str, ttl: int = 600) -> Optional[Any]:
    row = _cache.get(key)
    if not row:
        return None
    ts, val = row
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return val

def _cache_set(key: str, val: Any) -> None:
    _cache[key] = (time.time(), val)

# ---------- Utilities ----------
def _clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    s = s.strip()
    s = re.sub(r"\s+", " ", s)
    return s

def clean_summary_text(s: Optional[str]) -> str:
    return _clean_text(s)

def normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    url = re.sub(r"#.*$", "", url)  # drop hash fragments
    return url

def parse_date(d: Optional[str]) -> Optional[str]:
    if not d:
        return None
    try:
        dt = dateparser.parse(d)
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None

def _entry_to_article(
    entry: Any,
    source_key: str,
    team_codes: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    link = getattr(entry, "link", None) or entry.get("link") if isinstance(entry, dict) else None
    if not link:
        return None
    title = getattr(entry, "title", None) or entry.get("title") if isinstance(entry, dict) else None
    summary = getattr(entry, "summary", None) or entry.get("summary") if isinstance(entry, dict) else None

    published = None
    for key in ["published", "updated", "pubDate", "dc:date"]:
        v = getattr(entry, key, None) or (entry.get(key) if isinstance(entry, dict) else None)
        if v:
            published = parse_date(v)
            if published:
                break

    a = {
        "id": hashlib.sha1(f"{source_key}|{normalize_url(link)}".encode()).hexdigest(),
        "title": _clean_text(title),
        "source": source_key,
        "summary": clean_summary_text(summary),
        "url": normalize_url(link),
        "thumbnailUrl": None,
        "publishedUtc": published,
        "teams": team_codes or [],
        "leagues": [],
        "imageUrl": None,
    }
    media = getattr(entry, "media_thumbnail", None) or getattr(entry, "media_content", None)
    if media and isinstance(media, list):
        first = media[0]
        thumb = first.get("url") if isinstance(first, dict) else None
        a["thumbnailUrl"] = normalize_url(thumb)
    return a

def fetch_rss(url: str, source_key: str, team_codes: Optional[List[str]] = None, timeout: int = HTTP_TIMEOUT) -> List[Dict[str, Any]]:
    cache_k = _cache_key("rss", url)
    cached = _cache_get(cache_k, ttl=180)
    if cached is not None:
        return cached

    resp = _session.get(url, timeout=timeout)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)

    items: List[Dict[str, Any]] = []
    for e in parsed.entries:
        a = _entry_to_article(e, source_key=source_key, team_codes=team_codes)
        if a:
            items.append(a)
    _cache_set(cache_k, items)
    return items

# ---------- Detail page enrichment ----------
def fetch_detail_image_and_summary(url: str, timeout: int = 8) -> Dict[str, Any]:
    """
    Fetch the article page and try to extract a lead image and a better summary.
    (Used by 'official' providers enrichment in main.py.)
    """
    result: Dict[str, Any] = {"imageUrl": None, "summary": None}
    if not url:
        return result

    cache_k = _cache_key("detail", url)
    cached = _cache_get(cache_k, ttl=300)
    if cached is not None:
        return cached

    try:
        resp = _session.get(url, timeout=timeout)
        resp.raise_for_status()
    except Exception:
        return result

    try:
        soup = BeautifulSoup(resp.text, "lxml")
    except Exception:
        soup = None

    # Image preference: Open Graph image first, then any <img> in article body
    img = None
    if soup:
        og = soup.select_one('meta[property="og:image"], meta[name="og:image"]')
        if og and og.get("content"):
            img = og.get("content")
        if not img:
            art_img = soup.select_one("article img, .article img, .news-article img")
            if art_img and art_img.get("src"):
                img = art_img.get("src")
    if img:
        img = normalize_url(urljoin(url, img))

    # Summary: try description/og:description, else first paragraph
    desc = None
    if soup:
        meta_desc = soup.select_one('meta[name="description"], meta[property="og:description"]')
        if meta_desc and meta_desc.get("content"):
            desc = meta_desc.get("content")
        if not desc:
            p = soup.select_one("article p, .article p, .news-article p")
            if p:
                desc = p.get_text(" ", strip=True)

    # Published time (Arsenal official only)
    published_utc = None
    try:
        if "arsenal.com" in (url or "") and soup:
            published_utc = _extract_arsenal_published(soup)
    except Exception:
        published_utc = None

    data = {
        "imageUrl": img or None,
        "summary": clean_summary_text(desc or ""),
        "publishedUtc": published_utc,
    }
    _cache_set(cache_k, data)
    return data

# ---------- HTML-headlines (fan providers) ----------
def fetch_html_headlines(url: str, source_key: str, team_codes: Optional[List[str]] = None, timeout: int = HTTP_TIMEOUT) -> List[Dict[str, Any]]:
    # ...
    # existing logic unchanged
    # ...
    return []

# ---------- Provider utilities ----------
def best_image_for_item(item: Dict[str, Any]) -> Optional[str]:
    img = item.get("imageUrl") or item.get("thumbnailUrl")
    return normalize_url(img) if img else None

