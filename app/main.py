# app/main.py
from __future__ import annotations

from typing import List, Dict, Any, Optional, Set, Tuple
from datetime import datetime, timezone
import re
from urllib.parse import urlparse
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder

from .fetcher import (
    fetch_rss,
    fetch_html_headlines,
    clean_summary_text,
    fetch_detail_image_and_summary,
)
from .sources import PROVIDERS, build_feed_url

APP_VERSION = "1.0.8-news-quality"

app = FastAPI(title="Hilo News API", version=APP_VERSION)

@app.get("/health")
def health() -> Dict[str, str]:
    return {
        "status": "ok",
        "version": APP_VERSION,
        "time": datetime.now(timezone.utc).isoformat(),
    }

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

# ---------- Women’s filter (MEN-ONLY FEED: ALWAYS ON) ----------
_WOMEN_KEYS: List[str] = [
    # terms
    "women", "womens", "women’s", "women's", "awfc", "wfc", "fa wsl", "wsl",
    "barclays women's", "women's super league",
    # url/path fragments
    "/women/", "/wsl/", "/awfc/", "/women-", "/womens-", "women-", "womens-",
    "-women/", "-wsl/", "-awfc/",
    # common site tags/sections
    "arsenal women", "arsenal-women", "arsenalwomen", "mariona", "miedema", "mead", "eidevall", "katie mccabe"
]

def _looks_like_womens(a: Dict[str, Any]) -> bool:
    t = (a.get("title") or "").lower()
    s = (a.get("summary") or "").lower()
    u = (a.get("url") or "").lower()
    for k in _WOMEN_KEYS:
        if k in t or k in s or k in u:
            return True
    return False

# ---------- Relevance (guard media leakage) ----------
def _is_relevant_to_team(a: Dict[str, Any], team_name: str, aliases: List[str]) -> bool:
    text = f"{a.get('title','')} {a.get('summary','')}".lower()
    if any(alias.lower() in text for alias in aliases):
        return True
    # also allow obvious URL slugs like /arsenal-
    u = (a.get("url") or "").lower()
    if any(alias.lower().replace(" ", "-") in u for alias in aliases):
        return True
    return False

# ---------- Teams metadata ----------
_TEAM_ALIASES: Dict[str, List[str]] = {
    "ARS": ["Arsenal", "Gunners", "AFC", "Arsenal FC"],
    "CHE": ["Chelsea", "CFC", "Chelsea FC"],
    "TOT": ["Tottenham", "Spurs", "Tottenham Hotspur"],
    "MCI": ["Manchester City", "Man City", "City"],
    "LIV": ["Liverpool", "LFC", "Liverpool FC"],
}
def _aliases_for(code: str) -> List[str]:
    return _TEAM_ALIASES.get(code.upper(), [code.upper()])

# ---------- Quality rules / selectors ----------
ALLOWED_OFFICIAL = {"ArsenalOfficial", "SkySports", "TheStandard", "TheTimes", "DailyMail"}
ALLOWED_FAN = {"Arseblog", "PainInTheArsenal", "ArsenalInsider"}

HIGHLIGHT_PAT = re.compile(r"\b(highlights?|full[-\s]?match|replay|gallery|photos?)\b", re.I)
LIVE_PAT = re.compile(r"\b(live( blog)?|minute[-\s]?by[-\s]?minute|as it happened|recap)\b", re.I)
CELEB_PAT = re.compile(r"\b(tvshowbiz|celebrity|showbiz|hollywood|love island|sudeikis)\b", re.I)

MATCH_REPORT_PAT = re.compile(r"\b(match\s*report|player ratings?)\b", re.I)
PLAYER_RATINGS_PAT = re.compile(r"\b(player ratings?)\b", re.I)
PREVIEW_PAT = re.compile(r"\b(preview|prediction|line[\s-]?ups?)\b", re.I)
PRESSER_PAT = re.compile(r"\b(press(?:\s)?conference|every word)\b", re.I)

# Opponent names (for per-match de-dup + opponent-centric drop if Arsenal not mentioned)
_OPPONENTS = [
    "Nottingham Forest","Forest","Manchester City","Man City","Manchester United","Man United",
    "Tottenham","Spurs","Chelsea","Liverpool","Brighton","Newcastle","Aston Villa","West Ham",
    "Everton","Brentford","Bournemouth","Wolves","Fulham","Crystal Palace","Leicester",
    "Leeds","Southampton","Luton"
]
OPPONENT_PAT = re.compile(r"\b(" + "|".join(re.escape(x) for x in _OPPONENTS) + r")\b", re.I)

