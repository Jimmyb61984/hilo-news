# app/main.py
from __future__ import annotations

from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from .fetcher import (
    fetch_rss,
    fetch_html_headlines,
    clean_summary_text,
    fetch_detail_image_and_summary,
)
from .sources import PROVIDERS, build_feed_url

APP_VERSION = "1.0.7-men-only"

app = FastAPI(title="Hilo News API", version=APP_VERSION)

@app.get("/health")
def health() -> Dict[str, str]:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "time": datetime.now(timezone.utc).isoformat(),
    }

def _to_dt(iso_str: Optional[str]) -> datetime:
    if not iso_str:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

# ---------- Women’s filter (MEN-ONLY FEED: ALWAYS ON) ----------
_WOMEN_KEYS: List[str] = [
    # terms
    "women", "womens", "women’s", "women's", "awfc", "wfc", "fa wsl", "wsl",
    "barclays women's", "women's super league",
    # url/path fragments
    "/women/", "/wsl/", "/awfc/", "/women-", "/womens-", "women-", "womens-",
    "-women/", "-wsl/", "-awfc/",
    # common site tags/sections
    "arsenal women", "arsenal-women", "arsenalwomen"
]

def _looks_like_womens(a: Dict[str, Any]) -> bool:
    t = (a.get("title") or "").lower()
    s = (a.get("summary") or "").lower()
    u = (a.get("url") or "").lower()
    for k in _WOMEN_KEYS:
        if k in t or k in s or k in u:
            return True
    return False

# ---------- Relevance (guard media leakage) ----------
def _is_relevant_to_team(a: Dict[str, Any], team_name: str, aliases: List[str]) -> bool:
    text = f"{a.get('title','')} {a.get('summary','')}".lower()
    if any(alias.lower() in text for alias in aliases):
        return True
    # also allow obvious URL slugs like /arsenal-
    u = (a.get("url") or "").lower()
    if any(alias.lower().replace(" ", "-") in u for alias in aliases):
        return True
    return False

# ---------- Teams metadata (minimal inline; you can swap to DB/JSON later) ----------
_TEAM_ALIASES: Dict[str, List[str]] = {
    "ARS": ["Arsenal", "Gunners", "AFC", "Arsenal FC"],
    "CHE": ["Chelsea", "CFC", "Chelsea FC"],
    "TOT": ["Tottenham", "Spurs", "Tottenham Hotspur"],
    "MCI": ["Manchester City", "Man City", "City"],
    "LIV": ["Liverpool", "LFC", "Liverpool FC"],
    # add as needed
}
def _aliases_for(code: str) -> List[str]:
    return _TEAM_ALIASES.get(code.upper(), [code.upper()])

