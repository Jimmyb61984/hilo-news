# app/main.py
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .fetcher import (
    fetch_rss,
    fetch_html_headlines,
    fetch_detail_image_and_summary,
    best_image_for_item,
)
from .sources import PROVIDERS, build_feed_url
from .policy import apply_policy  # << ADDED

app = FastAPI(title="Hilo News API", version="1.0.0")

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _is_women_article(a: Dict[str, Any]) -> bool:
    # existing women-filter logic (unchanged)
    # ...
    return False

def _is_central_arsenal(a: Dict[str, Any]) -> bool:
    # existing central-focus logic (unchanged)
    # ...
    return True

def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for x in items:
        k = (x.get("source"), x.get("url"))
        if k in seen:
            continue
        seen.add(k)
        out.append(x)
    return out

@app.get("/news")
def news(
    team: Optional[str] = Query(default="ARS"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=100),
) -> JSONResponse:
    """
    Arsenal homepage feed (men's first team).
    """
    primary_team = (team or "ARS").upper()

    items: List[Dict[str, Any]] = []

    # Build per-provider items
    for name, meta in PROVIDERS.items():
        url = build_feed_url(name, team=primary_team)
        if not url:
            continue

        is_official = bool(meta.get("official"))
        is_html = bool(meta.get("html"))

        try:
            if is_html:
                provider_items = fetch_html_headlines(url, source_key=name, team_codes=[primary_team])
            else:
                provider_items = fetch_rss(url, source_key=name, team_codes=[primary_team])
        except Exception:
            provider_items = []

        # Enrichment for official providers (image + improved summary)
        enriched: List[Dict[str, Any]] = []
        for a in provider_items:
            if not a or not a.get("url"):
                continue

            # Men-only / central-arsenal filters (existing logic)
            if _is_women_article(a):
                continue
            if not _is_central_arsenal(a):
                continue

            if is_official:
                # Enrich with hero image/summary
                detail = fetch_detail_image_and_summary(a["url"])
                if detail and detail.get("imageUrl"):
                    a["imageUrl"] = detail["imageUrl"]
                if not a.get("summary"):
                    a["summary"] = detail.get("summary") or a.get("summary", "")

                # Use real publish time for ArsenalOfficial or drop if missing
                if name == "ArsenalOfficial":
                    real_pub = detail.get("publishedUtc")
                    if real_pub:
                        a["publishedUtc"] = real_pub
                    else:
                        # Skip item with unknown time to protect chronology
                        continue

            else:
                # Fan providers: do not show images (panel2), keep text only
                a["imageUrl"] = None
                a["thumbnailUrl"] = None

            enriched.append(a)

        items.extend(enriched)

    # Apply app policy (whitelists/blacklists, quality filters)
    items = apply_policy(items, team_code=primary_team)  # << ADDED

    # Per-provider cap (per page).
    per_provider_cap = 20
    capped: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for a in items:
        key = a.get("source") or "Unknown"
        c = counts.get(key, 0)
        if c >= per_provider_cap:
            continue
        capped.append(a)
        counts[key] = c + 1

    # Dedupe by (source,url)
    capped = _dedupe(capped)

    # Chronology (strict): publishedUtc (fallback to now)
    def _ts(x: Dict[str, Any]) -> str:
        return x.get("publishedUtc") or _now_iso()
    capped.sort(key=_ts, reverse=True)

    # Pagination (unchanged)
    total = len(capped)
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = capped[start:end]

    payload = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    }
    return JSONResponse(payload)