def _contains(text: str, pat: re.Pattern) -> bool:
    return bool(pat.search(text or ""))

def _norm_url(u: str) -> str:
    try:
        p = urlparse(u or "")
        return (p.netloc.lower() + p.path.rstrip("/")) if p.netloc else u or ""
    except Exception:
        return u or ""

def _article_kind(a: Dict[str, Any]) -> str:
    t = f"{a.get('title','')} {a.get('summary','')}"
    if _contains(t, PLAYER_RATINGS_PAT): return "player_ratings"
    if _contains(t, MATCH_REPORT_PAT):   return "match_report"
    if _contains(t, PREVIEW_PAT):        return "preview"
    if _contains(t, PRESSER_PAT):        return "presser"
    return "general"

def _opponent_key(a: Dict[str, Any]) -> Optional[str]:
    t = f"{a.get('title','')} {a.get('summary','')}"
    m = OPPONENT_PAT.search(t)
    return m.group(1).lower() if m else None

def _is_highlight_or_live(a: Dict[str, Any]) -> bool:
    t = f"{a.get('title','')} {a.get('summary','')} {a.get('url','')}"
    return _contains(t, HIGHLIGHT_PAT) or _contains(t, LIVE_PAT)

def _is_celeb(a: Dict[str, Any]) -> bool:
    t = f"{a.get('title','')} {a.get('summary','')} {a.get('url','')}"
    return _contains(t, CELEB_PAT)

def _is_opponent_centric(a: Dict[str, Any], team_aliases: List[str]) -> bool:
    t = f"{a.get('title','')} {a.get('summary','')}"
    # Keep if clearly Arsenal-focused
    if any(re.search(rf"\b{re.escape(alias)}\b", t, re.I) for alias in team_aliases):
        return False
    # Drop if it's about an opponent and Arsenal isn't mentioned
    return bool(OPPONENT_PAT.search(t))

SOURCE_PRIORITY = ["ArsenalOfficial","SkySports","TheStandard","TheTimes","DailyMail",
                   "Arseblog","PainInTheArsenal","ArsenalInsider"]

def _source_rank(s: str) -> int:
    try:
        return SOURCE_PRIORITY.index(s)
    except ValueError:
        return len(SOURCE_PRIORITY)

