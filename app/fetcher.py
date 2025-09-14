# app/fetcher.py
from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, urljoin

import feedparser
import httpx
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

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

# ---------- Simple in-memory image cache for detail enrichment ----------
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

# ---------- Text utils ----------
_WS_RE = re.compile(r"\s+", re.UNICODE)
_MAX_SUMMARY_LEN = 280

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

# ---------- URL normalization & meta helpers ----------
def _normalize_url(u: str) -> str:
    if not u:
        return ""
    pu = urlparse(u.strip())
    host = pu.netloc.lower()
    path = pu.path.rstrip("/")
    kept = [(k, v) for (k, v) in parse_qsl(pu.query, keep_blank_values=True)
            if not k.lower().startswith(("utm_", "gclid", "fbclid"))]
    query = urlencode(kept, doseq=True)
    return urlunparse((pu.scheme.lower(), host, path, pu.params, query, ""))

def _meta(soup: BeautifulSoup, name: str):
    return soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})

# ---------- Date helpers ----------
def _to_utc_iso(dt: datetime) -> str:
    """Consistent ISO formatting in UTC."""
    return dt.astimezone(timezone.utc).isoformat()

# ---------- HTML fetching ----------
def fetch_html(url: str) -> Optional[BeautifulSoup]:
    try:
        with httpx.Client(headers=_DEFAULT_HEADERS, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            resp = client.get(url)
            if resp.status_code >= 400 or not resp.text:
                return None
            return BeautifulSoup(resp.text, "lxml")
    except Exception:
        return None

# ---------- Publish time extraction ----------
def _parse_date(maybe_date: Any) -> Optional[datetime]:
    if not maybe_date:
        return None
    try:
        dt = dateparser.parse(str(maybe_date))
        if not dt:
            return None
        if not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def _extract_published_from_html(html_soup: BeautifulSoup) -> Optional[datetime]:
    # JSON-LD blocks
    for tag in html_soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, dict):
                iso = data.get("datePublished") or data.get("dateCreated")
                d = _parse_date(iso)
                if d:
                    return d
            elif isinstance(data, list):
                for node in data:
                    if isinstance(node, dict):
                        iso = node.get("datePublished") or node.get("dateCreated")
                        d = _parse_date(iso)
                        if d:
                            return d
        except Exception:
            pass
    # Meta tags
    for key in ("article:published_time", "og:article:published_time", "datePublished", "publish_date", "pubdate", "parsely-pub-date"):
        m = _meta(html_soup, key)
        if m and m.get("content"):
            d = _parse_date(m["content"])
            if d:
                return d
    # <time datetime="...">
    t = html_soup.find("time", {"datetime": True})
    if t:
        d = _parse_date(t.get("datetime"))
        if d:
            return d
    return None

def _fetch_published_for_url(url: str, timeout: float = 7.0) -> Optional[datetime]:
    try:
        with httpx.Client(headers=_DEFAULT_HEADERS, timeout=timeout, follow_redirects=True) as client:
            r = client.get(url)
        if r.status_code < 400 and r.text:
            soup = BeautifulSoup(r.text, "lxml")
            return _extract_published_from_html(soup)
    except Exception:
        pass
    return None

def _extract_arsenal_published(soup: BeautifulSoup) -> Optional[datetime]:
    """
    Arsenal.com specific publish extraction.
    Falls back to generic extraction, but tries common patterns first.
    """
    # Common Arsenal patterns are already covered by generic extraction;
    # keep this separate so we can extend if they change markup.
    d = _extract_published_from_html(soup)
    if d:
        return d
    # Fallback: look for <meta itemprop="datePublished"> or time[itemprop]
    m = soup.find("meta", attrs={"itemprop": "datePublished"})
    if m and m.get("content"):
        d = _parse_date(m["content"])
        if d:
            return d
    t = soup.find("time", attrs={"itemprop": "datePublished"})
    if t and t.get("datetime"):
        d = _parse_date(t["datetime"])
        if d:
            return d
    return None

