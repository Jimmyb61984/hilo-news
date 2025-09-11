#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
app/fetcher.py  (config-driven)

Reads providers from data/sources.yaml via app.data_loader.get_sources().

Provider schema (per sources.yaml):
  providers:
    <name>:
      kind: "rss" | "html"
      url: "<feed_or_page_url>"
      teams: ["ARS", ...]
      leagues: ["EPL", ...]
      thumbnails: true | false              # allow thumbnails (RSS only for now)
      recency_days: 7 | null                # per-provider override for RSS
      allow_domains: ["bbc.co.uk", ...]     # OPTIONAL for RSS link filtering
      html:                                  # ONLY for kind=html
        allow_domains_regex: ["^https?://(www\\.)?arsenal\\.com"]
        allow_paths_regex:   ["^/news/"]
        selectors: ["a.u-media-object__link", "h3 a", ...]

Backward-compatible fallbacks are included for the known HTML sources if the YAML
doesn’t define their selectors yet.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
import hashlib
from urllib.parse import urlparse, urljoin
import re

import requests
import feedparser
from dateutil import parser as dateparser
from bs4 import BeautifulSoup

from .models import Article
from .data_loader import get_sources  # NEW: load providers from YAML

# ==============================
# CONFIG & GLOBAL DEFAULTS
# ==============================

USER_AGENT = "HiloFetcher/1.0 (+https://example.invalid)"

# Global recency window for RSS when provider.recency_days isn’t set
RECENCY_DAYS_DEFAULT: Optional[int] = 7

# Common junk filters (apply to both RSS & HTML)
JUNK_TITLE_PHRASES = {
    "do not sell or share my personal information",
    "skip to content",
    "get in touch here",  # BBC promo/junk
}
JUNK_URL_BITS = {"#comments"}

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return " ".join(str(s).split())

def make_id(source: str, url: str, title: str) -> str:
    h = hashlib.sha1()
    h.update((source + "|" + url + "|" + title).encode("utf-8"))
    return h.hexdigest()

def parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        # time.struct_time
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

def is_recent_generic(published: Optional[datetime], window_days: Optional[int]) -> bool:
    if window_days is None:
        return True
    if not published:
        return False
    return published >= (datetime.now(timezone.utc) - timedelta(days=window_days))

def is_junk_title(title: str) -> bool:
    if not title:
        return True
    t = title.strip().lower()
    if t in JUNK_TITLE_PHRASES:
        return True
    if len(t) < 5:
        return True
    return False

def is_junk_url(url: str) -> bool:
    if not url:
        return True
    u = url.strip()
    if not u.startswith("http"):
        return True
    for bit in JUNK_URL_BITS:
        if bit in u:
            return True
    return False

def extract_thumb_from_entry(entry: Any) -> Optional[str]:
    # RSS thumbnails if present
    try:
        media = entry.get("media_thumbnail") or entry.get("media_content")
        if isinstance(media, list):
            for m in media:
                u = m.get("url")
                if u:
                    return u
    except Exception:
        pass
    try:
        for lk in entry.get("links", []) or []:
            if "image" in (lk.get("type") or "") and lk.get("href"):
                return lk["href"]
    except Exception:
        pass
    return None

# ==============================
# Provider config helpers
# ==============================

def _providers() -> Dict[str, Dict[str, Any]]:
    cfg = get_sources() or {}
    return (cfg.get("providers") or {}) if isinstance(cfg, dict) else {}

def _prov(name: str) -> Dict[str, Any]:
    return _providers().get(name, {})  # may be {}

def _prov_kind(name: str) -> str:
    return (_prov(name).get("kind") or "").lower()

def _prov_url(name: str) -> str:
    return _prov(name).get("url") or ""

def _prov_teams(name: str) -> List[str]:
    t = _prov(name).get("teams") or []
    return list(t) if isinstance(t, list) else []

def _prov_leagues(name: str) -> List[str]:
    l = _prov(name).get("leagues") or []
    return list(l) if isinstance(l, list) else []

def _prov_recency(name: str) -> Optional[int]:
    r = _prov(name).get("recency_days")
    return int(r) if (r is not None and str(r).isdigit()) else RECENCY_DAYS_DEFAULT

def _prov_thumbs(name: str) -> bool:
    return bool(_prov(name).get("thumbnails", False))

def _prov_allow_domains(name: str) -> List[str]:
    v = _prov(name).get("allow_domains") or []
    return list(v) if isinstance(v, list) else []

def _prov_html_rules(name: str) -> Dict[str, Any]:
    v = _prov(name).get("html") or {}
    return v if isinstance(v, dict) else {}

# Built-in fallback HTML rules for known sources (used ONLY if YAML doesn’t define them)
FALLBACK_HTML_RULES: Dict[str, Dict[str, Any]] = {
    "arsenal_official": {
        "allow_domains_regex": [r"^https?://(www\.)?arsenal\.com"],
        "allow_paths_regex":   [r"^/news/"],
        "selectors": [
            "a.u-media-object__link",
            "a.o-promobox__link",
            "a.o-teaser__heading-link",
            "h3 a", "h2 a", "a[href^='/news/']",
        ],
    },
    "sky_sports": {
        "allow_domains_regex": [r"^https?://(www\.)?skysports\.com"],
        "allow_paths_regex":   [r"/arsenal", r"/arsenal-"],
        "selectors": [
            "a.news-list__headline-link",
            "h4 a[href*='/arsenal-']",
            "a[href*='/football/news/']",
            "a[href*='/arsenal-']",
        ],
    },
    "daily_mail": {
        "allow_domains_regex": [r"^https?://(www\.)?dailymail\.co\.uk"],
        "allow_paths_regex":   [r"^/sport/football/arsenal", r"^/sport/football/"],
        "selectors": ["h2 a", "h3 a", "a.linkro-darkred", "a.js-link-track"],
    },
    "evening_standard": {
        "allow_domains_regex": [r"^https?://(www\.)?standard\.co\.uk"],
        "allow_paths_regex":   [r"^/sport/football/arsenal"],
        "selectors": ["h2 a", "h3 a", "a.teaser__link", "a[href*='/sport/football/arsenal']"],
    },
    "the_times": {
        "allow_domains_regex": [r"^https?://(www\.)?thetimes\.co\.uk"],
        "allow_paths_regex":   [r"^/sport/football/teams/arsenal"],
        "selectors": ["h2 a", "h3 a", "a[href*='/sport/football/teams/arsenal']"],
    },
}

