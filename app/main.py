from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

from app.fetcher import fetch_news
# Split policy: core filters first (keeps totals high), caps applied per-page
from app.policy import apply_policy_core, page_with_caps, canonicalize_provider
from app.db import ensure_schema, upsert_items, load_items
from app.headlines import curate_and_polish

app = FastAPI(title="Hilo News API", version="2.2.0")

# --- startup: ensure DB schema ------------------------------------------------
@app.on_event("startup")
def _startup() -> None:
    try:
        ensure_schema()
    except Exception as exc:
        # Don't crash the process on first boot in ephemeral envs
        print("⚠️  DB schema init failed:", exc)

def _tally(items: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for it in items or []:
        p = canonicalize_provider(it.get("provider", ""))
        counts[p] = counts.get(p, 0) + 1
    return counts

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()

@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "ts": _iso_now(), "service": "news"}

@app.get("/news")
def news(
    page: int = Query(1, ge=1),
    pageSize: int = Query(100, alias="pageSize", ge=1, le=200),
    since: Optional[str] = Query(None, description="ISO timestamp; default 48h back"),
    excludeWomen: bool = Query(False, description="If true, exclude women's football items"),
    team: str = Query("arsenal", description="Team code e.g. 'arsenal'")
) -> JSONResponse:
    # 1) Fetch fresh union from sources (may be empty if providers rate-limit)
    try:
        raw_union: List[Dict[str, Any]] = fetch_news(team=team, since=since)
    except TypeError:
        # Back-compat: some builds expect positional args
        raw_union = fetch_news(team, since)
    except Exception as exc:
        # Fallback to DB cache on provider failure
        print("⚠️  fetch_news failed, falling back to cache:", exc)
        raw_union = []

    # 2) Cache raw items (idempotent upsert)
    try:
        if raw_union:
            upsert_items(raw_union)
    except Exception as exc:
        print("⚠️  upsert_items failed:", exc)

    # 3) Load from cache (ensures stable pagination) with broad window if since missing
    try:
        items = load_items(since=since)
    except TypeError:
        items = load_items(since)
    except Exception as exc:
        print("⚠️  load_items failed:", exc)
        items = raw_union

    # 4) Apply core policy filters (team/validity/exclusions) WITHOUT per-page caps
    core = apply_policy_core(items=items, team_code=team, exclude_women=excludeWomen)

    # 5) Headline polishing + light de-dupe before paging
    curated = curate_and_polish(core, target_min=56, target_max=88)

    # 6) Apply per-page caps to maintain provider balance
    paged = page_with_caps(curated, page=page, page_size=pageSize)

    payload = {
        "items": paged,
        "page": page,
        "pageSize": pageSize,
        "total": len(curated)
    }
    return JSONResponse(payload)

@app.get("/debug")
def debug(
    samplePageSize: int = Query(12, ge=1, le=200),
    since: Optional[str] = Query(None),
    excludeWomen: bool = Query(False),
    team: str = Query("arsenal")
) -> Dict[str, Any]:
    try:
        raw_union: List[Dict[str, Any]] = fetch_news(team=team, since=since)
    except Exception:
        raw_union = []

    core = apply_policy_core(items=raw_union, team_code=team, exclude_women=excludeWomen)
    page1 = page_with_caps(core, page=1, page_size=samplePageSize)
    return {
        "since": since,
        "pre_policy_total": len(raw_union),
        "pre_policy_by_provider": _tally(raw_union),
        "post_policy_total": len(core),
        "post_policy_by_provider": _tally(core),
        "page1_by_provider": _tally(page1)
    }
