#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetcher.py
- Fetch both RSS and HTML providers from sources.py.
- RSS: normalized items from feed fields.
- HTML: safe HEADLINES-ONLY extraction (titles + links); NO image/page scraping.
- Writes JSON: out/<source>.json and out/all.json

Usage:
  python fetcher.py                 # fetch all providers
  python fetcher.py --source sky_sports,arsenal_official --limit 40
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

import feedparser            # pip install feedparser
from dateutil import parser as dateparser  # pip install python-dateutil
import requests              # pip install requests
from bs4 import BeautifulSoup  # pip install beautifulsoup4

from sources import PROVIDERS, build_feed_url

# ---------- Config ----------
USER_AGENT = "HiloFetcher/1.0 (+https://example.invalid)"
DEFAULT_TEAM_SECTION = "arsenal"
DEFAULT_TEAM_CODE = "ARS"
OUT_DIR = "out"

# Thumbnails only for trusted RSS sources (we don't scrape images from HTML pages)
THUMBNAIL_ALLOWLIST = {"bbc_sport"}

# Feeds already team-scoped; for HTML we don't apply keyword gates anyway
TEAM_SCOPED_SOURCES = {"bbc_sport", "arsenal_official", "sky_sports", "evening_standard", "daily_mail", "the_times"}

# Recency (RSS only). HTML pages often lack reliable dates; we skip recency on HTML.
RECENCY_DAYS: Optional[int] = 14
RECENCY_DAYS_BY_SOURCE = {}  # e.g., {"bbc_sport": 30}

