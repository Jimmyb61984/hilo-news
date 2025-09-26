from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Project-internal modules (do NOT import any non-existing classes!)
from app.fetcher import fetch_news  # expected existing module
from app.db import ensure_schema, upsert_items, load_items  # expected existing module
from app.policy import apply_policy_core, page_with_caps, canonicalize_provider  # existing functions
from app.headlines import rewrite_headline  # our headline rewriter

app = FastAPI(title="hilo-news")

# CORS - mirror prior behaviour; keep permissive for Unity preview
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

@app.on_event("startup")
def _startup() -> None:
    try:
        ensure_schema()
    except Exception as e:
        # Do not crash the app if schema creation fails at startup
        print(f"[startup] ensure_schema failed: {e!r}")

def _season_start_iso_utc(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    year = now.year if now.month >= 8 else now.year - 1
    dt = datetime(year, 8, 1, 0, 0, 0, tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")

def _union_by_url(a: List[Dict[str, Any]], b: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = {}
    for it in a + b:
        url = (it.get("url") or "").strip()
        if not url:
            continue
        if url not in seen:
            seen[url] = it
    return list(seen.values())

def _safe_rewrite(item: Dict[str, Any], team: str) -> Dict[str, Any]:
    """Apply rewrite_headline carefully; never raise, never shorten to stubs."""
    title = item.get("title") or ""
    summary = item.get("summary") or ""
    provider = canonicalize_provider(item.get("provider") or "")
    try:
        new_title = rewrite_headline(provider=provider, title=title, summary=summary, team=team)
        if new_title and isinstance(new_title, str):
            # Keep only if we actually improved clarity / removed ellipses, or bounded length
            if new_title != title:
                item = dict(item)  # copy
                item["title"] = new_title
    except Exception as e:
        # Never fail the request just because rewriting hiccups
        print(f"[rewrite] failed for provider={provider!r} title={title!r}: {e!r}")
    return item

@app.get("/health")
def health() -> Dict[str, str]:
    return {"ok": "true"}

@app.get("/news")
def news(
    team: str = Query(..., description="Team key, e.g., 'Arsenal'"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=200),
    since: Optional[str] = Query(None, description="ISO timestamp; default is season start"),
) -> Dict[str, Any]:
    # 1) Fetch latest from sources
    try:
        live_items = fetch_news(team)
    except Exception as e:
        print(f"[fetch] error: {e!r}")
        raise HTTPException(status_code=502, detail="Upstream fetch failed")

    # 2) Persist what we got (best-effort)
    try:
        if live_items:
            upsert_items(live_items)
    except Exception as e:
        print(f"[db] upsert_items failed: {e!r}")

    # 3) Determine window and load from DB
    since_iso = since or _season_start_iso_utc()
    try:
        stored_items = load_items(since_iso)
    except Exception as e:
        print(f"[db] load_items failed: {e!r}")
        stored_items = []

    # 4) Merge & apply policy
    merged = _union_by_url(stored_items, live_items or [])
    try:
        filtered = apply_policy_core(merged, team_code=team)
        filtered = page_with_caps(filtered, max_items=page * pageSize)  # keep prior cap semantics
    except Exception as e:
        print(f"[policy] apply failed: {e!r}")
        filtered = merged

    # 5) Rewrite headlines (balanced, no ellipses)
    rewritten: List[Dict[str, Any]] = []
    for it in filtered:
        rewritten.append(_safe_rewrite(it, team=team))

    # 6) Paging
    total = len(rewritten)
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = rewritten[start:end]

    # 7) Shape response
    return {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    }


