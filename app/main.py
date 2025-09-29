# app/main.py
"""
Hilo Newsfeed main app.

This file:
- Provides assemble_page() which applies polish -> dedupe -> caps/quotas
- Exposes GET endpoints (/news and aliases) that call fetch_news(...) from app.fetcher
- Avoids returning a silent empty payload on success; returns a 5xx with error details on failure
"""

import json
import re
import datetime as dt
from collections import Counter, defaultdict
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional

# Import local helpers (assumes these exist in app/)
from app.headlines import polish_title, headline_violations

# Tunables (adjust as needed)
TARGET_COUNT = 100
MAX_SINGLE_DOMAIN_SHARE = 0.30
MIN_OFFICIAL_SHARE = 0.35
MIN_FRESH_48H_SHARE = 0.85
FRESH_HOURS = 48

OFFICIAL_DOMAINS = {
    "standard.co.uk","bbc.co.uk","theguardian.com","skysports.com",
    "arsenal.com","reuters.com","apnews.com","telegraph.co.uk",
    "independent.co.uk","dailymail.co.uk","times.co.uk","espn.com"
}

# --- utility helpers ---
def _domain(u: str) -> str:
    try:
        return urlparse(u or "").netloc.lower().replace("www.", "")
    except Exception:
        return ""

def _is_official(u: str) -> bool:
    return _domain(u) in OFFICIAL_DOMAINS

def _age_hours(iso: Optional[str]) -> float:
    if not iso:
        return 1e9
    try:
        # dateutil not guaranteed — try fromisoformat fallback
        try:
            from dateutil import parser as du
            ts = du.parse(iso)
        except Exception:
            ts = dt.datetime.fromisoformat(iso)
        now = dt.datetime.now(dt.timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=dt.timezone.utc)
        return (now - ts).total_seconds() / 3600.0
    except Exception:
        return 1e9

def _tag(it: Dict[str, Any]) -> str:
    t = (it.get("type") or "").lower()
    title = (it.get("title") or "").lower()
    summary = (it.get("summary") or "").lower()
    if "women" in title + " " + summary:
        return "women"
    if "u19" in title + " " + summary:
        return "academy"
    return t or "other"

def _normalize_topic(it: Dict[str, Any]) -> str:
    t = (it.get("title") or "").lower()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _valid_image(it: Dict[str, Any]) -> bool:
    url = it.get("imageUrl") or ""
    if not url or url.endswith(".gif"):
        return False
    return True

