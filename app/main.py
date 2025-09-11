# main.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

# Local imports
from .fetcher import fetch_rss, fetch_html_headlines, clean_summary_text  # noqa
from .sources import PROVIDERS, build_feed_url

# If you have these, keep them. Otherwise it’s fine if they’re not used.
try:
    from .data_loader import get_leagues, get_teams  # type: ignore
except Exception:
    def get_leagues() -> List[Dict[str, Any]]: return []
    def get_teams(league_code: Optional[str] = None) -> List[Dict[str, Any]]: return []

APP_VERSION = "1.0.2-selectors-wired"

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


def _article_to_dict(a: Any) -> Dict[str, Any]:
    """
    Normalize pydantic/BaseModel or plain dict into a JSON-safe dict.
    """
    if hasattr(a, "model_dump"):
        return a.model_dump(mode="json")
    if hasattr(a, "dict"):
        return a.dict()
    if isinstance(a, dict):
        return a
    out = {}
    for k in ("id", "title", "source", "summary", "url", "thumbnailUrl", "publishedUtc", "teams", "leagues"):
        v = getattr(a, k, None)
        if not isinstance(v, (str, int, float, bool, type(None), list, dict)):
            v = str(v)
        out[k] = v
    return out


@app.get("/news")
def get_news(
    # Accept BOTH forms to match your Unity client and your older calls:
    team: Optional[str] = Query(default=None, description="Single team code, e.g. 'ARS'"),
    teamCodes: Optional[str] = Query(default=None, description="Comma-separated team codes, e.g. 'ARS,CHE'"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
) -> JSONResponse:
    """
    Returns merged news from providers defined in sources.PROVIDERS.
    - RSS providers use fetch_rss (summaries cleaned)
    - HTML providers use fetch_html_headlines with CSS selectors from the provider config
    """
    # Normalize team codes
    if teamCodes:
        team_codes = [t.strip().upper() for t in teamCodes.split(",") if t.strip()]
    elif team:
        team_codes = [team.strip().upper()]
    else:
        team_codes = ["ARS"]  # default

    leagues: List[str] = []  # reserved for future filtering

    items: List[Dict[str, Any]] = []

    for name, meta in PROVIDERS.items():
        provider_type = (meta.get("type") or "").lower().strip()
        url = build_feed_url(name, team_code=team_codes[0])
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
                summary_sel = sels.get("summary")  # optional
                date_sel = sels.get("date")        # optional
                thumb_sel = sels.get("thumb")      # optional

                # If any of the required selectors are missing, skip this provider cleanly.
                if not (item_sel and title_sel and link_sel):
                    # You can log a warning here if you want (omitted to keep output clean).
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
                # Ensure text fields are safe (double-clean just in case)
                a["summary"] = clean_summary_text(a.get("summary", ""))
                items.append(_article_to_dict(a))

        except Exception:
            # One bad provider must not break the whole response
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
    return JSONResponse(content=jsonable_encoder(payload))


# ---- Optional config endpoints (keep if you use them) ----

@app.get("/leagues")
def list_leagues() -> JSONResponse:
    data = {"leagues": get_leagues()}
    return JSONResponse(content=jsonable_encoder(data))


@app.get("/teams")
def list_teams(
    league: Optional[str] = Query(default=None, description="Optional league code filter, e.g. 'EPL'")
) -> JSONResponse:
    data = {"teams": get_teams(league_code=league)}
    return JSONResponse(content=jsonable_encoder(data))
