from __future__ import annotations
from typing import List, Dict, Any, Set, Tuple
from urllib.parse import urlparse
import re
import string
from datetime import datetime, timedelta

# --- Canonical provider names ------------------------------------------------
PROVIDER_ALIASES = {
    # Official / tier-1
    "arsenal.com": "ArsenalOfficial",
    "www.arsenal.com": "ArsenalOfficial",

    "www.skysports.com": "SkySports",
    "skysports.com": "SkySports",

    "www.dailymail.co.uk": "DailyMail",
    "dailymail.co.uk": "DailyMail",

    "www.standard.co.uk": "EveningStandard",
    "standard.co.uk": "EveningStandard",

    "www.thetimes.co.uk": "TheTimes",
    "thetimes.co.uk": "TheTimes",

    # Fan (Arsenal-specific)
    "arseblog.com": "Arseblog",
    "www.arseblog.com": "Arseblog",

    "paininthearsenal.com": "PainInTheArsenal",
    "www.paininthearsenal.com": "PainInTheArsenal",

    "arsenalinsider.com": "ArsenalInsider",
    "www.arsenalinsider.com": "ArsenalInsider",
}

# What we treat as "official / tier-1" in our mix (eligible for Panel1 if image present)
OFFICIAL_SET: Set[str] = {
    "ArsenalOfficial", "SkySports", "DailyMail", "EveningStandard", "TheTimes"
}

# Fan sites that are Arsenal-specific (always relevant)
FAN_ALWAYS_RELEVANT: Set[str] = {"Arseblog", "PainInTheArsenal", "ArsenalInsider"}

# --- Relevance filter (Arsenal-only scope) -----------------------------------
# Conservative: require "arsenal" OR key Arsenal entities in title/summary.
# ArsenalOfficial + dedicated Arsenal fan sites are auto-relevant.
ARSENAL_RELEVANCE_TERMS: List[re.Pattern] = [
    re.compile(r"\barsenal\b", re.I),
    re.compile(r"\bgunners\b", re.I),
    re.compile(r"\barteta\b", re.I),
    re.compile(r"\bsaka\b", re.I),
    re.compile(r"\bodegaard\b", re.I),
    re.compile(r"\bmartinelli\b", re.I),
    re.compile(r"\brice\b", re.I),
    re.compile(r"\bhavertz\b", re.I),
    re.compile(r"\bsaliba\b", re.I),
    re.compile(r"\bben\s*white\b", re.I),
    re.compile(r"\bgabriel\b", re.I),       # (CB) generic; acceptable when combined with other signals
    re.compile(r"\bjesus\b", re.I),         # Gabriel Jesus
    re.compile(r"\btrossard\b", re.I),
    re.compile(r"\btimber\b", re.I),
    re.compile(r"\braya\b", re.I),
    re.compile(r"\bnketiah\b", re.I),
    re.compile(r"\bsmith\s*rowe\b", re.I),
    re.compile(r"\bnelson\b", re.I),
]

def _is_relevant_to_arsenal(item: Dict[str, Any]) -> bool:
    provider = item.get("provider", "")
    if provider == "ArsenalOfficial" or provider in FAN_ALWAYS_RELEVANT:
        return True
    text = f"{item.get('title','')} {item.get('summary','')}"
    return any(p.search(text) for p in ARSENAL_RELEVANCE_TERMS)

# --- Women / Youth / Academy filters (apply EARLY) ---------------------------
WOMEN_YOUTH_KEYWORDS: List[str] = [
    "women", "womens", "wsl", "fa wsl", "ladies",
    "academy", "u23", "u21", "u20", "u19", "u18", "u17",
    "u-23", "u-21", "u-20", "u-19", "u-18", "u-17",
    "youth", "development squad",
    # examples seen slipping through from general feeds:
    "sam kerr", "chelsea women", "barclays wsl"
]

def _is_women_or_youth(item: Dict[str, Any]) -> bool:
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    return any(k in text for k in WOMEN_YOUTH_KEYWORDS)

