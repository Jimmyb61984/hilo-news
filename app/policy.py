import re
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Optional
from urllib.parse import urlparse, parse_qs

# ------------------------
# Provider priority & sets
# ------------------------
SOURCE_PRIORITY = [
    "ArsenalOfficial", "SkySports", "TheStandard", "TheTimes", "DailyMail",
    "Arseblog", "PainInTheArsenal", "ArsenalInsider"
]
OFFICIAL_SOURCES = {"ArsenalOfficial", "SkySports", "TheStandard", "TheTimes", "DailyMail"}
FAN_SOURCES      = {"Arseblog", "PainInTheArsenal", "ArsenalInsider"}

def _source_rank(s: str) -> int:
    try:
        return SOURCE_PRIORITY.index(s)
    except ValueError:
        return len(SOURCE_PRIORITY)

# ------------------------
# Team aliases & opponents
# ------------------------
_TEAM_ALIASES: Dict[str, List[str]] = {
    "ARS": ["Arsenal", "Gunners", "AFC", "Arsenal FC"]
}
def _aliases_for(code: str) -> List[str]:
    return _TEAM_ALIASES.get(code.upper(), [code.upper()])

_OPPONENTS = [
    # Premier League (+ common short forms)
    "Nottingham Forest","Forest","Manchester City","Man City","Manchester United","Man United",
    "Tottenham","Spurs","Chelsea","Liverpool","Brighton","Newcastle","Aston Villa","West Ham",
    "Everton","Brentford","Bournemouth","Wolves","Fulham","Crystal Palace","Leicester",
    "Leeds","Southampton","Luton",
    # Cups/Europe (extend as needed)
    "Porto","Athletic Bilbao","Real Madrid","Barcelona","Bayern","PSG","Juventus","Inter",
]
OPPONENT_PAT = re.compile(r"\b(" + "|".join(re.escape(x) for x in _OPPONENTS) + r")\b", re.I)

# ------------------------
# Filters (disallow lists)
# ------------------------
_WOMEN_KEYS = [
    "women", "womens", "women’s", "women's", "awfc", "wfc", "fa wsl", "wsl",
    "barclays women's", "women's super league",
    "/women/", "/wsl/", "/awfc/", "/women-", "/womens-", "-women/", "-wsl/", "-awfc/",
    "arsenal women", "arsenal-women", "arsenalwomen"
]
_WOMEN_NAMES = {
    "rachel yankey","mariona caldentey","beth mead","vivianne miedema","stina blackstenius",
    "kim little","leah williamson","lotte wubben-moy","frida maanum","caitlin foord",
    "lia walti","steph catley","laura wienroither","katie mccabe","manuela zinsberger",
    "jen beattie","jonas eidevall"
}

HIGHLIGHT_PAT = re.compile(r"\b(highlights?|full[-\s]?match|replay|gallery|photos?)\b", re.I)
LIVE_PAT      = re.compile(r"\b(live( blog)?|as[-\s]?it[-\s]?happened|minute[-\s]?by[-\s]?minute|recap)\b", re.I)
CELEB_PAT     = re.compile(r"\b(tvshowbiz|celebrity|showbiz|hollywood|sudeikis)\b", re.I)

# ------------------------
# Article classification
# ------------------------
MATCH_REPORT_PAT = re.compile(r"\b(match\s*report|player ratings?|ratings:)\b", re.I)
PREVIEW_PAT      = re.compile(r"\b(preview|prediction|line[\s-]?ups?|how to watch)\b", re.I)
PRESSER_PAT      = re.compile(r"\b(press(?:\s)?conference|every word|presser)\b", re.I)
SCORELINE_PAT    = re.compile(r"\b(\d+)\s*[–-]\s*(\d+)\b", re.I)

def _contains(text: str, pat: re.Pattern) -> bool:
    return bool(pat.search(text or ""))

def _norm_url(u: str) -> str:
    if not u: return ""
    try:
        p = urlparse(u)
        # remove query tracking noise
        path = p.path.rstrip("/")
        return (p.netloc.lower() + path)
    except Exception:
        return u

def _is_womens(a: dict) -> bool:
    t = f"{a.get('title','')} {a.get('summary','')}".lower()
    u = (a.get('url') or '').lower()
    blob = f"{t} {u}"
    if any(k in blob for k in _WOMEN_KEYS): return True
    if any(n in blob for n in _WOMEN_NAMES): return True
    return False

def _is_highlight_or_live(a: dict) -> bool:
    t = f"{a.get('title','')} {a.get('summary','')} {a.get('url','')}"
    return _contains(t, HIGHLIGHT_PAT) or _contains(t, LIVE_PAT)

def _is_celeb(a: dict) -> bool:
    t = f"{a.get('title','')} {a.get('summary','')} {a.get('url','')}"
    return _contains(t, CELEB_PAT)

