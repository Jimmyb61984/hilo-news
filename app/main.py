from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict, Any
from hashlib import sha1
from datetime import datetime, timezone

from app.fetcher import fetch_news
# Use split policy: core filters keep totals high; caps applied per page only
from app.policy import apply_policy_core, page_with_caps, canonicalize_provider
from app.db import ensure_schema, upsert_items, load_items

app = FastAPI(title="Hilo News API", version="2.1.0")

# --- startup: ensure DB schema ------------------------------------------------
@app.on_event("startup")
def _startup():
    ensure_schema()

# --- utils -------------------------------------------------------------------
def _mk_id(provider: str, url: str) -> str:
    key = f"{(provider or '').strip()}|{(url or '').strip()}".encode("utf-8")
    return sha1(key).hexdigest()

def _season_start_iso_utc(today: Optional[datetime] = None) -> str:
    d = (today or datetime.now(timezone.utc))
    season_year = d.year if d.month >= 7 else d.year - 1
    return f"{season_year}-08-01T00:00:00Z"

def _union_by_url(items_a: List[Dict[str, Any]], items_b: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Union preferring newer item when URLs collide."""
    by_url: Dict[str, Dict[str, Any]] = {}
    def _ingest(lst: List[Dict[str, Any]]):
        for it in lst:
            u = (it.get("url") or "").strip().lower()
            if not u:
                continue
            prev = by_url.get(u)
            if not prev:
                by_url[u] = it
            else:
                # prefer the one with newer publishedUtc
                new_dt = it.get("publishedUtc") or ""
                old_dt = prev.get("publishedUtc") or ""
                if new_dt > old_dt:
                    by_url[u] = it
    _ingest(items_a)
    _ingest(items_b)
    return list(by_url.values())

# --- /healthz -----------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}

# --- /metadata/teams ----------------------------------------------------------
@app.get("/metadata/teams")
def metadata_teams():
    # Canonical code: 'ARS'
    return {
        "ARS": {
            "code": "ARS",
            "name": "Arsenal",
            "aliases": ["Arsenal", "Arsenal FC", "Gunners", "Arse", "ARS"]
        }
    }

# --- /news --------------------------------------------------------------------
@app.get("/news")
def news(
    team: str = Query("ARS", description="Canonical team code"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),  # default raised from 20 -> 25
    types: Optional[str] = Query(None, description="Comma-list of types: official,fan"),
    excludeWomen: bool = Query(True, description="If true, filters WSL/Women/U21/U18/Academy"),
    since: Optional[str] = Query(None, description="ISO start for history merge; defaults to season start")
):
    """
    Returns:
      {
        "items": [ { id, title, url, provider, type, summary, imageUrl, publishedUtc }, ... ],
        "page": int,
        "pageSize": int,
        "total": int
      }
    """
    # 1) Allowed types
    allowed_types = None
    if types:
        allowed_types = {t.strip().lower() for t in types.split(",") if t.strip()}

    # 2) Live fetch
    live_items = fetch_news(team_code=team, allowed_types=allowed_types)

    # 3) Persist live (best-effort)
    try:
        upsert_items(live_items)
    except Exception:
        # non-fatal — keep serving live results
        pass

    # 4) Load historical since season start (or explicit 'since')
    since_iso = since or _season_start_iso_utc()
    historical = load_items(since_iso=since_iso)

    # 5) Union (historical + live) BEFORE policy
    raw_union = _union_by_url(historical, live_items)

    # 6) Apply CORE policy (women/youth, relevance, dedupe, sort) — NO GLOBAL CAPS
    core_items = apply_policy_core(items=raw_union, team_code=team, exclude_women=excludeWomen)

    # 7) Deterministic IDs across the full filtered set
    for it in core_items:
        if not it.get("id"):
            it["id"] = _mk_id(it.get("provider", ""), it.get("url", ""))

    # 8) Total is the size of the filtered inventory (stays large)
    total = len(core_items)

    # 9) Compose the requested page with PER-PAGE CAPS ONLY
    page_items = page_with_caps(core_items, page=page, page_size=pageSize)

    payload = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total
    }
    return JSONResponse(payload)

# --- /debug/news-stats --------------------------------------------------------
@app.get("/debug/news-stats")
def news_stats(
    team: str = Query("ARS"),
    types: Optional[str] = Query(None),
    excludeWomen: bool = Query(True),
    since: Optional[str] = Query(None),
    samplePageSize: int = Query(25, ge=1, le=100)
):
    """
    Observability:
    - pre-policy tallies (historical+live union)
    - post-policy tallies (after core filters, before caps)
    - page1 tallies (after per-page caps)
    """
    allowed_types = None
    if types:
        allowed_types = {t.strip().lower() for t in types.split(",") if t.strip()}

    live_items = fetch_news(team_code=team, allowed_types=allowed_types)
    try:
        upsert_items(live_items)
    except Exception:
        pass

    since_iso = since or _season_start_iso_utc()
    historical = load_items(since_iso=since_iso)
    raw_union = _union_by_url(historical, live_items)

    def _tally(lst: List[Dict[str, Any]]):
        d: Dict[str, int] = {}
        for it in lst:
            prov = canonicalize_provider(it.get("provider", ""))
            d[prov] = d.get(prov, 0) + 1
        return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))

    pre_counts = _tally(raw_union)
    core = apply_policy_core(items=raw_union, team_code=team, exclude_women=excludeWomen)
    post_counts = _tally(core)
    page1_counts = _tally(page_with_caps(core, page=1, page_size=samplePageSize))

    return {
        "since": since_iso,
        "pre_policy_total": len(raw_union),
        "pre_policy_by_provider": pre_counts,
        "post_policy_total": len(core),              # stays large
        "post_policy_by_provider": post_counts,      # after filters, before caps
        "page1_by_provider": page1_counts            # after per-page caps
    }