# ---------- High-res image picking ----------
def pick_best_image_url(soup: BeautifulSoup, page_url: str) -> Optional[str]:
    m = _meta(soup, "og:image")
    if m and m.get("content"):
        return urljoin(page_url, m["content"])
    for key in ("twitter:image:src", "twitter:image"):
        m = _meta(soup, key)
        if m and m.get("content"):
            return urljoin(page_url, m["content"])
    link_img = soup.find("link", attrs={"rel": ["image_src", "thumbnail"]})
    if link_img and link_img.get("href"):
        return urljoin(page_url, link_img["href"])

    best = None
    best_w = 0
    for img in soup.find_all("img"):
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            for part in srcset.split(","):
                p = part.strip()
                mm = re.match(r"(.+?)\s+(\d+)w", p)
                if mm:
                    u, w = mm.group(1).strip(), int(mm.group(2))
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

# ---------- Detail enrichment ----------
def fetch_detail_image_and_summary(page_url: str) -> Dict[str, Optional[str]]:
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
def _entry_to_article(entry: Any, *, source_key: str, team_codes: Optional[List[str]]) -> Optional[Dict[str, Any]]:
    title = _clean_text(getattr(entry, "title", "") or entry.get("title", ""))
    link_raw = getattr(entry, "link", "") or entry.get("link", "")
    link = _normalize_url(link_raw)

    if not title or not link:
        return None

    summary = (
        getattr(entry, "summary", "")
        or entry.get("summary", "")
        or entry.get("description", "")
        or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
        or title
    )

    # ---- Publish time (strict for ArsenalOfficial) ----
    pub_dt: Optional[datetime] = None
    if source_key == "ArsenalOfficial":
        # Always trust the article page time (and drop if missing)
        soup = fetch_html(link)
        if soup:
            pub_dt = _extract_arsenal_published(soup)
        if pub_dt is None:
            return None
    else:
        # Try RSS dates first, then page scrape
        published = (
            entry.get("published")
            or entry.get("pubDate")
            or entry.get("updated")
            or entry.get("dc:date")
            or ""
        )
        pub_dt = _parse_date(published) or _fetch_published_for_url(link)

        if pub_dt is None:
            return None

    return {
        "id": _make_id(source_key, title, link),
        "title": title,
        "source": source_key,
        "summary": clean_summary_text(summary),
        "url": link,
        "thumbnailUrl": None,
        "publishedUtc": _to_utc_iso(pub_dt),
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
    source = source_key or "rss"
    parsed = feedparser.parse(url)
    items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    for entry in (parsed.entries or [])[:limit]:
        try:
            art = _entry_to_article(entry, source_key=source, team_codes=team_codes)
            if not art:
                continue
            key = _normalize_url(art["url"])
            if key and key not in seen:
                seen.add(key)
                items.append(art)
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
        soup = fetch_html(url)
        if not soup:
            return []
        nodes = soup.select(item_selector)[:limit]
        out: List[Dict[str, Any]] = []
        seen: set[str] = set()

        for node in nodes:
            tnode = node.select_one(title_selector)
            title = _clean_text(tnode.get_text(" ", strip=True) if tnode else "")

            lnode = node.select_one(link_selector)
            href = (lnode.get("href") if lnode and lnode.has_attr("href") else "").strip()
            link = _normalize_url(urljoin(url, href)) if href else ""

            if not title or not link:
                continue

            summ = ""
            if summary_selector:
                snode = node.select_one(summary_selector)
                if snode:
                    summ = clean_summary_text(snode.get_text(" ", strip=True))

            thumb = None
            if thumb_selector:
                img = node.select_one(thumb_selector)
                if img:
                    if img.has_attr("src"):
                        thumb = urljoin(url, img["src"])
                    elif img.has_attr("data-src"):
                        thumb = urljoin(url, img["data-src"])

            pub_dt: Optional[datetime] = None
            if date_selector:
                dnode = node.select_one(date_selector)
                if dnode:
                    pub_dt = _parse_date(dnode.get_text(" ", strip=True))

            if pub_dt is None:
                page_soup = fetch_html(link)
                if page_soup:
                    # Arsenal page special-case when scraping their listings
                    if source == "ArsenalOfficial":
                        pub_dt = _extract_arsenal_published(page_soup)
                    else:
                        pub_dt = _extract_published_from_html(page_soup)

            if pub_dt is None:
                continue

            key = _normalize_url(link)
            if key in seen:
                continue
            seen.add(key)

            out.append(
                {
                    "id": _make_id(source, title, link),
                    "title": title,
                    "source": source,
                    "summary": summ,
                    "url": link,
                    "thumbnailUrl": thumb,
                    "publishedUtc": _to_utc_iso(pub_dt),
                    "teams": team_codes or [],
                    "leagues": [],
                }
            )

        return out
    except Exception:
        return []

