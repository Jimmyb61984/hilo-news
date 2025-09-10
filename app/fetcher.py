#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fetcher.py
- Fetch RSS feeds defined in sources.py and emit normalized article records.
- No HTML scraping. Thumbnails only from feed-provided media fields (allow-listed publishers).
- Safe on missing/odd fields; never crashes the pipeline on a single bad item.

Usage:
  # Fetch ALL RSS providers declared in sources.py
  python fetcher.py

  # Fetch only BBC + Arsenal Official
  python fetcher.py --source bbc_sport,arsenal_official

  # Limit items (per feed) and show JSON items (no persistence)
  python fetcher.py --source bbc_sport --limit 20 --dry-run

  # Normal run (prints brief logs). Integrate save_articles() for your DB.
  python fetcher.py --limit 50
"""

from __future__ import annotations
import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import feedparser  # pip install feedparser
from datetime import datetime, timezone
from dateutil import parser as dateparser  # pip install python-dateutil

# Project-local sources registry
from sources import PROVIDERS, build_feed_url

# ---------- Configuration ----------

USER_AGENT = "HiloNewsFetcher/1.0 (+https://example.invalid)"
DEFAULT_TEAM_SECTION = "arsenal"
DEFAULT_TEAM_CODE = "ARS"

# Trusted publishers that may display thumbnails if present in the feed.
THUMBNAIL_ALLOWLIST = {
    "bbc_sport",
    "arsenal_official",
    # add other trusted publishers here if you enable more
}

# Feeds that are already team-scoped; do NOT apply keyword team gating.
TEAM_SCOPED_SOURCES = {
    "bbc_sport",
    "arsenal_official",
}

# Drop items older than this many days (set None to disable)
RECENCY_DAYS = 14


# ---------- Utilities ----------

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value: Any) -> Optional[datetime]:
    """
    Robust date parsing for RSS. Returns timezone-aware UTC datetime or None.
    """
    if not value:
        return None
    # feedparser may already provide a struct_time in entry.published_parsed
    if hasattr(value, "tm_year"):
        try:
            dt = datetime.fromtimestamp(time.mktime(value), tz=timezone.utc)
            return dt
        except Exception:
            pass
    # otherwise try strings via dateutil
    try:
        dt = dateparser.parse(str(value))
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def clean_text(s: Optional[str]) -> str:
    """
    Very light cleaning; do not strip aggressively. No HTML scraping.
    """
    if not s:
        return ""
    # feedparser already decodes entities; we just normalize whitespace
    return " ".join(str(s).split())


def extract_thumbnail(entry: Any) -> Optional[str]:
    """
    Only take thumbnails from standard feed fields (media:thumbnail, media:content, links).
    No HTML page scraping.
    """
    # media_thumbnail
    thumb = None
    try:
        thumbs = entry.get("media_thumbnail") or entry.get("media_content")
        if thumbs and isinstance(thumbs, list):
            for t in thumbs:
                url = t.get("url")
                if url:
                    thumb = url
                    break
    except Exception:
        pass

    # look into links with rel='enclosure' or type image/*
    if not thumb:
        try:
            for lk in entry.get("links", []) or []:
                href = lk.get("href")
                if href and ("image" in (lk.get("type") or "")):
                    thumb = href
                    break
        except Exception:
            pass

    return thumb


def is_recent(published: Optional[datetime]) -> bool:
    if RECENCY_DAYS is None:
        return True
    if not published:
        # If no date, consider as recent but you can choose to drop instead.
        return True
    cutoff = _now_utc() - timedelta_days(RECENCY_DAYS)
    return published >= cutoff


def timedelta_days(days: int):
    return datetime.timedelta(days=days)  # type: ignore[attr-defined]


# ---------- Core fetch/normalize ----------

def fetch_rss(url: str, source_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Fetch and normalize items from an RSS/Atom feed URL.
    Returns a list of normalized dicts; persistence is up to caller.
    """
    # feedparser allows setting a user agent via request_headers (since 6.0.11)
    # For older versions, set globally.
    feedparser.USER_AGENT = USER_AGENT

    parsed = feedparser.parse(url)
    entries = parsed.entries or []

    if limit is not None and limit > 0:
        entries = entries[:limit]

    items: List[Dict[str, Any]] = []

    for e in entries:
        title = clean_text(getattr(e, "title", e.get("title", "")))
        link = getattr(e, "link", e.get("link", "")) if hasattr(e, "link") or isinstance(e, dict) else ""
        summary = clean_text(getattr(e, "summary", e.get("summary", "")))
        # published date
        published = None
        if hasattr(e, "published_parsed") and e.published_parsed:
            published = parse_date(e.published_parsed)
        elif hasattr(e, "updated_parsed") and e.updated_parsed:
            published = parse_date(e.updated_parsed)
        else:
            # try string fields
            published = parse_date(getattr(e, "published", e.get("published", None)))
            if not published:
                published = parse_date(getattr(e, "updated", e.get("updated", None)))

        # fallback: assign 'now' if missing for team-scoped sources to avoid accidental drops
        if not published and source_name in TEAM_SCOPED_SOURCES:
            published = _now_utc()

        thumb = extract_thumbnail(e) if source_name in THUMBNAIL_ALLOWLIST else None

        item = {
            "source": source_name,
            "title": title,
            "url": link,
            "summary": summary,
            "published_utc": published.isoformat() if published else None,
            "thumbnail_url": thumb,
        }
        items.append(item)

    return items


