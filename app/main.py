from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict, Any
from hashlib import sha1
from datetime import datetime, timezone

from app.fetcher import fetch_news
# Use split policy: core filters keep totals high; caps applied per page only
from app.policy import apply_policy_core, page_with_caps, canonicalize_provider
from app.db import ensure_schema, upsert_items, load_items
from app.headlines import rewrite_headline  # ← headline polish

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
    pageSize: int = Query(25, ge=1, le=100),  # default 25
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
    # normalize inputs
    team_code = (team or "ARS").strip()
    allowed_types = None
    if types:
        allowed_types = {t.strip().lower() for t in types.split(",") if t.strip()}

    since_iso = since or _season_start_iso_utc()

    # fetch fresh, persist, and merge with history (season-length browsing)
    try:
        fresh_items = fetch_news(team_code=team_code)
    except Exception:
        fresh_items = []
    if fresh_items:
        upsert_items(fresh_items)

    history_items = load_items(since_iso, team_code)
    merged = _union_by_url(fresh_items, history_items)

    # core policy (filters, relevance, sorting) before pagination/caps
    filtered = apply_policy_core(
        merged,
        team_code=team_code,
        allowed_types=allowed_types,
        exclude_women=excludeWomen,
    )

    # per-page caps + final pagination
    page_items, total = page_with_caps(filtered, page=page, page_size=pageSize)

    # finalize each item: provider canonicalization, deterministic id, headline polish
    out: List[Dict[str, Any]] = []
    for it in page_items:
        provider = canonicalize_provider(it.get("provider") or "")
        url = it.get("url") or ""
        title_raw = it.get("title") or ""
        summary = it.get("summary") or None

        it_out = dict(it)  # shallow copy to avoid mutating shared list
        it_out["provider"] = provider
        it_out["id"] = _mk_id(provider, url)
        # rewrite the headline with balanced length; uses summary to avoid stubby results
        it_out["title"] = rewrite_headline(title_raw, summary)

        out.append(it_out)

    return JSONResponse({
        "items": out,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    })
