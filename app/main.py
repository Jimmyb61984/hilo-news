from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from app.policy import (
    ProviderPolicy,
    apply_policy_core,
    KNOWN_FAN_SITES,
    OFFICIAL_SITES,
    is_allowed_provider,
)

# >>> ADDED: import the headline rewriter (only change at import section)
from app.headlines import rewrite_headline  # headline balancer

app = FastAPI(title="Hilo News API")

# ---------------------------------------------------------------------
# Models (unchanged)
# ---------------------------------------------------------------------
class NewsItem(BaseModel):
    title: str
    url: str
    summary: Optional[str] = None
    imageUrl: Optional[str] = None
    provider: str
    type: str  # "official" | "fan"
    publishedUtc: str
    id: str


class NewsResponse(BaseModel):
    items: List[NewsItem]
    page: int
    pageSize: int
    total: int


# ---------------------------------------------------------------------
# Source mocks / adapters (unchanged placeholders or your real fetchers)
# ---------------------------------------------------------------------
def fetch_evening_standard(team: str) -> List[Dict[str, Any]]:
    # ... your real implementation ...
    return []


def fetch_daily_mail(team: str) -> List[Dict[str, Any]]:
    # ... your real implementation ...
    return []


def fetch_fan_sites(team: str) -> List[Dict[str, Any]]:
    # ... your real implementation ...
    return []


PROVIDERS = {
    "EveningStandard": fetch_evening_standard,
    "DailyMail": fetch_daily_mail,
    "Fan": fetch_fan_sites,
}

# ---------------------------------------------------------------------
# Utilities (unchanged)
# ---------------------------------------------------------------------
def parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(tz=None)


def within_days(s: str, days: int = 7) -> bool:
    try:
        dt = parse_dt(s)
        return dt >= datetime.now(tz=dt.tzinfo) - timedelta(days=days)
    except Exception:
        return True


def ensure_type(provider: str) -> str:
    p = provider.strip()
    if p in OFFICIAL_SITES:
        return "official"
    if p in KNOWN_FAN_SITES:
        return "fan"
    return "official"


def normalize_item(raw: Dict[str, Any]) -> Dict[str, Any]:
    # Map/normalize fields into our canonical shape, keep original title
    return {
        "title": raw.get("title") or "",
        "url": raw.get("url") or "",
        "summary": raw.get("summary"),
        "imageUrl": raw.get("imageUrl"),
        "provider": raw.get("provider") or raw.get("source") or "",
        "type": raw.get("type") or ensure_type(raw.get("provider") or ""),
        "publishedUtc": raw.get("publishedUtc") or raw.get("published_at") or datetime.utcnow().isoformat() + "Z",
        "id": raw.get("id") or raw.get("url") or "",
    }


# ---------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------
@app.get("/healthz")
def health() -> Dict[str, str]:
    return {"ok": "true"}


# ---------------------------------------------------------------------
# Main news endpoint
# ---------------------------------------------------------------------
@app.get("/news", response_model=NewsResponse)
def get_news(
    team: str = Query(..., description="Team to filter by"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(20, ge=1, le=100),
    hideFan: bool = Query(False),
    maxAgeDays: int = Query(7, ge=1, le=30),
):
    """
    Aggregate, policy-filter and return paginated news.
    """

    # 1) Gather raw items from all providers for this team
    raw_union: List[Dict[str, Any]] = []
    for name, fetch in PROVIDERS.items():
        try:
            raw = fetch(team)
            if not raw:
                continue
            raw_union.extend(raw)
        except Exception:
            # Fail-open per provider; we don't want a 500 because one source is down
            continue

    # 2) Normalize
    normalized = [normalize_item(r) for r in raw_union]

    # 3) Filter by provider allowlist/denylist (policy)
    filtered = [it for it in normalized if is_allowed_provider(it.get("provider", ""))]

    # 4) Optionally hide fan sites
    if hideFan:
        filtered = [it for it in filtered if (it.get("type") or "official") != "fan"]

    # 5) Drop stale (older than maxAgeDays)
    if maxAgeDays:
        filtered = [it for it in filtered if within_days(it.get("publishedUtc", ""), days=maxAgeDays)]

    # 6) Apply de-duplication / ranking policy
    core_items = apply_policy_core(filtered)

    # >>> ADDED: 6.5) Rewrite headlines for balance/quality (idempotent, safe)
    try:
        for _it in core_items:
            t = _it.get("title")
            if t:
                _it["title"] = rewrite_headline(
                    t,
                    provider=_it.get("provider"),
                    summary=_it.get("summary"),
                )
    except Exception:
        # Fail open: never block the feed on rewrite issues
        pass

    # 7) Sort newest first
    core_items.sort(key=lambda x: x.get("publishedUtc", ""), reverse=True)

    # 8) Pagination
    total = len(core_items)
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = core_items[start:end]

    # 9) Shape response
    response = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    }
    return JSONResponse(response)