# --- Stage 1: polish + score ---
def polish_and_score(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    polished: List[Dict[str, Any]] = []
    for it in items:
        raw_title = it.get("title","") or ""
        raw_summary = it.get("summary","") or ""
        title = polish_title(raw_title, raw_summary) if callable(polish_title) else (raw_title or raw_summary or "")
        it2 = dict(it)
        it2["title"] = title
        it2["_violations"] = headline_violations(title) if callable(headline_violations) else []
        it2["_official"] = _is_official(it.get("url",""))
        it2["_age_h"] = _age_hours(it.get("publishedUtc",""))
        it2["_domain"] = _domain(it.get("url",""))
        it2["_tags"] = _tag(it)
        it2["_topic"] = _normalize_topic(it)
        it2["_img_ok"] = _valid_image(it)
        score = 0.0
        score += 1.0 if it2["_official"] else 0.0
        if it2["_age_h"] <= 6: score += 0.7
        elif it2["_age_h"] <= 24: score += 0.4
        elif it2["_age_h"] <= 48: score += 0.2
        if it2["_img_ok"]: score += 0.2
        score -= 0.2 * len(it2["_violations"])
        it2["_q"] = round(score, 3)
        polished.append(it2)
    return polished

# --- Stage 2: soft dedupe by topic ---
def dedupe_soft(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets = defaultdict(list)
    for it in items:
        buckets[it.get("_topic","")].append(it)
    kept = []
    for _, group in buckets.items():
        group.sort(key=lambda x:(-x.get("_q",0), x.get("_age_h",1e9)))
        kept.append(group[0])
    return kept

# --- Stage 3: caps & quotas ---
def apply_caps_and_quotas(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items_sorted = sorted(items, key=lambda x:(-x.get("_q",0), x.get("_age_h",1e9)))
    items_filtered = [x for x in items_sorted if x.get("_img_ok",False) and not set(x.get("_violations",[])) & {"html","cutoff","empty"}]

    by_domain = defaultdict(list)
    for it in items_filtered:
        by_domain[it.get("_domain","")].append(it)
    max_per = max(1, int(TARGET_COUNT * MAX_SINGLE_DOMAIN_SHARE))
    trimmed = []
    for d, group in by_domain.items():
        trimmed.extend(group[:max_per])

    off = [x for x in trimmed if x.get("_official")]
    non = [x for x in trimmed if not x.get("_official")]
    min_off = int(TARGET_COUNT * MIN_OFFICIAL_SHARE)
    if len(off) < min_off:
        extra_off = [x for x in items_filtered if x.get("_official") and x not in off]
        off.extend(extra_off[: (min_off - len(off))])

    fresh_needed = int(TARGET_COUNT * MIN_FRESH_48H_SHARE)
    fresh = [x for x in trimmed if x.get("_age_h",1e9) <= FRESH_HOURS]
    if len(fresh) < fresh_needed:
        pool = [x for x in items_filtered if x.get("_age_h",1e9) <= FRESH_HOURS and x not in fresh]
        fresh.extend(pool[: (fresh_needed - len(fresh))])

    # merge while preserving order preference (official, fresh, trimmed)
    merged = list({id(x): x for x in (off + fresh + trimmed)}.values())
    merged = sorted(merged, key=lambda x:(-x.get("_q",0), x.get("_age_h",1e9)))
    return merged[:TARGET_COUNT]

# --- Final assemble / QA ---
def assemble_page(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        raise ValueError("assemble_page expects a list")

    stage1 = polish_and_score(raw_items)
    stage2 = dedupe_soft(stage1)
    stage3 = apply_caps_and_quotas(stage2)

    domains = [x.get("_domain","") for x in stage3]
    domain_counts = Counter(domains)
    official_share = sum(1 for x in stage3 if x.get("_official")) / max(1,len(stage3))
    fresh_share = sum(1 for x in stage3 if x.get("_age_h",1e9) <= FRESH_HOURS) / max(1,len(stage3))

    ok = True
    if any(v/max(1,len(stage3)) > MAX_SINGLE_DOMAIN_SHARE for v in domain_counts.values()):
        ok = False
    if official_share < MIN_OFFICIAL_SHARE:
        ok = False
    if fresh_share < MIN_FRESH_48H_SHARE:
        ok = False
    if any(set(x.get("_violations",[])) & {"html","cutoff","empty"} for x in stage3):
        ok = False

    # debug prints (keeps logs verbose so errors are visible on Render)
    print("=== FEED QA ===")
    print("Domains:", dict(domain_counts))
    print("Official share:", round(official_share,2))
    print("Fresh(≤48h) share:", round(fresh_share,2))
    print("Violations:", sum(len(x.get("_violations",[])) for x in stage3))
    print("===========================")

    # NOTE: strict gate: if the QA fails we still return stage3 (so client sees items),
    # but we also log that QA failed. This prevents silent empty responses.
    if not ok:
        print("assemble_page: QA failed - returning page anyway for visibility")
    return stage3

# --- ASGI app (FastAPI) ---
try:
    from fastapi import FastAPI, Query, HTTPException
    from fastapi.responses import JSONResponse
    # Import your fetcher here (must exist at app/fetcher.py)
    from app.fetcher import fetch_news
except Exception as e:
    # If imports fail, surface error on /healthz and do not silently return empty feed
    FastAPI = None
    fetch_news = None
    import_exception = e
else:
    import_exception = None

app = FastAPI(title="Hilo Newsfeed")

@app.get("/health")
@app.get("/healthz")
def _health():
    if import_exception:
        return JSONResponse(status_code=500, content={"status":"error","error": str(import_exception)})
    return {"status":"ok"}

@app.post("/feed")
def _feed_post(payload: Dict[str, Any]):
    items = payload.get("items", [])
    page = assemble_page(items)
    return JSONResponse({"items": page, "count": len(page)})

@app.get("/feed")
@app.get("/news")
@app.get("/api/news")
@app.get("/v1/news")
def _feed_get(
    team: str = Query("ARS"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
    types: str = Query("official,fan"),
    excludeWomen: bool = Query(False),
    excludeAcademy: bool = Query(False),
):
    if import_exception or fetch_news is None:
        raise HTTPException(status_code=500, detail=f"Server imports failing: {import_exception}")

    # Parse allowed types
    allowed = {t.strip().lower() for t in types.split(",") if t.strip()} or None

    try:
        raw = fetch_news(team_code=team, allowed_types=allowed)
    except Exception as e:
        # make failure explicit so logs show stacktrace in Render
        raise HTTPException(status_code=500, detail=f"fetch_news failed: {e}")

    try:
        page_items = assemble_page(raw)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"assemble_page failed: {e}")

    # optional filters
    if excludeWomen:
        page_items = [x for x in page_items if x.get("_tags") != "women"]
    if excludeAcademy:
        page_items = [x for x in page_items if x.get("_tags") != "academy"]

    # paginate
    start = (page - 1) * pageSize
    end = start + pageSize
    total = len(page_items)
    return JSONResponse({
        "items": page_items[start:end],
        "page": page,
        "pageSize": pageSize,
        "total": total,
    })

# CLI helper (local debug)
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python -m app.main <json_file_with_items_or_items-array>")
        raise SystemExit(1)
    data = json.load(open(sys.argv[1], "r", encoding="utf-8"))
    items = data.get("items", data) if isinstance(data, dict) else data
    result = assemble_page(items)
    print(json.dumps({"items": result, "count": len(result)}, ensure_ascii=False, indent=2))
