from typing import List, Dict
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from .models import Article, NewsResponse
from .config import TEAM_FEEDS, TIER_WEIGHTS, PAGE_SIZE_MAX, CACHE_TTL_SECONDS
from .sources import build_feed_url, PROVIDERS
from .fetcher import fetch_rss
from .cache import cache

app = FastAPI(title="Hilo News API", version="0.1.0")

# Permissive CORS for now (you can lock this down later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"ok": True}

def _collect_articles_for_team(team_code: str) -> List[Article]:
    """
    For a given team code (e.g., 'ARS'), build feed URLs from TEAM_FEEDS
    and collect articles from enabled providers (RSS only for now).
    """
    items: List[Article] = []
    team_conf = TEAM_FEEDS.get(team_code.upper())
    if not team_conf:
        return items

    # Go in tier order A > B > C (you can weight/sort later if you like)
    for tier in ("A", "B", "C"):
        for entry in team_conf.get(tier, []):
            provider = entry.get("provider")
            section = entry.get("section")
            if not provider:
                continue

            meta = PROVIDERS.get(provider)
            if not meta or meta.get("type") != "rss":
                # Skip placeholders (html) for now
                continue

            url = build_feed_url(provider, section=section, team_code=team_code)
            if not url:
                continue

            # Fetch and tag with team + (optional) leagues; for now we leave leagues empty
            try:
                fetched = fetch_rss(
                    url=url,
                    team_codes=[team_code.upper()],
                    leagues=[],  # add league codes later when you want
                    source_name=provider
                )
                items.extend(fetched)
            except Exception:
                # Robust in MVP: if one feed fails, continue with others
                continue

    return items

def _dedupe_and_sort(all_items: List[Article]) -> List[Article]:
    # Dedupe by (url+title) hash id already created in fetcher; use latest publish time if duplicates
    seen: Dict[str, Article] = {}
    for it in all_items:
        seen[it.id] = it
    deduped = list(seen.values())
    # ISO timestamps sort correctly lexicographically (newest first)
    deduped.sort(key=lambda a: a.publishedUtc, reverse=True)
    return deduped

@app.get("/news", response_model=NewsResponse)
def get_news(
    teamCodes: str = Query("ARS", description="Comma-separated team codes, e.g. ARS"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=PAGE_SIZE_MAX),
):
    """
    Return normalized news for the requested team codes.
    Example: /news?teamCodes=ARS&page=1&pageSize=25
    """
    # Cache final result per (teamCodes,page,pageSize)
    cache_key = f"news:{teamCodes}:{page}:{pageSize}"
    cached = cache.get(cache_key)
    if cached:
        return cached

    codes = [c.strip().upper() for c in teamCodes.split(",") if c.strip()]
    all_items: List[Article] = []
    for code in codes:
        all_items.extend(_collect_articles_for_team(code))

    deduped_sorted = _dedupe_and_sort(all_items)
    total = len(deduped_sorted)

    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = deduped_sorted[start:end]

    payload = NewsResponse(
        items=page_items,
        page=page,
        pageSize=pageSize,
        total=total
    )

    cache.set(cache_key, payload, ttl_seconds=CACHE_TTL_SECONDS)
    return payload
