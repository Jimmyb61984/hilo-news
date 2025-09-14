from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from app.policy import apply_policy  # EXACTLY as requested

from app.fetcher import (
    fetch_rss,
    fetch_html_headlines,
    clean_summary_text,
    fetch_detail_image_and_summary,
)
from app.sources import PROVIDERS, build_feed_url

APP_VERSION = "1.0.8-arsenal-publish-fix"

app = FastAPI(title="Hilo News API", version=APP_VERSION)

@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "version": APP_VERSION, "time": datetime.now(timezone.utc).isoformat()}

def _to_dt(iso_str: Optional[str]) -> datetime:
    if not iso_str:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime(1970, 1, 1, tzinfo=timezone.utc)

def _norm_url(u: str) -> str:
    try:
        p = urlparse(u or "")
        return (p.netloc.lower() + p.path.rstrip("/")) if p.netloc else (u or "")
    except Exception:
        return u or ""

@app.get("/news")
def get_news(
    team: Optional[str] = Query(default="ARS"),
    page: int = Query(default=1, ge=1),
    pageSize: int = Query(default=50, ge=1, le=100),
) -> JSONResponse:
    primary_team = (team or "ARS").upper()
    team_codes = [primary_team]

    items: List[Dict[str, Any]] = []

    # ---- collect from providers (unchanged, no new params) ----
    for name, meta in PROVIDERS.items():
        provider_type = (meta.get("type") or "").lower().strip()
        is_official = bool(meta.get("is_official", False))

        url = build_feed_url(name, team_code=primary_team)
        if not url:
            continue

        try:
            if provider_type == "rss":
                articles = fetch_rss(url=url, team_codes=team_codes, source_key=name, limit=60)
            elif provider_type == "html":
                sels = meta.get("selectors") or {}
                item_sel = sels.get("item"); title_sel = sels.get("title"); link_sel = sels.get("link")
                summary_sel = sels.get("summary"); date_sel = sels.get("date"); thumb_sel = sels.get("thumb")
                if not (item_sel and title_sel and link_sel):
                    continue
                articles = fetch_html_headlines(
                    url=url,
                    item_selector=item_sel,
                    title_selector=title_sel,
                    link_selector=link_sel,
                    summary_selector=summary_sel,
                    date_selector=date_sel,
                    thumb_selector=thumb_sel,
                    team_codes=team_codes,
                    source_key=name,
                    limit=48,
                )
            else:
                continue

            for a in articles:
                a["summary"] = clean_summary_text(a.get("summary", ""))
                a.setdefault("imageUrl", None)
                a.setdefault("thumbnailUrl", None)

                # ArsenalOfficial: require real publish time from detail page
                if name == "ArsenalOfficial":
                    page_url = a.get("url") or ""
                    if page_url:
                        detail = fetch_detail_image_and_summary(page_url)
                        img = detail.get("imageUrl") or None
                        if img:
                            a["imageUrl"] = img
                            a["thumbnailUrl"] = img
                        real_pub = detail.get("publishedUtc")
                        if not real_pub:
                            # protect chronology: skip if we can't prove time
                            continue
                        a["publishedUtc"] = real_pub
                        if not a.get("summary"):
                            a["summary"] = detail.get("summary") or a.get("summary", "")
                # else: unchanged for other providers

                items.append(a)

        except Exception:
            continue

    # ---- apply policy EXACTLY here, as requested ----
    items = apply_policy(items, team_code=(team or "ARS"))

    # ---- sort & paginate (unchanged) ----
    items.sort(key=lambda x: _to_dt(x.get("publishedUtc")), reverse=True)

    total = len(items)
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = items[start:end]

    payload = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    }
    return JSONResponse(content=jsonable_encoder(payload))

