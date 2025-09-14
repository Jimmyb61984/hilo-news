from __future__ import annotations
from typing import List, Dict, Any, Optional, Iterable
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
import feedparser
from bs4 import BeautifulSoup

from app.sources import PROVIDERS
from app.policy import canonicalize_provider

HTTP_TIMEOUT = 12.0
MAX_ITEMS_PER_SOURCE = 40  # raw fetch cap before policy

def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _parse_date_guess(text: str) -> Optional[datetime]:
    # Very light-weight parser to avoid adding heavy deps; feedparser often provides parsed time.
    try:
        # Try feedparser's mktime; else fall back to dateutil if available.
        import email.utils as eut
        tup = eut.parsedate_tz(text)
        if tup:
            ts = eut.mktime_tz(tup)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        pass
    try:
        from dateutil import parser as du
        return du.parse(text).astimezone(timezone.utc)
    except Exception:
        return None

def _fetch_url_text(client: httpx.Client, url: str) -> Optional[str]:
    try:
        r = client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        if r.status_code == 200 and r.text:
            return r.text
    except Exception:
        pass
    return None

def _extract_og_image(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        val = og["content"].strip()
        return urljoin(base_url, val)
    ld_img = soup.find("meta", attrs={"name": "twitter:image"})
    if ld_img and ld_img.get("content"):
        return urljoin(base_url, ld_img["content"].strip())
    # Fallback: look for first <img>
    img = soup.find("img")
    if img and img.get("src"):
        return urljoin(base_url, img["src"])
    return None

def _extract_arsenal_published(html: str) -> Optional[datetime]:
    """
    ArsenalOfficial publishes a precise article datetime in page metadata.
    We attempt several common locations; if not found, return None.
    """
    soup = BeautifulSoup(html, "lxml")
    # Preferred: <meta property="article:published_time" content="2025-09-14T16:45:00Z">
    meta_time = soup.find("meta", property="article:published_time")
    if meta_time and meta_time.get("content"):
        try:
            from dateutil import parser as du
            return du.parse(meta_time["content"]).astimezone(timezone.utc)
        except Exception:
            pass
    # Sometimes within <time datetime="...">
    t = soup.find("time")
    if t and t.get("datetime"):
        dt = _parse_date_guess(t["datetime"])
        if dt:
            return dt
    # Fallback: look for JSON-LD datePublished
    ld = soup.find("script", type="application/ld+json")
    if ld and ld.string:
        try:
            import json
            data = json.loads(ld.string)
            if isinstance(data, dict) and "datePublished" in data:
                dt = _parse_date_guess(data["datePublished"])
                if dt:
                    return dt
            if isinstance(data, list):
                for node in data:
                    if isinstance(node, dict) and "datePublished" in node:
                        dt = _parse_date_guess(node["datePublished"])
                        if dt:
                            return dt
        except Exception:
            pass
    return None

def _ensure_arsenal_publish_time(client: httpx.Client, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    For arsenal.com, fetch the article page to get reliable publish time.
    If we can't determine a trustworthy time, drop the item (protect chronology).
    """
    url = item.get("url", "")
    if "arsenal.com" not in url:
        return item  # not an arsenal.com item

    html = _fetch_url_text(client, url)
    if not html:
        return None

    dt = _extract_arsenal_published(html)
    if not dt:
        return None

    # Also try to set a strong hero image if missing
    if not item.get("imageUrl"):
        soup = BeautifulSoup(html, "lxml")
        img = _extract_og_image(soup, url)
        if img:
            item["imageUrl"] = img

    item["publishedUtc"] = _to_utc_iso(dt)
    return item

def _normalize_item(entry: Dict[str, Any], provider: str) -> Optional[Dict[str, Any]]:
    """
    Normalize common fields from RSS/HTML entry to Hilo DTO.
    Required: title, url, provider, type, publishedUtc.
    """
    title = (entry.get("title") or "").strip()
    url = (entry.get("url") or "").strip()
    if not title or not url:
        return None

    summary = (entry.get("summary") or "").strip()
    image = entry.get("imageUrl")
    published = entry.get("publishedUtc")

    # If no publishedUtc yet, try to parse any date-like field:
    if not published:
        for key in ("published", "pubDate", "date"):
            if entry.get(key):
                dt = _parse_date_guess(entry[key])
                if dt:
                    published = _to_utc_iso(dt)
                    break
    if not published:
        # As a last resort, use now — we avoid this for arsenal.com via ensure call
        published = _to_utc_iso(datetime.utcnow())

    return {
        "title": title,
        "url": url,
        "summary": summary,
        "imageUrl": image,
        "provider": canonicalize_provider(provider),
        "type": entry.get("type", "fan"),
        "publishedUtc": published,
    }

def _fetch_rss_source(client: httpx.Client, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    d = feedparser.parse(src["url"])
    for e in d.entries[:MAX_ITEMS_PER_SOURCE]:
        url = e.get("link") or ""
        title = e.get("title") or ""
        summary = e.get("summary") or e.get("subtitle") or ""
        published = None
        if e.get("published"):
            published = _to_utc_iso(_parse_date_guess(e["published"]) or datetime.utcnow())
        image = None
        # Try media:content or enclosure
        media = e.get("media_content") or e.get("media_thumbnail") or []
        if media and isinstance(media, list) and media[0].get("url"):
            image = media[0]["url"]
        enclosure = e.get("enclosures") or []
        if not image and enclosure and enclosure[0].get("href"):
            image = enclosure[0]["href"]

        out.append({
            "title": title,
            "url": url,
            "summary": summary,
            "imageUrl": image,
            "publishedUtc": published,
            "type": src.get("type", "fan"),
        })
    return out

def _fetch_html_source(client: httpx.Client, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    html = _fetch_url_text(client, src["url"])
    if not html:
        return out
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(src["selectors"]["item"])[:MAX_ITEMS_PER_SOURCE]
    for card in cards:
        a = card.select_one(src["selectors"]["link"])
        if not a or not a.get("href"):
            continue
        url = urljoin(src["base"], a["href"])
        title_el = card.select_one(src["selectors"]["title"]) if src["selectors"].get("title") else None
        title = title_el.get_text(strip=True) if title_el else (a.get("title") or a.get_text(strip=True) or "")
        if not title:
            continue
        # Summary optional
        summary = ""
        if src["selectors"].get("summary"):
            sum_el = card.select_one(src["selectors"]["summary"])
            if sum_el:
                summary = sum_el.get_text(strip=True)

        # Try image from list card first
        image = None
        img_el = card.select_one(src["selectors"].get("image", "")) if src["selectors"].get("image") else None
        if img_el:
            for key in ("data-src", "data-original", "src"):
                if img_el.get(key):
                    image = urljoin(src["base"], img_el.get(key))
                    break

        # Attempt to read list time if provided (many sites include a <time> tag)
        published = None
        if src["selectors"].get("time"):
            t = card.select_one(src["selectors"]["time"])
            if t:
                for key in ("datetime", "title", "aria-label"):
                    if t.get(key):
                        dt = _parse_date_guess(t.get(key))
                        if dt:
                            published = _to_utc_iso(dt)
                            break

        out.append({
            "title": title,
            "url": url,
            "summary": summary,
            "imageUrl": image,
            "publishedUtc": published,
            "type": src.get("type", "official"),
        })
    return out

def fetch_news(team_code: str = "ARS", allowed_types: Optional[set] = None) -> List[Dict[str, Any]]:
    """
    Single, canonical entry point used by app.main.
    - Fetches from all providers declared in app.sources.PROVIDERS for the given team.
    - Supports both 'rss' and 'html' providers.
    - For arsenal.com items, enforces article-page publish time or drops item.
    - Returns normalized items; policy will filter/cap/sort later.
    """
    items: List[Dict[str, Any]] = []
    with httpx.Client(headers={"User-Agent": "Hilo/2.0 (+https://hilo-news)"},
                      timeout=HTTP_TIMEOUT) as client:
        for provider_key, src in PROVIDERS.items():
            # Filter by team if we ever expand; for now ARS-only config
            if allowed_types and src.get("type") not in allowed_types:
                continue

            try:

