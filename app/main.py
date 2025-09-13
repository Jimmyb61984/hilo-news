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

APP_VERSION = "1.0.9-arsenal-men-only-strict"

app = FastAPI(title="Hilo News API", version=APP_VERSION)

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "version": APP_VERSION, "time": datetime.now(timezone.utc).isoformat()}

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

# -------- MEN-ONLY hard filter (generic) --------
_WOMEN_KEYS: List[str] = [
    "women", "womens", "women’s", "women's", "awfc", "wfc", "fa wsl", "wsl",
    "/women/", "/wsl/", "/awfc/", "/women-", "/womens-", "women-", "womens-",
    "arsenal women", "arsenal-women", "arsenalwomen",
]

def _looks_like_womens(a: Dict[str, Any]) -> bool:
    t = (a.get("title") or "").lower()
    s = (a.get("summary") or "").lower()
    u = (a.get("url") or "").lower()
    for k in _WOMEN_KEYS:
        if k in t or k in s or k in u:
            return True
    return False

# -------- Team relevance (Arsenal-only for ARS) --------
_ALIASES: Dict[str, List[str]] = {
    "ARS": ["Arsenal", "Gunners", "AFC", "Arsenal FC"],
}

def _aliases_for(code: str) -> List[str]:
    return _ALIASES.get(code.upper(), [code.upper()])

def _is_media_relevant_to_team(a: Dict[str, Any], aliases: List[str]) -> bool:
    """
    Strict guard for national media (DailyMail, TheTimes, TheStandard, SkySports):
    - Title/summary must mention an alias (e.g., 'Arsenal'/'Gunners'), OR
    - URL must contain slug with alias (e.g., '/arsenal-...').
    Additionally drops obviously cross-club columns by requiring at least one of those signals.
    """
    title = (a.get("title") or "").lower()
    summary = (a.get("summary") or "").lower()
    url = (a.get("url") or "").lower()

    alias_hit = any(al.lower() in title or al.lower() in summary for al in aliases)
    slug_hit = any(al.lower().replace(" ", "-") in url for al in aliases)

    return alias_hit or slug_hit

# -------- News endpoint --------
@app.get("/news")
def get_news(
    team: Optional[str] = Query(default=None),
    teamCodes: Optional[str] = Query(default=None),
    types: Optional[str] = Query(default=None),  # 'official', 'fan' (both if omitted)
    excludeWomen: Optional[bool] = Query(default=None),  # ignored (men-only enforced)
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
) -> JSONResponse:
    # Which team? (default ARS)
    if teamCodes:
        team_codes = [t.strip().upper() for t in teamCodes.split(",") if t.strip()]
    elif team:
        team_codes = [team.strip().upper()]
    else:
        team_codes = ["ARS"]

    primary_team = team_codes[0]
    aliases = _aliases_for(primary_team)

    # Which types?
    allowed: Set[str] = {"official", "fan"}
    if types:
        parts = [p.strip().lower() for p in types.split(",") if p.strip()]
        allowed = set([p for p in parts if p in ("official", "fan")]) or {"official", "fan"}

    items: List[Dict[str, Any]] = []

    for name, meta in PROVIDERS.items():
        provider_type = (meta.get("type") or "").lower().strip()
        is_official = bool(meta.get("is_official", False))

        if is_official and "official" not in allowed:
            continue
        if (not is_official) and "fan" not in allowed:
            continue

        url = build_feed_url(name, team_code=primary_team)
        if not url:
            continue

        try:
            if provider_type == "rss":
                articles = fetch_rss(url=url, team_codes=team_codes, source_key=name, limit=60)
            elif provider_type == "html":
                sels = meta.get("selectors") or {}
                item_sel = sels.get("item"); title_sel = sels.get("title"); link_sel = sels.get("link")
                summary_sel = sels.get("summary"); date_sel = sels.get("date"); thumb_sel = sels.get("thumb")
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
                a["summary"] = clean_summary_text(a.get("summary", ""))
                a.setdefault("imageUrl", None)
                a.setdefault("thumbnailUrl", None)

                # MEN-ONLY (generic pass)
                if _looks_like_womens(a):
                    continue

                # National media must be clearly about Arsenal men
                if name in ("DailyMail", "TheTimes", "TheStandard", "SkySports"):
                    if not _is_media_relevant_to_team(a, aliases):
                        continue
                    # quick women guard on media headlines/urls
                    if _looks_like_womens(a):
                        continue

                if is_official:
                    page_url = a.get("url") or ""
                    if page_url:
                        detail = fetch_detail_image_and_summary(page_url)

                        # HARD women block using article page signals (ArsenalOfficial)
                        if detail.get("is_women"):
                            continue

                        # Prefer hero image from detail page
                        img = detail.get("imageUrl") or None
                        if img:
                            a["imageUrl"] = img
                            a["thumbnailUrl"] = img

                        # Fill summary if empty
                        if not a.get("summary"):
                            a["summary"] = detail.get("summary") or a.get("summary", "")

                        # Prefer real published time if list time looks synthetic
                        det_pub = detail.get("published")
                        if det_pub:
                            try:
                                cur_dt = _to_dt(a.get("publishedUtc"))
                                det_dt = _to_dt(det_pub)
                                if cur_dt.year <= 1971 or abs((datetime.now(timezone.utc) - cur_dt).total_seconds()) < 5:
                                    a["publishedUtc"] = det_dt.isoformat()
                            except Exception:
                                pass
                else:
                    # Fan pages render on Panel2 (no hero image)
                    a["imageUrl"] = None

                items.append(a)
        except Exception:
            continue

    # Per-provider cap scaled by page size
    cap = max(6, pageSize // 4)  # e.g. 50 -> 12 max per provider
    capped: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for it in items:
        src = it.get("source") or "unknown"
        if counts.get(src, 0) >= cap:
            continue
        counts[src] = counts.get(src, 0) + 1
        capped.append(it)

    # De-dup across (url,title,summary)
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

    payload = {"items": page_items, "page": page, "pageSize": pageSize, "total": total}
    return JSONResponse(content=jsonable_encoder(payload))

# Minimal metadata (unchanged)
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