# ==============================
# RSS
# ==============================

def fetch_rss(
    url: str,
    team_codes: List[str],
    leagues: List[str],
    source_name: str,
    limit: Optional[int] = None,
) -> List[Article]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "close",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        content = resp.content
    except Exception:
        return []

    parsed = feedparser.parse(content)
    entries = list(parsed.entries or [])
    if limit and limit > 0:
        entries = entries[:limit]

    # Per-provider controls
    thumbs_ok = _prov_thumbs(source_name)
    recency_days = _prov_recency(source_name)
    rss_allowed_domains = set(d.lower() for d in _prov_allow_domains(source_name) if isinstance(d, str))

    items: List[Article] = []
    for e in entries:
        title = clean_text(e.get("title", ""))
        link = e.get("link", "")
        summary = clean_text(e.get("summary", ""))

        # BBC-specific filter: keep genuinely Arsenal items only
        if source_name == "bbc_sport":
            lk = (link or "").lower()
            tt = (title or "").lower()
            if ("arsenal" not in lk) and ("arsenal" not in tt):
                continue

        # published timestamp
        if e.get("published_parsed"):
            published_dt = parse_date(e.get("published_parsed"))
        elif e.get("updated_parsed"):
            published_dt = parse_date(e.get("updated_parsed"))
        else:
            published_dt = parse_date(e.get("published") or e.get("updated"))

        if not is_recent_generic(published_dt, recency_days):
            continue

        # Filters
        if is_junk_title(title) or is_junk_url(link):
            continue

        # Optional domain gate (if provided in YAML)
        if rss_allowed_domains:
            try:
                netloc = urlparse(link).netloc.lower()
            except Exception:
                continue
            # tolerate both bare domains and full hostnames in config
            if not any(netloc.endswith(d) or netloc == d for d in rss_allowed_domains):
                continue

        thumb = extract_thumb_from_entry(e) if thumbs_ok else None

        items.append(
            Article(
                id=make_id(source_name, link or "", title or ""),
                title=title or "",
                source=source_name,
                summary=summary or "",
                url=link or "https://example.invalid",
                thumbnailUrl=thumb,
                publishedUtc=(published_dt.isoformat() if published_dt else now_utc_iso()),
                teams=team_codes or [],
                leagues=leagues or [],
            )
        )

    return items

# ==============================
# HTML (HEADLINES ONLY)
# ==============================

def _compile_html_rules(source_name: str) -> Dict[str, Any]:
    rules = _prov_html_rules(source_name)
    if not rules:
        # fallback to built-ins for known sources
        rules = FALLBACK_HTML_RULES.get(source_name, {})

    # normalize fields
    dom_pats = [re.compile(p, re.I) for p in rules.get("allow_domains_regex", []) if isinstance(p, str)]
    path_pats = [re.compile(p, re.I) for p in rules.get("allow_paths_regex", []) if isinstance(p, str)]
    selectors = [s for s in (rules.get("selectors") or []) if isinstance(s, str)]
    if not selectors:
        selectors = ["a"]
    return {"domains": dom_pats, "paths": path_pats, "selectors": selectors}

def _allowed_url_html(href: str, base_url: str, rules: Dict[str, Any]) -> Optional[str]:
    if not href or href.startswith("#"):
        return None
    if not href.startswith("http"):
        href = urljoin(base_url, href)

    ok_domain = any(p.search(href) for p in rules["domains"]) if rules["domains"] else True
    ok_path   = any(p.search(href) for p in rules["paths"])   if rules["paths"]   else True
    if not (ok_domain and ok_path):
        return None

    if is_junk_url(href):
        return None
    return href

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

    rules = _compile_html_rules(source_name)
    selectors: List[str] = rules["selectors"]

    seen: set[str] = set()
    items: List[Article] = []

    def push(href: str, text: str):
        if not href or not text:
            return
        t = clean_text(text)
        if is_junk_title(t):
            return
        if href in seen:
            return
        seen.add(href)
        items.append(
            Article(
                id=make_id(source_name, href, t),
                title=t,
                source=source_name,
                summary="",
                url=href,
                thumbnailUrl=None,            # HTML: keep text-only (no article scraping)
                publishedUtc=now_utc_iso(),   # list pages rarely include per-item dates
                teams=team_codes or [],
                leagues=leagues or [],
            )
        )

    # Primary selectors
    for sel in selectors:
        for a in soup.select(sel):
            if limit and len(items) >= limit:
                break
            href_raw = a.get("href")
            text = a.get_text(" ", strip=True)
            href = _allowed_url_html(href_raw, url, rules) if href_raw else None
            if href:
                push(href, text)
        if limit and len(items) >= (limit or 0):
            break

    # Fallback: all anchors if nothing was captured
    if not items:
        for a in soup.find_all("a"):
            if limit and len(items) >= (limit or 0):
                break
            href_raw = a.get("href")
            text = a.get_text(" ", strip=True)
            href = _allowed_url_html(href_raw, url, rules) if href_raw else None
            if href:
                push(href, text)

    return items