# ---------- News endpoint ----------
@app.get("/news")
def get_news(
    team: Optional[str] = Query(default=None, description="Single team code, e.g. 'ARS'"),
    teamCodes: Optional[str] = Query(default=None, description="Comma-separated team codes, e.g. 'ARS,CHE'"),
    types: Optional[str] = Query(default=None, description="Comma-separated: 'official', 'fan' (default both)"),
    # NOTE: excludeWomen param is now ignored for team feeds (MEN-ONLY enforced).
    excludeWomen: Optional[bool] = Query(default=None, description="(ignored for team feeds; men-only enforced)"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
) -> JSONResponse:
    # Normalize team codes
    if teamCodes:
        team_codes = [t.strip().upper() for t in teamCodes.split(",") if t.strip()]
    elif team:
        team_codes = [team.strip().upper()]
    else:
        team_codes = ["ARS"]

    primary_team = team_codes[0]
    team_aliases = _aliases_for(primary_team)

    # Types
    allowed_types: Set[str] = {"official", "fan"}
    if types:
        parts = [p.strip().lower() for p in types.split(",") if p.strip()]
        allowed_types = set([p for p in parts if p in ("official", "fan")]) or {"official", "fan"}

    items: List[Dict[str, Any]] = []

    for name, meta in PROVIDERS.items():
        provider_type = (meta.get("type") or "").lower().strip()
        is_official = bool(meta.get("is_official", False))

        # Respect type filter
        if is_official and "official" not in allowed_types:
            continue
        if (not is_official) and "fan" not in allowed_types:
            continue

        url = build_feed_url(name, team_code=primary_team)
        if not url:
            continue

        try:
            if provider_type == "rss":
                articles = fetch_rss(url=url, team_codes=team_codes, source_key=name, limit=60)

            elif provider_type == "html":
                sels = meta.get("selectors") or {}
                item_sel = sels.get("item")
                title_sel = sels.get("title")
                link_sel = sels.get("link")
                summary_sel = sels.get("summary")
                date_sel = sels.get("date")
                thumb_sel = sels.get("thumb")

                if not (item_sel and title_sel and link_sel):
                    continue

                articles = fetch_html_headlines(
                    url=url,
                    item_selector=item_sel,
                    title_selector=title_sel,
                    link_selector=link_sel,
                    summary_selector=summary_sel,
                    date_selector=date_sel,
                    thumb_selector=thumb_sel,
                    team_codes=team_codes,
                    source_key=name,
                    limit=48,
                )
            else:
                continue

            for a in articles:
                # Clean summary
                a["summary"] = clean_summary_text(a.get("summary", ""))

                # Ensure fields exist
                a.setdefault("imageUrl", None)
                a.setdefault("thumbnailUrl", None)

                # MEN-ONLY: always exclude women's content
                if _looks_like_womens(a):
                    continue

                # Media relevance guard (require team mention)
                if name in ("DailyMail", "TheTimes", "TheStandard", "SkySports"):
                    if not _is_relevant_to_team(a, team_aliases[0], team_aliases):
                        continue

                if is_official:
                    # Enrich with hero image/summary
                    page_url = a.get("url") or ""
                    if page_url:
                        detail = fetch_detail_image_and_summary(page_url)
                        img = detail.get("imageUrl") or None
                        if img:
                            a["imageUrl"] = img
                            a["thumbnailUrl"] = img
                        if not a.get("summary"):
                            a["summary"] = detail.get("summary") or a.get("summary", "")
                else:
                    # Fan pages: force no image (Panel2)
                    a["imageUrl"] = None

                items.append(a)

        except Exception:
            # fail-safe: skip provider on error
            continue

    # Per-provider cap (per page). Scale with pageSize for fairness.
    cap = max(6, pageSize // 4)  # e.g., 50 -> 12 per provider
    capped: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for it in items:
        src = it.get("source") or "unknown"
        if counts.get(src, 0) >= cap:
            continue
        counts[src] = counts.get(src, 0) + 1
        capped.append(it)

    # Stronger de-dup: (url, title, summary)
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in capped:
        key = (it.get("url"), it.get("title"), it.get("summary"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # Sort newest first
    deduped.sort(key=lambda x: _to_dt(x.get("publishedUtc")), reverse=True)

    # Paginate
    total = len(deduped)
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = deduped[start:end]

    payload = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    }
    return JSONResponse(content=jsonable_encoder(payload))

# Minimal teams metadata (used by TeamsCatalog if you call it)
@app.get("/metadata/teams")
def get_teams() -> JSONResponse:
    teams = [
        {"code": "ARS", "name": "Arsenal", "aliases": ["Arsenal", "AFC", "Gunners"]},
        {"code": "CHE", "name": "Chelsea", "aliases": ["Chelsea", "CFC"]},
        {"code": "TOT", "name": "Tottenham Hotspur", "aliases": ["Tottenham", "Spurs"]},
        {"code": "MCI", "name": "Manchester City", "aliases": ["Man City", "City"]},
        {"code": "LIV", "name": "Liverpool", "aliases": ["Liverpool", "LFC"]},
    ]
    return JSONResponse(content=teams)

