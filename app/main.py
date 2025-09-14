from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import List, Optional, Dict, Any
from app.fetcher import fetch_news
from app.policy import apply_policy, PROVIDER_CAPS, WOMEN_YOUTH_KEYWORDS
from datetime import datetime

app = FastAPI(title="Hilo News API", version="2.0.0")

# --- /healthz ---------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"status": "ok", "time": datetime.utcnow().isoformat() + "Z"}

# --- /metadata/teams --------------------------------------------------------
# Unity's TeamsCatalog expects this to exist. For now we return a minimal map
# that at least covers Arsenal and a few aliases.
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

# --- /news ------------------------------------------------------------------
@app.get("/news")
def news(
    team: str = Query("ARS", description="Canonical team code"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    types: Optional[str] = Query(None, description="Comma-list of types: official,fan"),
    excludeWomen: bool = Query(True, description="If true, filters WSL/Women/U21/U18/Academy")
):
    """
    Returns:
      {
        "items": [ { title, url, provider, type, summary, imageUrl, publishedUtc }, ... ],
        "page": int,
        "pageSize": int,
        "total": int
      }
    """
    # 1) Fetch raw items from all configured sources (official + fan),
    #    respecting the 'types' filter if provided.
    allowed_types = None
    if types:
        allowed_types = {t.strip().lower() for t in types.split(",") if t.strip()}

    raw_items = fetch_news(team_code=team, allowed_types=allowed_types)

    # 2) Apply policy BEFORE pagination (women/youth filter, caps, dedupe, sort).
    items = apply_policy(
        items=raw_items,
        team_code=team,
        exclude_women=excludeWomen
    )

    # 3) Pagination (stable).
    total = len(items)
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = items[start:end]

    payload = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total
    }
    return JSONResponse(payload)