# Per-site HTML rules (very conservative; anchors only)
HTML_RULES: Dict[str, Dict[str, Any]] = {
    "arsenal_official": {
        "allow_domains": [r"^https?://(www\.)?arsenal\.com"],
        "allow_paths":   [r"/news/"],
        "selectors": [
            "a.u-media-object__link",   # common on article tiles
            "a.o-promobox__link",
            "a.o-teaser__heading-link",
            "a",                        # fallback
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

# ---------- Utils ----------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def ensure_out():
    os.makedirs(OUT_DIR, exist_ok=True)

def write_json(path: str, data: Any) -> None:
    ensure_out()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

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

def clean_text(s: Optional[str]) -> str:
    if not s:
        return ""
    return " ".join(str(s).split())

def is_recent_for(source: str, published: Optional[datetime]) -> bool:
    if source in HTML_RULES:
        # HTML: no reliable dates; don't filter on recency here
        return True
    days = RECENCY_DAYS_BY_SOURCE.get(source, RECENCY_DAYS)
    if days is None or not published:
        return True
    return published >= (now_utc() - timedelta(days=days))

def extract_thumbnail_from_feed(entry: Any) -> Optional[str]:
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

# ---------- RSS ----------
def fetch_rss(url: str, source_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    feedparser.USER_AGENT = USER_AGENT
    parsed = feedparser.parse(url)
    entries = list(parsed.entries or [])
    if limit and limit > 0:
        entries = entries[:limit]
    items: List[Dict[str, Any]] = []
    for e in entries:
        title = clean_text(getattr(e, "title", e.get("title", "")) if hasattr(e, "get") else getattr(e, "title", ""))
        link = getattr(e, "link", e.get("link", "")) if hasattr(e, "get") else getattr(e, "link", "")
        summary = ""
        if hasattr(e, "summary"):
            summary = clean_text(e.summary)
        elif hasattr(e, "get"):
            summary = clean_text(e.get("summary", ""))

        published = None
        if hasattr(e, "published_parsed") and e.published_parsed:
            published = parse_date(e.published_parsed)
        elif hasattr(e, "updated_parsed") and e.updated_parsed:
            published = parse_date(e.updated_parsed)
        else:
            cand = None
            if hasattr(e, "published"):
                cand = e.published
            elif hasattr(e, "get"):
                cand = e.get("published") or e.get("updated")
            published = parse_date(cand)

        if not is_recent_for(source_name, published):
            continue

        thumb = extract_thumbnail_from_feed(e) if source_name in THUMBNAIL_ALLOWLIST else None

        items.append({
            "source": source_name,
            "title": title or "",
            "url": link or "",
            "summary": summary or "",
            "published_utc": (published.isoformat() if published else None),
            "thumbnail_url": thumb,
            "created_utc": now_utc().isoformat(),
        })
    return items

# ---------- HTML (headlines-only) ----------
def allowed_url(source: str, href: str) -> bool:
    if not href or href.startswith("#"):
        return False
    rules = HTML_RULES.get(source, {})
    domains = [re.compile(pat, re.I) for pat in rules.get("allow_domains", [])]
    paths   = [re.compile(pat, re.I) for pat in rules.get("allow_paths", [])]
    ok_domain = any(r.search(href) for r in domains) if domains else True
    ok_path   = any(r.search(href) for r in paths) if paths else True
    return ok_domain and ok_path

def fetch_html_headlines(url: str, source_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    rules = HTML_RULES.get(source_name, {})
    selectors: List[str] = rules.get("selectors", ["a"])
    seen: set[str] = set()
    items: List[Dict[str, Any]] = []

    def push(href: str, text: str):
        nonlocal items
        if not href or not text:
            return
        if not href.startswith("http"):
            # make absolute
            from urllib.parse import urljoin
            href = urljoin(url, href)
        if not allowed_url(source_name, href):
            return
        key = href.strip()
        if key in seen:
            return
        seen.add(key)
        items.append({
            "source": source_name,
            "title": clean_text(text),
            "url": href,
            "summary": "",
            "published_utc": None,      # no reliable date on listing pages
            "thumbnail_url": None,      # we do not scrape images
            "created_utc": now_utc().isoformat(),
        })

    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href")
            text = a.get_text(" ", strip=True)
            push(href, text)
            if limit and len(items) >= limit:
                return items

    # Fallback scan if selectors too strict
    if not items:
        for a in soup.find_all("a"):
            href = a.get("href")
            text = a.get_text(" ", strip=True)
            push(href, text)
            if limit and len(items) >= limit:
                break

    return items

# ---------- Runner ----------
def discover_providers(kind: Optional[str] = None) -> List[str]:
    if kind is None:
        return list(PROVIDERS.keys())
    return [k for k, v in PROVIDERS.items() if v.get("type") == kind]

def resolve_url(provider: str) -> Optional[str]:
    try:
        return build_feed_url(provider, section=DEFAULT_TEAM_SECTION, team_code=DEFAULT_TEAM_CODE)
    except Exception:
        return None

def run(sources: Optional[List[str]] = None, limit: Optional[int] = None) -> int:
    ensure_out()
    providers = sources or list(PROVIDERS.keys())
    all_items: List[Dict[str, Any]] = []
    for src in providers:
        meta = PROVIDERS.get(src) or {}
        ptype = meta.get("type")
        url = resolve_url(src)
        if not url:
            print(f"[SKIP] {src}: no URL")
            continue

        try:
            if ptype == "rss":
                print(f"[FETCH][RSS]  {src} -> {url}")
                items = fetch_rss(url, source_name=src, limit=limit)
            elif ptype == "html":
                print(f"[FETCH][HTML] {src} -> {url}")
                items = fetch_html_headlines(url, source_name=src, limit=limit or 60)
            else:
                print(f"[SKIP] {src}: unknown type {ptype}")
                continue

            print(f"[OK] {src}: {len(items)} items")
            write_json(os.path.join(OUT_DIR, f"{src}.json"), items)
            all_items.extend(items)
        except Exception as e:
            print(f"[ERROR] {src}: {e}")

    write_json(os.path.join(OUT_DIR, "all.json"), all_items)
    print(f"[DONE] total_items={len(all_items)}")
    return 0

# ---------- CLI ----------
def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch RSS and HTML headlines from configured sources.")
    p.add_argument("--source", type=str, help="Comma-separated provider keys (default: all)")
    p.add_argument("--limit",  type=int, default=None, help="Max items per provider")
    return p.parse_args(argv)

def main(argv: List[str]) -> int:
    args = parse_args(argv)
    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    return run(sources=sources, limit=args.limit)

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

