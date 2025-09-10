#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
app/fetcher.py
- RSS: normalized items from feed fields.
- HTML: safe HEADLINES-ONLY extraction (titles + links); NO image/page scraping.
- Returns Pydantic Article objects so main.py can dedupe/sort by .id/.publishedUtc.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
import hashlib
import re

import feedparser                  # pip install feedparser
from dateutil import parser as dateparser  # pip install python-dateutil
import requests                    # pip install requests
from bs4 import BeautifulSoup      # pip install beautifulsoup4

from .models import Article
from .sources import PROVIDERS

# ---------- Config ----------
USER_AGENT = "HiloFetcher/1.0 (+https://example.invalid)"
THUMBNAIL_ALLOWLIST = {"bbc_sport"}  # only use feed-provided media for these
RECENCY_DAYS: Optional[int] = 14     # RSS only; HTML lists rarely expose reliable dates

# ---------- Helpers ----------
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        if hasattr(value, "tm_year"):
            return datetime(*value[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        dt = dateparser.parse(str(value))
        if not dt:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None

def is_recent(published: Optional[datetime]) -> bool:
    if RECENCY_DAYS is None:
        return True
    if not published:
        return False
    return published >= (datetime.now(timezone.utc) - timedelta(days=RECENCY_DAYS))

def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return " ".join(str(s).split())

def make_id(source: str, url: str, title: str) -> str:
    h = hashlib.sha1()
    h.update((source + "|" + url + "|" + title).encode("utf-8"))
    return h.hexdigest()

def extract_thumb_from_entry(entry: Any) -> Optional[str]:
    # media_thumbnail / media_content
    try:
        media = entry.get("media_thumbnail") or entry.get("media_content")
        if isinstance(media, list):
            for m in media:
                u = m.get("url")
                if u:
                    return u
    except Exception:
        pass
    # enclosure with image/*
    try:
        for lk in entry.get("links", []) or []:
            if "image" in (lk.get("type") or "") and lk.get("href"):
                return lk["href"]
    except Exception:
        pass
    return None

# ---------- Public: RSS ----------
def fetch_rss(
    url: str,
    team_codes: List[str],
    leagues: List[str],
    source_name: str,
    limit: Optional[int] = None,
) -> List[Article]:
    feedparser.USER_AGENT = USER_AGENT
    parsed = feedparser.parse(url)
    entries = list(parsed.entries or [])
    if limit and limit > 0:
        entries = entries[:limit]

    items: List[Article] = []
    for e in entries:
        title = clean_text(getattr(e, "title", e.get("title", "")) if hasattr(e, "get") else getattr(e, "title", ""))
        link = getattr(e, "link", e.get("link", "")) if hasattr(e, "get") else getattr(e, "link", "")

        # summary/description
        summary = ""
        if hasattr(e, "summary"):
            summary = clean_text(e.summary)
        elif hasattr(e, "get"):
            summary = clean_text(e.get("summary", ""))

        # published
        published_dt = None
        if hasattr(e, "published_parsed") and e.published_parsed:
            published_dt = parse_date(e.published_parsed)
        elif hasattr(e, "updated_parsed") and e.updated_parsed:
            published_dt = parse_date(e.updated_parsed)
        else:
            cand = None
            if hasattr(e, "published"):
                cand = e.published
            elif hasattr(e, "get"):
                cand = e.get("published") or e.get("updated")
            published_dt = parse_date(cand)

        if not is_recent(published_dt):
            continue

        thumb = extract_thumb_from_entry(e) if source_name in THUMBNAIL_ALLOWLIST else None

        art = Article(
            id=make_id(source_name, link or "", title or ""),
            title=title or "",
            source=source_name,
            summary=summary or "",
            url=link or "https://example.invalid",  # HttpUrl required; link should exist
            thumbnailUrl=thumb,
            publishedUtc=(published_dt.isoformat() if published_dt else now_utc_iso()),
            teams=team_codes or [],
            leagues=leagues or [],
        )
        items.append(art)

    return items

# ---------- Public: HTML (headlines-only) ----------
HTML_RULES: Dict[str, Dict[str, Any]] = {
    "arsenal_official": {
        "allow_domains": [r"^https?://(www\.)?arsenal\.com"],
        "allow_paths":   [r"/news/"],
        "selectors": [
            "a.u-media-object__link",
            "a.o-promobox__link",
            "a.o-teaser__heading-link",
            "a",
        ],
    },
    "sky_sports": {
        "allow_domains": [r"^https?://(www\.)?skysports\.com"],
        "allow_paths":   [r"/football/", r"/arsenal"],
        "selectors": [
            "a.news-list__headline-link",
            "a.wdc-cta-card__link",
            "a",
        ],
    },
    "evening_standard": {
        "allow_domains": [r"^https?://(www\.)?standard\.co\.uk"],
        "allow_paths":   [r"/sport/football/arsenal"],
        "selectors": [
            "a[href*='/sport/football/arsenal']",
            "a",
        ],
    },
    "daily_mail": {
        "allow_domains": [r"^https?://(www\.)?dailymail\.co\.uk"],
        "allow_paths":   [r"/sport/football", r"/sport/teampages/arsenal"],
        "selectors": [
            "a.linkro-darkred",
            "a[href*='/sport/football/']",
            "a",
        ],
    },
    "the_times": {
        "allow_domains": [r"^https?://(www\.)?thetimes\.co\.uk"],
        "allow_paths":   [r"/sport/football/teams/arsenal"],
        "selectors": [
            "a[href*='/sport/football/teams/arsenal']",
            "a",
        ],
    },
}

def _allowed_url(source: str, href: str) -> bool:
    if not href or href.startswith("#"):
        return False
    rules = HTML_RULES.get(source, {})
    domains = [re.compile(pat, re.I) for pat in rules.get("allow_domains", [])]
    paths   = [re.compile(pat, re.I) for pat in rules.get("allow_paths", [])]
    ok_domain = any(r.search(href) for r in domains) if domains else True
    ok_path   = any(r.search(href) for r in paths) if paths else True
    return ok_domain and ok_path

def fetch_html_headlines(
    url: str,
    team_codes: List[str],
    leagues: List[str],
    source_name: str,
    limit: Optional[int] = None,
) -> List[Article]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rules = HTML_RULES.get(source_name, {})
    selectors: List[str] = rules.get("selectors", ["a"])
    seen: set[str] = set()
    items: List[Article] = []

    def push(href: str, text: str):
        if not href or not text:
            return
        if not href.startswith("http"):
            from urllib.parse import urljoin
            href = urljoin(url, href)
        if not _allowed_url(source_name, href):
            return
        key = href.strip()
        if key in seen:
            return
        seen.add(key)
        items.append(
            Article(
                id=make_id(source_name, key, text),
                title=clean_text(text),
                source=source_name,
                summary="",
                url=key,
                thumbnailUrl=None,
                # HTML listing pages rarely have reliable dates; use now to keep ordering stable
                publishedUtc=now_utc_iso(),
                teams=team_codes or [],
                leagues=leagues or [],
            )
        )

    for sel in selectors:
        for a in soup.select(sel):
            if limit and len(items) >= limit:
                break
            href = a.get("href")
            text = a.get_text(" ", strip=True)
            push(href, text)
        if limit and len(items) >= limit:
            break

    if not items:
        # Fallback: scan all anchors
        for a in soup.find_all("a"):
            if limit and len(items) >= (limit or 0):
                break
            href = a.get("href")
            text = a.get_text(" ", strip=True)
            push(href, text)

    return items
