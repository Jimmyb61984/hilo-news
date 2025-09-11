from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder  # <-- key import

from .fetcher import fetch_rss, fetch_html_headlines
from .sources import PROVIDERS, build_feed_url

# NEW: config loader (you'll add app/data_loader.py)
from .data_loader import get_leagues, get_teams

app = FastAPI(title="Hilo News API", version="1.0.1")


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


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


def _article_to_dict(a: Any) -> Dict[str, Any]:
    """
    Normalize Pydantic/BaseModel or plain dict Article into JSON-safe dict.
    """
    # Pydantic v2
    if hasattr(a, "model_dump"):
        return a.model_dump(mode="json")
    # Pydantic v1
    if hasattr(a, "dict"):
        return a.dict()
    if isinstance(a, dict):
        return a
    # Fallback: best-effort attribute extraction
    out = {}
    for k in ("id", "title", "source", "summary", "url", "thumbnailUrl", "publishedUtc", "teams", "leagues"):
        v = getattr(a, k, None)
        # Convert HttpUrl or other non-JSON types to str
        if not isinstance(v, (str, int, float, bool, type(None), list, dict)):
            v = str(v)
        out[k] = v
    return out


@app.get("/news")
def get_news(
    team: str = Query("ARS", description="Team code (default ARS)"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
) -> JSONResponse:
    """
    Returns merged news from providers configured in sources.PROVIDERS.
    - RSS providers use fetch_rss
    - HTML providers use fetch_html_headlines (headlines-only)
    """
    team = team.upper()
    team_codes = [team]
    leagues: List[str] = []

    items: List[Dict[str, Any]] = []

    for name, meta in PROVIDERS.items():
        provider_type = meta.get("type")
        url = build_feed_url(name, team_code=team)
        if not url:
            continue

        try:
            if provider_type == "rss":
                articles = fetch_rss(url=url, team_codes=team_codes, leagues=leagues, source_name=name, limit=50)
            elif provider_type == "html":
                articles = fetch_html_headlines(url=url, team_codes=team_codes, leagues=leagues, source_name=name, limit=40)
            else:
                continue

            for a in articles:
                items.append(_article_to_dict(a))

        except Exception:
            # One bad provider should not break the whole response
            continue

    # De-dup by (url, title)
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in items:
        key = (it.get("url"), it.get("title"))
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
    # Ensure JSON-safe output (e.g., HttpUrl -> str)
    return JSONResponse(content=jsonable_encoder(payload))


# ===== New config endpoints =====

@app.get("/leagues")
def list_leagues() -> JSONResponse:
    """
    Returns the leagues config loaded from data/leagues.yaml
    """
    data = {"leagues": get_leagues()}
    return JSONResponse(content=jsonable_encoder(data))


@app.get("/teams")
def list_teams(
    league: Optional[str] = Query(
        default=None,
        description="Optional league code filter, e.g. 'EPL'"
    )
) -> JSONResponse:
    """
    Returns the teams config loaded from data/teams.yaml.
    Optionally filter by league code (?league=EPL).
    """
    data = {"teams": get_teams(league_code=league)}
    return JSONResponse(content=jsonable_encoder(data))