# --- Per-provider caps (apply BEFORE pagination) -----------------------------
# Caps tuned to force a healthy mix on page 1 (reduce dominance).
# (This is our “balanced default” that still allows ArsenalOfficial to lead.)
PROVIDER_CAPS: Dict[str, int] = {
    # Fan sites (kept tight)
    "Arseblog": 2,
    "PainInTheArsenal": 1,
    "ArsenalInsider": 1,

    # Tier-1/general outlets
    "DailyMail": 2,
    "SkySports": 2,
    "EveningStandard": 2,
    "TheTimes": 1,

    # ArsenalOfficial (allow more — headline content for Panel1)
    "ArsenalOfficial": 6,
}

def canonicalize_provider(provider_or_url: str) -> str:
    """
    Map hostnames or shorthand to canonical provider names.
    Accepts either a hostname/URL or already-canonical name.
    """
    if provider_or_url in PROVIDER_ALIASES.values():
        return provider_or_url
    try:
        host = urlparse(provider_or_url).netloc or provider_or_url
        if host in PROVIDER_ALIASES:
            return PROVIDER_ALIASES[host]
    except Exception:
        pass
    # bare key fallback (e.g., "Arseblog")
    return PROVIDER_ALIASES.get(provider_or_url, provider_or_url)

# --- Quality score used for tie-breaks and near-dupe winner selection --------
def _score(item: Dict[str, Any]) -> float:
    base = 1.0
    if item.get("provider") in OFFICIAL_SET:
        base += 1.0
    if item.get("imageUrl"):  # helps Panel1 eligibility
        base += 0.2
    return base

# --- Cross-provider near-duplicate collapse ----------------------------------
# Goal: collapse essentially identical pieces (e.g., multiple match reports).
# Approach: normalize titles, then equal/very-similar within a short date window.
_PUNC_TABLE = str.maketrans("", "", string.punctuation)
_STOPWORDS = {
    "the","a","an","and","or","to","of","vs","v","on","in","for","with","at","as",
    "match","report","live","blog","preview","reaction","player","ratings","analysis",
    "recap","review","result","results","highlights","coverage"
}
# Markers we strip from titles to avoid false duplicates differing by suffix/prefix
_TITLE_STRIPPERS = [
    r"\blive\s+blog\b",
    r"\bplayer\s+ratings\b",
    r"\banalysis\b",
    r"\bpreview\b",
    r"\breaction\b",
    r"\bmatch\s+report\b",
]
_TITLE_STRIPPER_RE = re.compile("|".join(_TITLE_STRIPPERS), re.I)

def _norm_title(s: str) -> str:
    s = _TITLE_STRIPPER_RE.sub("", s or "")
    s = s.translate(_PUNC_TABLE).lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _tokens(s: str) -> Set[str]:
    return {t for t in _norm_title(s).split() if t and t not in _STOPWORDS}

def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0

def _parse_utc(dt: str) -> datetime:
    # Accepts "YYYY-MM-DDTHH:MM:SSZ" or fallback empty -> epoch
    try:
        if dt and dt.endswith("Z"):
            return datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ")
        if dt:
            # tolerant parse if provider forgot 'Z'
            return datetime.fromisoformat(dt.replace("Z",""))
    except Exception:
        pass
    return datetime(1970,1,1)

