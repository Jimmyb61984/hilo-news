# app/main.py
import os
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.fetcher import fetch_rss  # only import what exists
from app.policy import apply_policy  # your policy module

app = FastAPI(title="hilo-news")

# CORS (open: adjust if you want to restrict)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Provider config (RSS) ----
# Keep this aligned with your project’s actual sources.
RSS_SOURCES: List[Dict[str, Any]] = [
    {
        "source": "ArsenalOfficial",
        "url": "https://www.arsenal.com/rss.xml",
        "teams": ["ARS"],
        "limit": 100,
    },
    {
        "source": "Arseblog",
        "url": "https://arseblog.com/feed/",
        "teams": ["ARS"],
        "limit": 100,
    },
    {
        "source": "PainInTheArsenal",
        "url": "https://paininthearsenal.com/feed/",
        "teams": ["ARS"],
        "limit": 100,
    },
    {
        "source": "ArsenalInsider",
        "url": "https://www.arsenalinsider.com/feed/",
        "teams": ["ARS"],
        "limit": 100,
    },
]

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}

@app.get("/news")
def news(
    team: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=100, ge=1, le=100),
    debugPolicy: Optional[bool] = Query(default=False),
) -> JSONResponse:
    """
    Returns:
      {
        "items": [...],
        "page": <int>,
        "pageSize": <int>,
        "total": <int>
      }
    """
    # 1) Fetch from configured RSS sources
    items: List[Dict[str, Any]] = []
    for src in RSS_SOURCES:
        try:
            batch = fetch_rss(
                url=src["url"],
                team_codes=src.get("teams"),
                source_key=src["source"],
                limit=src.get("limit", 100),
            )
            if batch:
                items.extend(batch)
        except Exception as e:
            # Don’t crash the endpoint if one provider fails
            # (Optionally log e)
            continue

    # 2) Sort by publishedUtc desc (if present)
    def _key(it: Dict[str, Any]):
        # ensure missing/invalid timestamps go to the bottom
        return it.get("publishedUtc") or ""

    items.sort(key=_key, reverse=True)

    # 3) Apply policy
    team_code = (team or "ARS").upper()
    try:
        items = apply_policy(items, team_code=team_code, debug=bool(debugPolicy))
    except TypeError:
        # If your apply_policy doesn’t accept debug kwarg
        items = apply_policy(items, team_code=team_code)

    # 4) Paginate
    total = len(items)
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = items[start:end]

    resp = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    }
    return JSONResponse(content=resp)