# ---------- Persistence hook (no-op default) ----------

def save_articles(items: List[Dict[str, Any]]) -> None:
    """
    Hook for persisting items to your store.
    Replace this with your upsert/DB code (e.g., SQL/ORM).
    Default is a no-op to keep this file portable.
    """
    # Example placeholder (disabled):
    # for it in items:
    #     upsert_article(it)
    pass


# ---------- Runner ----------

def resolve_feed_url(provider: str) -> Optional[str]:
    """
    Resolve a provider key to a concrete URL using sources.py.
    """
    try:
        return build_feed_url(provider, section=DEFAULT_TEAM_SECTION, team_code=DEFAULT_TEAM_CODE)
    except Exception:
        return None


def discover_rss_providers() -> List[str]:
    return [k for k, v in PROVIDERS.items() if v.get("type") == "rss"]


def run(sources: Optional[List[str]] = None, limit: Optional[int] = None, dry_run: bool = False, json_out: bool = False) -> int:
    """
    Execute fetch across sources. Returns process exit code.
    """
    providers = sources or discover_rss_providers()
    if not providers:
        print("[WARN] No RSS providers discovered.")
        return 0

    grand_total = 0
    all_items_for_json: List[Dict[str, Any]] = []

    for src in providers:
        url = resolve_feed_url(src)
        if not url:
            print(f"[SKIP] {src}: no URL (disabled or HTML provider)")
            continue

        print(f"[FETCH] {src} -> {url}")
        try:
            items = fetch_rss(url, source_name=src, limit=limit)
            print(f"[OK] {src}: {len(items)} items")
            grand_total += len(items)

            if json_out or dry_run:
                all_items_for_json.extend(items)
            else:
                # Persist immediately
                save_articles(items)

        except Exception as e:
            print(f"[ERROR] {src}: {e}")

    if json_out or dry_run:
        # print compact JSON to stdout
        print(json.dumps(all_items_for_json, ensure_ascii=False, separators=(",", ":")))

    print(f"[DONE] total_items={grand_total}")
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch RSS articles from configured sources.")
    p.add_argument(
        "--source",
        type=str,
        help="Comma-separated provider keys to fetch (default: all RSS providers)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max items per feed (default: no limit)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not persist; print JSON to stdout",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Alias of --dry-run (print normalized items as JSON)",
    )
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    sources: Optional[List[str]] = None
    if args.source:
        sources = [s.strip() for s in args.source.split(",") if s.strip()]
    dry = bool(args.dry_run or args.json)
    json_out = bool(args.json or args.dry_run)
    return run(sources=sources, limit=args.limit, dry_run=dry, json_out=json_out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