def _choose_better(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    # Prefer higher score; if tie, prefer official; if tie, prefer has image; then newer; then lexical title
    sl, sr = _score(left), _score(right)
    if sl != sr:
        return left if sl > sr else right
    lo, ro = (left.get("provider") in OFFICIAL_SET), (right.get("provider") in OFFICIAL_SET)
    if lo != ro:
        return left if lo else right
    li, ri = bool(left.get("imageUrl")), bool(right.get("imageUrl"))
    if li != ri:
        return left if li else right
    tl, tr = _parse_utc(left.get("publishedUtc","")), _parse_utc(right.get("publishedUtc",""))
    if tl != tr:
        return left if tl > tr else right
    return left if (left.get("title","") <= right.get("title","")) else right

def _collapse_near_dupes(items: List[Dict[str, Any]], time_window_days: int = 3, jaccard_threshold: float = 0.8) -> List[Dict[str, Any]]:
    """
    Collapse items that have highly similar titles within a short publication window.
    Keep the 'better' one using _choose_better.
    """
    if not items:
        return items

    # Sort newest first to make selection deterministic
    items_sorted = sorted(items, key=lambda it: (
        _parse_utc(it.get("publishedUtc","")),
        _score(it),
        it.get("title","").lower()
    ), reverse=True)

    kept: List[Dict[str, Any]] = []
    buckets: List[Tuple[Set[str], datetime, Dict[str, Any]]] = []
    window = timedelta(days=time_window_days)

    for it in items_sorted:
        tkns = _tokens(it.get("title",""))
        ts = _parse_utc(it.get("publishedUtc",""))
        matched_idx = -1
        best_j = 0.0

        # Try match to an existing bucket
        for idx, (btkns, bts, best_item) in enumerate(buckets):
            if abs(ts - bts) > window:
                continue
            j = _jaccard(tkns, btkns)
            if j >= jaccard_threshold and j > best_j:
                matched_idx, best_j = idx, j

        if matched_idx == -1:
            # new bucket
            buckets.append((tkns, ts, it))
            kept.append(it)
        else:
            # compete with bucket winner; update if this one is better
            btkns, bts, best_item = buckets[matched_idx]
            winner = _choose_better(best_item, it)
            if winner is it:
                buckets[matched_idx] = (tkns, ts, it)
                # replace in kept
                kept.remove(best_item)
                kept.append(it)

    # keep order newest->oldest
    kept.sort(key=lambda it: (
        _parse_utc(it.get("publishedUtc","")),
        _score(it),
        it.get("title","").lower()
    ), reverse=True)
    return kept

# --- Main policy -------------------------------------------------------------
def apply_policy(items: List[Dict[str, Any]], team_code: str = "ARS", exclude_women: bool = True) -> List[Dict[str, Any]]:
    """
    Pipeline:
    - canonicalize provider + infer type (official/fan)
    - early women/youth filter (if configured)
    - relevance filter (Arsenal-only) for general outlets; fan Arsenal sites always pass
    - dedupe by URL/title (strict)
    - cross-provider near-duplicate collapse (soft, title-similarity + time window)
    - per-provider caps
    - sort by publishedUtc desc, tie-break on score then title
    """
    # 1) Canonicalize + early filters + strict dedupe
    norm: List[Dict[str, Any]] = []
    seen_url: Set[str] = set()
    seen_title: Set[str] = set()

    for it in items:
        it["provider"] = canonicalize_provider(it.get("provider", ""))
        it["type"] = "official" if it["provider"] in OFFICIAL_SET else "fan"

        if exclude_women and _is_women_or_youth(it):
            continue

        if not _is_relevant_to_arsenal(it):
            continue

        url_key = (it.get("url") or "").strip().lower()
        title_key = (it.get("title") or "").strip().lower()
        if not url_key or not title_key:
            continue
        if url_key in seen_url or title_key in seen_title:
            continue
        seen_url.add(url_key)
        seen_title.add(title_key)

        norm.append(it)

    # 2) Cross-provider near-duplicate collapse (before caps)
    collapsed = _collapse_near_dupes(norm)

    # 3) Per-provider caps
    per_provider_count: Dict[str, int] = {}
    capped: List[Dict[str, Any]] = []
    for it in collapsed:
        prov = it["provider"]
        cap = PROVIDER_CAPS.get(prov, 999)
        cnt = per_provider_count.get(prov, 0)
        if cnt >= cap:
            continue
        per_provider_count[prov] = cnt + 1
        capped.append(it)

    # 4) Sort (descending publishedUtc, then score, then title)
    def key_fn(it: Dict[str, Any]):
        return (
            it.get("publishedUtc", ""),
            _score(it),
            it.get("title", "").lower()
        )
    capped.sort(key=key_fn, reverse=True)
    return capped

