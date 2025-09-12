# app/main.py
from __future__ import annotations

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from .fetcher import fetch_rss, fetch_html_headlines, clean_summary_text, fetch_detail_image_and_summary
from .sources import PROVIDERS, build_feed_url

APP_VERSION = "1.0.3-hero-images"

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

@app.get("/news")
def get_news(
    team: Optional[str] = Query(default=None, description="Single team code, e.g. 'ARS'"),
    teamCodes: Optional[str] = Query(default=None, description="Comma-separated team codes, e.g. 'ARS,CHE'"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
) -> JSONResponse:
    # Normalize team codes
    if teamCodes:
        team_codes = [t.strip().upper() for t in teamCodes.split(",") if t.strip()]
    elif team:
        team_codes = [team.strip().upper()]
    else:
        team_codes = ["ARS"]

    items: List[Dict[str, Any]] = []

    for name, meta in PROVIDERS.items():
        provider_type = (meta.get("type") or "").lower().strip()
        is_official = bool(meta.get("is_official", False))
        url = build_feed_url(name, team_code=team_codes[0])
        if not url:
            continue

        try:
            if provider_type == "rss":
                articles = fetch_rss(url=url, team_codes=team_codes, source_key=name, limit=60)

            elif provider_type == "html":
                sels = meta.get("selectors") or {}
                item_sel = sels.get("item")
                title_sel = sels.get("title")
                link_sel = sels.get("link")
                summary_sel = sels.get("summary")
                date_sel = sels.get("date")
                thumb_sel = sels.get("thumb")

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

            # Post-process per provider policy
            for a in articles:
                # Always clean summary
                a["summary"] = clean_summary_text(a.get("summary", ""))

                # Ensure fields exist
                a.setdefault("imageUrl", None)
                a.setdefault("thumbnailUrl", None)

                if is_official:
                    # Load article page once to pick a HERO image + better description
                    page_url = a.get("url") or ""
                    if page_url:
                        detail = fetch_detail_image_and_summary(page_url)
                        img = detail.get("imageUrl") or None
                        if img:
                            a["imageUrl"] = img
                            a["thumbnailUrl"] = img  # back-compat alias
                        # prefer page description if list summary was empty
                        if not a.get("summary"):
                            a["summary"] = detail.get("summary") or a.get("summary", "")
                else:
                    # Fan pages: force no image (Panel2)
                    a["imageUrl"] = None
                    # keep thumbnailUrl as-is (if you ever want it for tiny icons), but Panel2 ignores it

                # Collect
                items.append(a)

        except Exception:
            continue

    # De-dup by (url, title)
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for it in items:
        key = (it.get("url"), it.get("title"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    # Sort newest first
    deduped.sort(key=lambda x: _to_dt(x.get("publishedUtc")), reverse=True)

    # Paginate
    total = len(deduped)
    start = (page - 1) * pageSize
    end = start + pageSize
    page_items = deduped[start:end]

    payload = {
        "items": page_items,
        "page": page,
        "pageSize": pageSize,
        "total": total,
    }
    return JSONResponse(content=jsonable_encoder(payload))
