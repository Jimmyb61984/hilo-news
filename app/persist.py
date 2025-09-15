# app/persist.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import date

from app.fetcher import fetch_news
from app.db import ensure_schema, upsert_items, load_items

def _season_start_iso_utc(today: Optional[date] = None) -> str:
    """
    Premier League season start heuristic:
    - If month >= 7 (July+), season start is Aug 1 of current year.
    - Else season start is Aug 1 of previous year.
    Return ISO string with 'Z'.
    """
    d = today or date.today()
    season_year = d.year if d.month >= 7 else d.year - 1
    return f"{season_year}-08-01T00:00:00Z"

def fetch_with_persistence(team_code: str = "ARS", allowed_types: Optional[set] = None) -> List[Dict[str, Any]]:
    """
    1) Fetch LIVE items via existing fetcher (no policy here).
    2) UPSERT into SQLite so history accumulates.
    3) LOAD season-to-date history and MERGE with live (unique by URL).
    Returns a PRE-POLICY list for main.py to pass into apply_policy().
    """
    ensure_schema()

    live = fetch_news(team_code=team_code, allowed_types=allowed_types) or []
    try:
        upsert_items(live)
    except Exception:
        # Never break responses because of storage issues.
        pass

    since = _season_start_iso_utc()
    hist = load_items(since_iso=since)

    # Merge live + historical, unique by URL (prefer the "live" item copy).
    by_url = {}
    for it in (hist or []):
        url = (it.get("url") or "").strip().lower()
        if url:
            by_url[url] = it
    for it in (live or []):
        url = (it.get("url") or "").strip().lower()
        if url:
            by_url[url] = it  # overwrite with live copy if exists

    return list(by_url.values())
