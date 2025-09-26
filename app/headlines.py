from __future__ import annotations

import os
from typing import Any, Dict, List

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

# PROJECT-SPECIFIC policy imports (do NOT import ProviderPolicy)
from app.policy import apply_policy_core, page_with_caps, canonicalize_provider

# Headline rewriter from our project file
from app.headlines import rewrite_headline

app = FastAPI(title="Hilo News API")

# CORS for Unity / Editor / Render
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "20"))
MAX_PAGE_SIZE = int(os.getenv("MAX_PAGE_SIZE", "100"))


def _rewrite_item_title(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safe, in-place title rewrite. Never introduces ellipses or stubby fragments.
    """
    try:
        title = item.get("title") or ""
        provider = item.get("provider")
        summary = item.get("summary")
        new_title = rewrite_headline(title, provider=provider, summary=summary)
        if new_title:
            item["title"] = new_title
    except Exception:
        # Fail-safe: keep original title on any error
        pass
    return item


@app.get("/news")
def news(
    team: str = Query(..., description="Team name to filter feed for"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """
    Returns paginated news for a given team.
    Titles are rewritten for balance (no stubby truncation, no trailing ellipses).
    """
    # 1) Pull filtered + ranked items from policy layer
    items: List[Dict[str, Any]] = apply_policy_core(team)

    # 2) Canonicalize provider names using project helper
    for it in items:
        if "provider" in it:
            it["provider"] = canonicalize_provider(it["provider"])

    # 3) Rewrite titles with our balanced rules
    items = [_rewrite_item_title(it) for it in items]

    # 4) Paginate with caps
    paged = page_with_caps(items, page=page, pageSize=pageSize, maxPageSize=MAX_PAGE_SIZE)
    return paged
