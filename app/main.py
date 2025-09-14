# app/main.py
from __future__ import annotations

from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime
import logging

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Internal imports (package-qualified; Render runs "app.main:app")
from app.sources import PROVIDERS, build_feed_url  # type: ignore
from app.fetcher import fetch_provider, Item  # type: ignore

# Policy import (present in our repo). If missing for any reason, fall back safely.
try:
    from app.policy import apply_policy, apply_policy_with_stats  # type: ignore
except Exception:  # pragma: no cover
    def apply_policy(items: List[Dict[str, Any]], team_code: str = "ARS") -> List[Dict[str, Any]]:
        return items
    def apply_policy_with_stats(items: List[Dict[str, Any]], team_code: str = "ARS") -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        return items, {"applied": False, "reason": "policy module missing"}

logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Hilo News API")

# CORS (relaxed; tighten if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def _item_to_dict(it: Item) -> Dict[str, Any]:
    """Dataclass/obj -> dict with expected keys and ISO8601 publishedUtc."""
    if isinstance(it, dict):
        d: Dict[str, Any] = dict(it)
    else:
        d = {k: getattr(it, k) for k in dir(it) if not k.startswith("_") and not callable(getattr(it, k))}
    d.setdefault("id", d.get("guid") or d.get("url"))
    d.setdefault("title", "")
    d.setdefault("source", "")
    d.setdefault("summary", "")
    d.setdefault("thumbnailUrl", d.get("imageUrl"))
    d.setdefault("publishedUtc", None)
    d.setdefault("teams", [])
    d.setdefault("leagues", [])
    # normalize publishedUtc
    pu = d.get("publishedUtc")
    if isinstance(pu, datetime):
        d["publishedUtc"] = pu.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return d

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/")
async def root():
    return {"message": "Hilo News API. See /news"}

@app.get("/news")
async def get_news(
    team: Optional[str] = Query(None, description="Team code like ARS"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
    debug: bool = Query(False, description="Include policy debug info"),
) -> JSONResponse:
    # 1) Aggregate from all providers
    items: List[Dict[str, Any]] = []
    for prov in PROVIDERS:
        try:
            fetched = await fetch_provider(prov, team=team)
            items.extend(_item_to_dict(x) for x in fetched)
        except Exception as e:
            logger.exception("Provider fetch failed for %s: %s", getattr(prov, "name", prov), e)

    # 2) Apply policy (filters/dedupe/ranking)
    if debug:
        items, stats = apply_policy_with_stats(items, team_code=(team or "ARS"))
    else:
        items = apply_policy(items, team_code=(team or "ARS"))
        stats = None  # type: ignore

    # 3) Sort by publishedUtc (desc). Items lacking it go last.
    def sort_key(d: Dict[str, Any]):
        v = d.get("publishedUtc")
        if isinstance(v, str):
            return v.replace("Z", "+00:00")
        return ""
    items.sort(key=sort_key, reverse=True)

    total = len(items)

    # 4) Pagination
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = items[start:end]

    resp: Dict[str, Any] = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    }
    if debug and stats is not None:
        resp["policyDebug"] = stats

    return JSONResponse(content=resp)


