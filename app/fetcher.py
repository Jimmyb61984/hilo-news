# app/fetcher.py
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from urllib.parse import urljoin

# ---------- HTTP client (shared) ----------
_DEFAULT_HEADERS = {
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

# ---------- Simple in-memory cache ----------
@dataclass
class _CacheEntry:
    value: Optional[str]
    expires_at: datetime

_IMG_CACHE: Dict[str, _CacheEntry] = {}
_IMG_TTL = timedelta(hours=24)

def _cache_get(url: str) -> Optional[str]:
    e = _IMG_CACHE.get(url)
    if not e:
        return None
    if datetime.now(timezone.utc) >= e.expires_at:
        _IMG_CACHE.pop(url, None)
        return None
    return e.value

def _cache_set(url: str, value: Optional[str]) -> None:
    _IMG_CACHE[url] = _CacheEntry(value=value, expires_at=datetime.now(timezone.utc) + _IMG_TTL)

# ---------- Utilities ----------
_WS_RE = re.compile(r"\s+")
_MAX_SUMMARY_LEN = 220

def _clean_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    txt = BeautifulSoup(s, "lxml").get_text(" ", strip=True)
    txt = _WS_RE.sub(" ", txt).strip()
    return txt

def clean_summary_text(html_or_text: str, *, limit: int = _MAX_SUMMARY_LEN) -> str:
    txt = _clean_text(html_or_text or "")
    return (txt[: limit - 1] + "…") if len(txt) > limit else txt

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

# ---------- High-res image picking ----------
def _meta(soup: BeautifulSoup, name: str):
    return soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})

def pick_best_image_url(soup: BeautifulSoup, page_url: str) -> Optional[str]:
    # 1) OpenGraph image
    m = _meta(soup, "og:image")
    if m and m.get("content"):
        return urljoin(page_url, m["content"])
    # 2) Twitter images
    for key in ("twitter:image:src", "twitter:image"):
        m = _meta(soup, key)
        if m and m.get("content"):
            return urljoin(page_url, m["content"])
    # 3) link rel
    link_img = soup.find("link", attrs={"rel": ["image_src", "thumbnail"]})
    if link_img and link_img.get("href"):
        return urljoin(page_url, link_img["href"])
    # 4) largest srcset or biggest <img>
    best = None
    best_w = 0
    for img in soup.find_all("img"):
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            for part in srcset.split(","):
                p = part.strip()
                m = re.match(r"(.+?)\s+(\d+)w", p)
                if m:
                    u, w = m.group(1).strip(), int(m.group(2))
                    if w > best_w:
                        best_w = w
                        best = urljoin(page_url, u)
        elif img.get("src"):
            u = urljoin(page_url, img["src"])
            try:
                w = int(img.get("width") or 0)
            except Exception:
                w = 0
            if w >= best_w:
                best_w = w
                best = u
    return best

def fetch_html(url: str) -> Optional[BeautifulSoup]:
    try:
        with httpx.Client(headers=_DEFAULT_HEADERS, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400 or not resp.text:
                return None
            return BeautifulSoup(resp.text, "lxml")
    except Exception:
        return None

def fetch_detail_image_and_summary(page_url: str) -> Dict[str, Optional[str]]:
    """Load article page once, pick best image + clean description (cached)."""
    cached = _cache_get(page_url)
    if cached is not None:
        return {"imageUrl": cached or None, "summary": ""}

    soup = fetch_html(page_url)
    if not soup:
        _cache_set(page_url, None)
        return {"imageUrl": None, "summary": ""}

    img = pick_best_image_url(soup, page_url)
    desc = None
    for key in ("og:description", "twitter:description", "description"):
        m = _meta(soup, key)
        if m and m.get("content"):
            desc = m["content"]
            break

    img_abs = img
    _cache_set(page_url, img_abs or "")
    return {"imageUrl": img_abs or None, "summary": clean_summary_text(desc or "")}

# ---------- RSS: map an entry ----------
def _entry_to_article(entry: Any, *, source_key: str, team_codes: Optional[List[str]]) -> Dict[str, Any]:
    title = _clean_text(getattr(entry, "title", "") or entry.get("title", ""))
    link = getattr(entry, "link", "") or entry.get("link", "")
    # summary fields vary: summary, description, content[0].value, etc.
    summary = (
        getattr(entry, "summary", "")
        or entry.get("summary", "")
        or entry.get("description", "")
        or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
        or title
    )
    # media thumbnails (common in RSS)
    thumb = None
    media_thumbnail = entry.get("media_thumbnail") or entry.get("media:thumbnail")
    if media_thumbnail:
        try:
            if isinstance(media_thumbnail, list):
                thumb = media_thumbnail[0].get("url") or media_thumbnail[0].get("href")
            elif isinstance(media_thumbnail, dict):
                thumb = media_thumbnail.get("url") or media_thumbnail.get("href")
        except Exception:
            thumb = None
    if not thumb:
        media_content = entry.get("media_content") or entry.get("media:content")
        try:
            if isinstance(media_content, list):
                thumb = media_content[0].get("url")
            elif isinstance(media_content, dict):
                thumb = media_content.get("url")
        except Exception:
            thumb = None

    # date
    published = (
        entry.get("published")
        or entry.get("pubDate")
        or entry.get("updated")
        or entry.get("dc:date")
        or ""
    )
    published_iso = _parse_date(published).isoformat()

    return {
        "id": _make_id(source_key, title, link),
        "title": title or "(untitled)",
        "source": source_key,
        "summary": clean_summary_text(summary),
        "url": link or "",
        "thumbnailUrl": thumb,
        "publishedUtc": published_iso,
        "teams": team_codes or [],
        "leagues": [],
        # imageUrl intentionally not set here; main.py will enforce policy:
        # - official: fetch hero image
        # - fan: force imageUrl=None (Panel2)
    }

# ---------- PUBLIC: RSS fetcher ----------
def fetch_rss(
    url: str,
    *,
    team_codes: Optional[List[str]] = None,
    source_key: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    source = source_key or "rss"
    parsed = feedparser.parse(url)
    items: List[Dict[str, Any]] = []
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
            href = urljoin(url, href) if href else ""

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
                    date_txt = (
                        date_el.get("datetime")
                        or date_el.get("data-time")
                        or date_el.get("title")
                        or date_el.get_text(" ", strip=True)
                        or ""
                    )
            published_iso = _parse_date(date_txt).isoformat()

            # Thumbnail (list page) — we keep it only as a fallback
            thumb = None
            if thumb_selector:
                img_el = el.select_one(thumb_selector)
                if img_el:
                    thumb = img_el.get("src") or img_el.get("data-src") or img_el.get("content")
                    if thumb:
                        thumb = urljoin(url, thumb)

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

