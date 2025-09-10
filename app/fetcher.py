#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetcher.py
- Fetch RSS feeds defined in sources.py, normalize them, and persist to SQLite.
- No HTML scraping. Thumbnails only from feed-provided media fields (allow-listed).
- Idempotent upserts on (source, url).

Usage:
  # Fetch ALL RSS providers declared in sources.py and save to DB
  python fetcher.py

  # Fetch only BBC + Arsenal Official
  python fetcher.py --source bbc_sport,arsenal_official

  # Limit items (per feed) and print JSON instead of saving
  python fetcher.py --source bbc_sport --limit 20 --json
"""

from __future__ import annotations
import argparse
import json
import sqlite3
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta

import feedparser  # pip install feedparser
from dateutil import parser as dateparser  # pip install python-dateutil

from sources import PROVIDERS, build_feed_url  # project-local

# ------------------ Config ------------------

USER_AGENT = "HiloNewsFetcher/1.0"
DEFAULT_TEAM_SECTION = "arsenal"
DEFAULT_TEAM_CODE = "ARS"

DB_PATH = "news.db"

# Only these sources may display thumbnails (others render text-only)
THUMBNAIL_ALLOWLIST = {
    "bbc_sport",
    "arsenal_official",
}

# Feeds that are already team-scoped; skip keyword/team gating entirely
TEAM_SCOPED_SOURCES = {
    "bbc_sport",
    "arsenal_official",
}

# Drop items older than this many days (None to disable)
RECENCY_DAYS: Optional[int] = 14


# ------------------ Utilities ------------------

def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value: Any) -> Optional[datetime]:
    """Robust date parsing for RSS. Returns aware UTC datetime or None."""
    if not value:
        return None
    # feedparser often provides struct_time via *.published_parsed / *.updated_parsed
    try:
        # struct_time has tm_year attr
        if hasattr(value, "tm_year"):
            # convert struct_time -> datetime (naive), then set UTC
            return datetime(*value[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    # strings
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
    """Light cleanup; normalize whitespace; no HTML scraping."""
    if not s:
        return ""
    return " ".join(str(s).split())


def extract_thumbnail(entry: Any) -> Optional[str]:
    """Use only feed-provided media fields (no page scraping)."""
    # Try media_thumbnail / media_content
    try:
        media = entry.get("media_thumbnail") or entry.get("media_content")
        if isinstance(media, list):
            for m in media:
                u = m.get("url")
                if u:
                    return u
    except Exception:
        pass

    # Try <link rel="enclosure" type="image/*">
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
        # Consider undated as recent for team-scoped trusted feeds to avoid accidental drops
        return True
    cutoff = now_utc() - timedelta(days=RECENCY_DAYS)
    return published >= cutoff


# ------------------ Core fetch/normalize ------------------

def fetch_rss(url: str, source_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Fetch and normalize items from an RSS/Atom feed URL.
    Returns normalized dicts; persistence handled separately.
    """
    feedparser.USER_AGENT = USER_AGENT
    parsed = feedparser.parse(url)
    entries = list(parsed.entries or [])
    if limit and limit > 0:
        entries = entries[:limit]

    items: List[Dict[str, Any]] = []
    for e in entries:
        title = clean_text(getattr(e, "title", e.get("title", "")) if hasattr(e, "get") else getattr(e, "title", ""))
        link = getattr(e, "link", e.get("link", "")) if hasattr(e, "get") else getattr(e, "link", "")

        # Summary/description
        summary = ""
        if hasattr(e, "summary"):
            summary = clean_text(e.summary)
        elif hasattr(e, "get"):
            summary = clean_text(e.get("summary", ""))

        # Published/updated dates (several fallbacks)
        published = None
        if hasattr(e, "published_parsed") and e.published_parsed:
            published = parse_date(e.published_parsed)
        elif hasattr(e, "updated_parsed") and e.updated_parsed:
            published = parse_date(e.updated_parsed)
        else:
            # string fields
            cand = None
            if hasattr(e, "published"):
                cand = e.published
            elif hasattr(e, "get"):
                cand = e.get("published") or e.get("updated")
            published = parse_date(cand)

        # For team-scoped trusted feeds, fall back to 'now' if missing
        if not published and source_name in TEAM_SCOPED_SOURCES:
            published = now_utc()

        # Filter on recency
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


# ------------------ Persistence (SQLite) ------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    thumbnail_url TEXT,
    published_utc TEXT,
    created_utc TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_articles_source_url
ON articles (source, url);
"""

UPSERT_SQL = """
INSERT INTO articles (source, url, title, summary, thumbnail_url, published_utc, created_utc)
VALUES (:source, :url, :title, :summary, :thumbnail_url, :published_utc, :created_utc)
ON CONFLICT(source, url) DO UPDATE SET
  title=excluded.title,
  summary=excluded.summary,
  thumbnail_url=excluded.thumbnail_url,
  published_utc=COALESCE(excluded.published_utc, articles.published_utc),
  created_utc=excluded.created_utc;
"""


def ensure_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def save_articles(items: List[Dict[str, Any]], db_path: str = DB_PATH) -> int:
    if not items:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        ensure_db(conn)
        cur = conn.cursor()
        cur.executemany(UPSERT_SQL, items)
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


# ------------------ Runner ------------------

def discover_rss_providers() -> List[str]:
    return [k for k, v in PROVIDERS.items() if v.get("type") == "rss"]


def resolve_feed_url(provider: str) -> Optional[str]:
    try:
        return build_feed_url(provider, section=DEFAULT_TEAM_SECTION, team_code=DEFAULT_TEAM_CODE)
    except Exception:
        return None


def run(sources: Optional[List[str]] = None, limit: Optional[int] = None, json_out: bool = False) -> int:
    providers = sources or discover_rss_providers()
    if not providers:
        print("[WARN] No RSS providers discovered.")
        return 0

    total = 0
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
            if json_out:
                all_items.extend(items)
            else:
                n = save_articles(items)
                print(f"[SAVE] {src}: upserted {n} rows")
            total += len(items)
        except Exception as e:
            print(f"[ERROR] {src}: {e}")

    if json_out:
        print(json.dumps(all_items, ensure_ascii=False))

    print(f"[DONE] total_items={total}")
    return 0


# ------------------ CLI ------------------

def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch RSS articles from configured sources.")
    p.add_argument("--source", type=str, help="Comma-separated provider keys (default: all RSS providers)")
    p.add_argument("--limit", type=int, default=None, help="Max items per feed")
    p.add_argument("--json", action="store_true", help="Print normalized items as JSON instead of saving")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    return run(sources=sources, limit=args.limit, json_out=args.json)


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))