# ---------- News endpoint ----------
@app.get("/news")
def get_news(
    team: Optional[str] = Query(default=None, description="Single team code, e.g. 'ARS'"),
    teamCodes: Optional[str] = Query(default=None, description="Comma-separated team codes, e.g. 'ARS,CHE'"),
    types: Optional[str] = Query(default=None, description="Comma-separated: 'official', 'fan' (default both)"),
    excludeWomen: Optional[bool] = Query(default=None, description="(ignored for team feeds; men-only enforced)"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(100, ge=1, le=100),  # default to 100
) -> JSONResponse:
    # Normalize team codes
    if teamCodes:
        team_codes = [t.strip().upper() for t in teamCodes.split(",") if t.strip()]
    elif team:
        team_codes = [team.strip().upper()]
    else:
        team_codes = ["ARS"]

    primary_team = team_codes[0]
    team_aliases = _aliases_for(primary_team)

    # Types
    allowed_types: Set[str] = {"official", "fan"}
    if types:
        parts = [p.strip().lower() for p in types.split(",") if p.strip()]
        allowed_types = set([p for p in parts if p in ("official", "fan")]) or {"official", "fan"}

    items: List[Dict[str, Any]] = []

    # ---- Fetch phase ----
    for name, meta in PROVIDERS.items():
        provider_type = (meta.get("type") or "").lower().strip()
        is_official = bool(meta.get("is_official", False))

        # Respect type filter
        if is_official and "official" not in allowed_types:
            continue
        if (not is_official) and "fan" not in allowed_types:
            continue

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
                # Clean summary
                a["summary"] = clean_summary_text(a.get("summary", ""))

                # Ensure fields exist
                a.setdefault("imageUrl", None)
                a.setdefault("thumbnailUrl", None)

                # MEN-ONLY: always exclude women's content
                if _looks_like_womens(a):
                    continue

                # Media relevance guard (require team mention) for big media
                if name in ("DailyMail", "TheTimes", "TheStandard", "SkySports"):
                    if not _is_relevant_to_team(a, team_aliases[0], team_aliases):
                        continue

                if is_official:
                    # Enrich with hero image/summary
                    page_url = a.get("url") or ""
                    if page_url:
                        detail = fetch_detail_image_and_summary(page_url)
                        img = detail.get("imageUrl") or None
                        if img:
                            a["imageUrl"] = img
                            a["thumbnailUrl"] = img
                        if not a.get("summary"):
                            a["summary"] = detail.get("summary") or a.get("summary", "")
                else:
                    # Fan pages: force no image (Panel2)
                    a["imageUrl"] = None

                items.append(a)

        except Exception:
            # fail-safe: skip provider on error
            continue

    # ---- Quality filter phase ----
    # 0) allow-list sources only (belt-and-braces; PROVIDERS already controls this)
    items = [a for a in items if (a.get("source") in ALLOWED_OFFICIAL or a.get("source") in ALLOWED_FAN)]

    # 1) hard drops
    def _drop(a: Dict[str, Any]) -> bool:
        if not a.get("title") or not a.get("url"): return True
        if _looks_like_womens(a): return True
        if _is_highlight_or_live(a): return True
        if _is_celeb(a): return True
        if _is_opponent_centric(a, team_aliases): return True
        return False

    items = [a for a in items if not _drop(a)]

    # 2) de-dup by normalized URL
    seen_keys: Set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for it in items:
        key = _norm_url(it.get("url") or "")
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(it)
    items = deduped

    # 3) Per-match de-duplication: only ONE per kind (report / preview / ratings) per opponent
    selected: List[Dict[str, Any]] = []
    kept_by_match: Dict[Tuple[str, str], int] = {}  # (opp, kind) -> index in selected

    def _kind(a: Dict[str, Any]) -> str:
        return _article_kind(a)

    def _better(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        # prefer higher source priority; tie-break newer time
        ra, rb = _source_rank(a.get("source","")), _source_rank(b.get("source",""))
        if ra != rb: return ra < rb
        return _to_dt(a.get("publishedUtc")) > _to_dt(b.get("publishedUtc"))

    for a in items:
        kind = _kind(a)
        if kind in ("match_report", "preview", "player_ratings"):
            opp = _opponent_key(a) or "_none_"
            key = (opp, kind)
            if key in kept_by_match:
                idx = kept_by_match[key]
                if _better(a, selected[idx]):
                    selected[idx] = a
                continue
            kept_by_match[key] = len(selected)
            selected.append(a)
        else:
            selected.append(a)

    # 4) Per-provider cap (scales with pageSize)
    cap = max(6, pageSize // 4)  # e.g., 100 -> 25 per provider max
    capped: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for it in selected:
        src = it.get("source") or "unknown"
        if counts.get(src, 0) >= cap:
            continue
        counts[src] = counts.get(src, 0) + 1
        capped.append(it)

    # 5) Sort newest first with stable tie-breakers
    capped.sort(
        key=lambda x: (
            _to_dt(x.get("publishedUtc")),
            -1000 + (-_source_rank(x.get("source",""))),  # prefer better sources when time ties
            x.get("id") or ""
        ),
        reverse=True
    )

    # 6) Paginate
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
    return JSONResponse(content=jsonable_encoder(payload))

# Minimal teams metadata (used by TeamsCatalog if you call it)
@app.get("/metadata/teams")
def get_teams() -> JSONResponse:
    teams = [
        {"code": "ARS", "name": "Arsenal", "aliases": ["Arsenal", "AFC", "Gunners"]},
        {"code": "CHE", "name": "Chelsea", "aliases": ["Chelsea", "CFC"]},
        {"code": "TOT", "name": "Tottenham Hotspur", "aliases": ["Tottenham", "Spurs"]},
        {"code": "MCI", "name": "Manchester City", "aliases": ["Man City", "City"]},
        {"code": "LIV", "name": "Liverpool", "aliases": ["Liverpool", "LFC"]},
    ]
    return JSONResponse(content=teams)
