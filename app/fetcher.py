#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetcher.py
- Fetch RSS feeds defined in sources.py and emit normalized JSON files to ./out/.
- No HTML scraping. Thumbnails only from feed-provided media fields (allow-listed).
- Zero assumptions about your storage or UI.

Outputs (created or overwritten each run):
  ./out/bbc_sport.json
  ./out/arsenal_official.json
  ./out/all.json

Usage:
  # Fetch all RSS providers and write ./out/*.json
  python fetcher.py

  # Narrow to BBC + Arsenal Official
  python fetcher.py --source bbc_sport,arsenal_official

  # Limit items per feed
  python fetcher.py --limit 25
"""

from __future__ import annotations
import argparse
import json
import os
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

import feedparser  # pip install feedparser
from dateutil import parser as dateparser  # pip install python-dateutil

from sources import PROVIDERS, build_feed_url  # local module

# ---------- Config ----------
USER_AGENT = "HiloNewsFetcher/1.0"
DEFAULT_TEAM_SECTION = "arsenal"
DEFAULT_TEAM_CODE = "ARS"

OUT_DIR = "out"

# Only these sources may display thumbnails (others render text-only)
THUMBNAIL_ALLOWLIST = {
    "bbc_sport",
    "arsenal_official",
}

# Feeds already team-scoped; skip keyword/team gating entirely
TEAM_SCOPED_SOURCES = {
    "bbc_sport",
    "arsenal_official",
}

# Drop items older than this many days (None to disable)
RECENCY_DAYS: Optional[int] = 14


# ---------- Utils ----------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def parse_date(value: Any) -> Optional[datetime]:
    """Parse RSS dates (struct_time or string) -> aware UTC datetime or None."""
    if not value:
        return None
    try:
        if hasattr(value, "tm_year"):  # struct_time
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

def extract_thumbnail(entry: Any) -> Optional[str]:
    """Use only feed media fields; no page scraping."""
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

def is_recent(published: Optional[datetime]) -> bool:
    if RECENCY_DAYS is None:
        return True
    if not published:
        # Consider undated as recent for team-scoped trusted feeds
        return True
    cutoff = now_utc() - timedelta(days=RECENCY_DAYS)
    return published >= cutoff


# ---------- Core ----------
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

        if not published and source_name in TEAM_SCOPED_SOURCES:
            published = now_utc()

        if not is_recent(published):
            continue

        thumb = extract_thumbnail(e) if source_name in THUMBNAIL_ALLOWLIST else None

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


# ---------- Output ----------
def write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def discover_rss_providers() -> List[str]:
    return [k for k, v in PROVIDERS.items() if v.get("type") == "rss"]

def resolve_feed_url(provider: str) -> Optional[str]:
    try:
        return build_feed_url(provider, section=DEFAULT_TEAM_SECTION, team_code=DEFAULT_TEAM_CODE)
    except Exception:
        return None


# ---------- Runner ----------
def run(sources: Optional[List[str]] = None, limit: Optional[int] = None) -> int:
    providers = sources or discover_rss_providers()
    if not providers:
        print("[WARN] No RSS providers discovered.")
        return 0

    all_items: List[Dict[str, Any]] = []
    for src in providers:
        url = resolve_feed_url(src)
        if not url:
            print(f"[SKIP] {src}: no URL (disabled or HTML provider)")
            continue

        print(f"[FETCH] {src} -> {url}")
        try:
            items = fetch_rss(url, source_name=src, limit=limit)
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
    p = argparse.ArgumentParser(description="Fetch RSS articles from configured sources and write JSON.")
    p.add_argument("--source", type=str, help="Comma-separated provider keys (default: all RSS providers)")
    p.add_argument("--limit", type=int, default=None, help="Max items per feed")
    return p.parse_args(argv)

def main(argv: List[str]) -> int:
    args = parse_args(argv)
    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    return run(sources=sources, limit=args.limit)

if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