def _is_opponent_centric(a: dict, team_aliases: List[str]) -> bool:
    t = f"{a.get('title','')} {a.get('summary','')}"
    has_opp = bool(OPPONENT_PAT.search(t))
    has_ars = any(re.search(rf"\b{re.escape(alias)}\b", t, re.I) for alias in team_aliases)
    if not has_opp: return False
    # Drop when the angle clearly *leads* with the opponent/opp manager and Arsenal isn’t central.
    starts_with_opp = bool(re.match(rf"^\s*{OPPONENT_PAT.pattern}", t, re.I))
    return (starts_with_opp or not has_ars)

def _article_kind(a: dict) -> str:
    text = f"{a.get('title','')} {a.get('summary','')}"
    title = a.get("title","")
    if _contains(text, PRESSER_PAT):      return "presser"
    if _contains(text, PREVIEW_PAT):      return "preview"
    if _contains(text, MATCH_REPORT_PAT): return "match_report"
    if SCORELINE_PAT.search(title) and OPPONENT_PAT.search(text) and re.search(r"\bArsenal\b", text, re.I):
        return "match_report"
    if OPPONENT_PAT.search(text) and re.search(r"\bArsenal\b", text, re.I):
        return "match_article"
    return "general"

def _opponent_key(a: dict) -> Optional[str]:
    text = f"{a.get('title','')} {a.get('summary','')}"
    m = OPPONENT_PAT.search(text)
    return (m.group(1).lower() if m else None)

def _parse_time_iso(s: str) -> datetime:
    # Expect strict ISO; guard nulls
    if not s: return datetime(1970,1,1, tzinfo=timezone.utc)
    s = s.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime(1970,1,1, tzinfo=timezone.utc)

# ------------------------
# Core policy
# ------------------------
def apply_policy(items: List[dict], team_code: str = "ARS") -> List[dict]:
    """
    Apply all newsroom rules:
      - men’s first team only
      - drop highlights/replay/gallery/live-blogs/celeb
      - drop opponent-centric angles
      - cross-source de-dupe by URL
      - per-fixture quotas: 1 preview, 1 report, 1 ratings/article
      - topic throttling (collapse near-duplicate hot-takes)
      - strict sort by publishedUtc desc, then source priority, then id
    """
    aliases = _aliases_for(team_code)

    # 1) Basic drops
    filtered = []
    seen_urls = set()
    for a in items:
        # must have a trustworthy publishedUtc
        if not a.get("publishedUtc"):
            continue

        if _is_womens(a):
            continue
        if _is_highlight_or_live(a):
            continue
        if _is_celeb(a):
            continue
        if _is_opponent_centric(a, aliases):
            continue

        ukey = _norm_url(a.get("url",""))
        if not ukey:
            continue
        if ukey in seen_urls:
            continue
        seen_urls.add(ukey)
        filtered.append(a)

    # 2) Per-fixture quotas (keep best by source priority, then recency)
    # kinds we want to cap per opponent
    cap_kinds = {"preview", "match_report", "match_article"}
    chosen = []
    kept_by_opp_kind: Dict[Tuple[str,str], dict] = {}

    # Sort so that higher priority and more recent are considered first
    filtered.sort(
        key=lambda x: (
            _source_rank(x.get("source","")),
            -_parse_time_iso(x.get("publishedUtc")).timestamp()
        )
    )

    for a in filtered:
        kind = _article_kind(a)
        if kind in cap_kinds:
            opp = _opponent_key(a)
            if opp:
                key = (opp, kind)
                if key in kept_by_opp_kind:
                    continue  # already have one for this opponent+kind
                kept_by_opp_kind[key] = a
                chosen.append(a)
                continue
        chosen.append(a)

    # 3) Topic throttling (collapse near-duplicate hot takes within short windows)
    # Simple: limit to 1 per 3 hours per normalized title stem
    window_secs = 3 * 3600
    def _stem(title: str) -> str:
        t = (title or "").lower()
        t = re.sub(r"[^a-z0-9\s]", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        # collapse common stopwords very lightly
        stop = {"the","a","an","of","for","and","or","to","with","on","in","as","by"}
        return " ".join(w for w in t.split() if w not in stop)[:80]

    seen_stem_time: Dict[str, datetime] = {}
    final: List[dict] = []
    for a in sorted(chosen, key=lambda x: _parse_time_iso(x.get("publishedUtc")).timestamp(), reverse=True):
        s = _stem(a.get("title",""))
        t = _parse_time_iso(a.get("publishedUtc"))
        last = seen_stem_time.get(s)
        if last and (last - t).total_seconds() < window_secs:
            # too soon — skip near-duplicate take
            continue
        seen_stem_time[s] = t
        final.append(a)

    # 4) Final ordering: strict chronology, then source priority, then id for stability
    final.sort(
        key=lambda x: (
            -_parse_time_iso(x.get("publishedUtc")).timestamp(),
            _source_rank(x.get("source","")),
            x.get("id","")
        )
    )
    return final
