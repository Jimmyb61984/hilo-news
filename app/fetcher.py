from __future__ import annotations

import re
import json
import hashlib
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from email.utils import parsedate_to_datetime

import httpx
import feedparser
from bs4 import BeautifulSoup

HTTP_TIMEOUT = 20.0

_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HiloNews/1.0)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ----------------- small utils -----------------
def _normalize_url(u: str) -> str:
    if not u:
        return ""
    try:
        p = urlparse(u)
        if not p.scheme:
            return "https://" + u
        return u
    except Exception:
        return u

def _clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())

def clean_summary_text(s: str) -> str:
    s = _clean_text(s or "")
    s = re.sub(r"^By\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s*[-–—]\s*", "", s)
    return s

def _truncate(s: str, n: int = 240) -> str:
    return (s[: n - 1] + "…") if s and len(s) > n else s

def _to_utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()

def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    # ISO variants
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    # RFC 822
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    # yyyy-mm-dd hh:mm[:ss]
    m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)", s)
    if m:
        try:
            return datetime.fromisoformat(m.group(1))
        except Exception:
            return None
    return None

def _make_id(source: str, title: str, url: str) -> str:
    base = f"{source}|{_clean_text(title)}|{_normalize_url(url)}".encode("utf-8", "ignore")
    return hashlib.sha1(base).hexdigest()

# ----------------- network -----------------
def fetch_html(url: str) -> Optional[BeautifulSoup]:
    try:
        with httpx.Client(headers=_DEFAULT_HEADERS, timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            r = client.get(url)
            if r.status_code >= 400 or not r.text:
                return None
            # built-in parser (no external dependency)
            return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None

def _meta(soup: BeautifulSoup, name_or_prop: str) -> Optional[Dict[str, str]]:
    meta = soup.find("meta", attrs={"property": name_or_prop}) or soup.find("meta", attrs={"name": name_or_prop})
    if meta:
        return dict(meta.attrs)
    return None

def pick_best_image_url(soup: BeautifulSoup, page_url: str) -> Optional[str]:
    m = _meta(soup, "og:image")
    if m and m.get("content"):
        return urljoin(page_url, m["content"])
    m = _meta(soup, "twitter:image")
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
                part = part.strip()
                m = re.match(r"(.+?)\s+(\d+)w", part)
                if m:
                    u, w = m.group(1).strip(), int(m.group(2))
                    if w > best_w:
                        best_w = w
                        best = urljoin(page_url, u)
        elif img.get("src"):
            best = best or urljoin(page_url, img["src"])
    return best

# ----------------- NEW: Arsenal.com publish time -----------------
def _extract_arsenal_published(soup: Optional[BeautifulSoup], page_url: str) -> Optional[str]:
    if not soup:
        return None

    for key in ("article:published_time", "og:article:published_time", "published_time", "pubdate"):
        m = _meta(soup, key)
        if m and m.get("content"):
            dt = _parse_date(m["content"])
            if dt:
                return _to_utc_iso(dt)

    t = soup.find("time", attrs={"datetime": True})
    if t and t.get("datetime"):
        dt = _parse_date(t["datetime"])
        if dt:
            return _to_utc_iso(dt)

    d = soup.find(attrs={"itemprop": "datePublished"})
    if d:
        value = d.get("content") or d.get("datetime") or d.get_text(" ", strip=True)
        dt = _parse_date(value or "")
        if dt:
            return _to_utc_iso(dt)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or "{}")
            if isinstance(data, dict):
                cand = data.get("datePublished") or data.get("uploadDate")
                if cand:
                    dt = _parse_date(str(cand))
                    if dt:
                        return _to_utc_iso(dt)
            elif isinstance(data, list):
                for node in data:
                    cand = (node or {}).get("datePublished") or (node or {}).get("uploadDate")
                    if cand:
                        dt = _parse_date(str(cand))
                        if dt:
                            return _to_utc_iso(dt)
        except Exception:
            continue

    by = soup.find(string=re.compile(r"Published|Posted", re.I))
    if by:
        m = re.search(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)", by)
        if m:
            dt = _parse_date(m.group(1))
            if dt:
                return _to_utc_iso(dt)

    return None

# ----------------- page enrichment -----------------
_page_img_cache: Dict[str, Optional[str]] = {}

def fetch_detail_image_and_summary(page_url: str) -> Dict[str, Optional[str]]:
    img_cached = _page_img_cache.get(page_url, None)
    soup = None
    if img_cached is None:
        soup = fetch_html(page_url)
        if not soup:
            _page_img_cache[page_url] = None
        else:
            _page_img_cache[page_url] = pick_best_image_url(soup, page_url)
    image_url = _page_img_cache.get(page_url, None)

    if soup is None:
        soup = fetch_html(page_url)

    summary = ""
    if soup:
        for key in ("og:description", "twitter:description", "description"):
            m = _meta(soup, key)
            if m and m.get("content"):
                summary = m["content"]
                break

    published_iso = _extract_arsenal_published(soup, page_url)

    return {
        "imageUrl": image_url,
        "summary": clean_summary_text(summary),
        "publishedUtc": published_iso,
    }

# ----------------- RSS/HTML adapters -----------------
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

    published_txt = entry.get("published") or entry.get("pubDate") or entry.get("updated") or entry.get("dc:date") or ""
    pub_dt = _parse_date(published_txt)

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
    soup = fetch_html(url)
    if not soup:
        return []
    nodes = soup.select(item_selector)[:limit]
    out: List[Dict[str, Any]] = []
    for node in nodes:
        try:
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

            pub_iso = None
            if date_selector:
                dnode = node.select_one(date_selector)
                if dnode:
                    raw = dnode.get("datetime") or dnode.get("title") or dnode.get_text(" ", strip=True)
                    pub_iso = _to_utc_iso(_parse_date(raw or ""))

            thumb = None
            if thumb_selector:
                img = node.select_one(thumb_selector)
                if img:
                    if img.has_attr("src"):
                        thumb = urljoin(url, img["src"])
                    elif img.has_attr("data-src"):
                        thumb = urljoin(url, img["data-src"])

            out.append(
                {
                    "id": _make_id(source, title, link),
                    "title": title,
                    "source": source,
                    "summary": summ or title,
                    "url": link,
                    "thumbnailUrl": thumb,
                    "publishedUtc": pub_iso,
                    "teams": team_codes or [],
                    "leagues": [],
                }
            )
        except Exception:
            continue
    return out

