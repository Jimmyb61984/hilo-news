from __future__ import annotations
from typing import List, Dict, Any, Optional
from collections import defaultdict
from datetime import datetime, timezone, timedelta
import hashlib
import re
from urllib.parse import urlparse  # <-- added

from app.policy_near_dupes import collapse_near_dupes  # near-duplicate collapse

# --- Provider normalization ---------------------------------------------------
_CANON = {
    "arsenalinsider.com": "ArsenalInsider",
    "paininthearsenal.com": "PainInTheArsenal",
    "arseblog.com": "Arseblog",
    "standard.co.uk": "EveningStandard",
    "dailymail.co.uk": "DailyMail",
    # legacy keys in case provider field already canonicalized
    "ArsenalInsider": "ArsenalInsider",
    "PainInTheArsenal": "PainInTheArsenal",
    "Arseblog": "Arseblog",
    "EveningStandard": "EveningStandard",
    "DailyMail": "DailyMail",
}
def canonicalize_provider(p: str) -> str:
    if not p:
        return "Unknown"
    key = p.strip().lower().replace("www.", "")
    return _CANON.get(key, p.strip())

# Only these providers are allowed into the feed
_ALLOWED_PROVIDERS = {
    "EveningStandard",
    "DailyMail",
    "Arseblog",
    "PainInTheArsenal",
    "ArsenalInsider",
}

# --- Women/U19 filter (U18 & U21 are allowed) --------------------------------
_WOMEN_U19_KEYS = [
    "women", "wsl", "fa wsl", "wfc",
    "u19", "under-19", "under 19"
]
def _is_women_or_u19(txt: str) -> bool:
    t = (txt or "").lower()
    return any(k in t for k in _WOMEN_U19_KEYS)

# --- Arsenal relevance for official press (DM/ES) -----------------------------
_ARS_KEYWORDS = {
    "arsenal", "gunners", "arteta",
    "odegaard", "saka", "saliba", "trossard", "rice",
    "white", "havertz", "jesus", "raya", "eze", "mosquera",
    "emirates stadium", "north london derby"
}
_ARS_RE = re.compile(r"\barsenal\b", re.IGNORECASE)

def _text_has_arsenal(text: str) -> bool:
    t = (text or "").lower()
    if "arsenal" in t or "gunners" in t:
        return True
    for k in _ARS_KEYWORDS:
        if k in t:
            return True
    return False

def _is_about_arsenal(item: Dict[str, Any]) -> bool:
    """
    True if clearly Arsenal-specific:
    - title/summary mention Arsenal (or related keywords), OR
    - URL is inside the Arsenal section for Daily Mail / Evening Standard.
    """
    title = item.get("title") or ""
    summary = (item.get("summary") or "") + " " + (item.get("snippet") or "")
    url = item.get("url") or ""

    # Text signals
    if _text_has_arsenal(title): 
        return True
    if _text_has_arsenal(summary): 
        return True

    # URL section signals (handles cases where headline doesn't say "Arsenal")
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower().replace("www.", "")
        path = (parsed.path or "").lower()
    except Exception:
        host, path = "", ""

    # Daily Mail typical: /sport/football/arsenal/
    if "dailymail.co.uk" in host and "/sport/football/arsenal" in path:
        return True
    # Evening Standard typical: /sport/football/arsenal
    if "standard.co.uk" in host and "/sport/football/arsenal" in path:
        return True

    # Fallback: explicit "arsenal" in URL string
    if _ARS_RE.search(url):
        return True

    return False

# --- Utility ------------------------------------------------------------------
def _iso(dt: Optional[str]) -> str:
    return (dt or "1970-01-01T00:00:00Z")

def _parse_dt(dt_str: Optional[str]) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        from dateutil import parser as du
        return du.parse(dt_str).astimezone(timezone.utc)
    except Exception:
        return None

def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for it in items:
        u = (it.get("url") or "").strip().lower()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(it)
    return out

def _score(it: Dict[str, Any]) -> int:
    # Simple score: official providers get a small boost; image gets a bump
    prov = canonicalize_provider(it.get("provider", ""))
    official_boost = 10 if prov in {"EveningStandard", "DailyMail"} else 0
    has_img = 1 if it.get("imageUrl") else 0
    return official_boost * 100 + has_img

# --- Declump: stagger same-minute items to avoid bunched look -----------------
def _declump_same_minute(items: List[Dict[str, Any]]) -> None:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for it in items:
        pu = it.get("publishedUtc")
        dt = _parse_dt(pu)
        if not dt:
            continue
        minute_key = f"{canonicalize_provider(it.get('provider',''))}|{dt.strftime('%Y-%m-%dT%H:%M')}"
        buckets[minute_key].append(it)
    for _, group in buckets.items():
        if len(group) <= 1:
            continue
        for it in group:
            url = (it.get("url") or "").encode("utf-8")
            h = hashlib.sha1(url).hexdigest()
            offset = int(h[:2], 16) % 30  # 0..29 seconds
            base_dt = _parse_dt(it.get("publishedUtc"))
            if base_dt:
                it["publishedUtc"] = _to_utc_iso(base_dt.replace(second=0, microsecond=0) + timedelta(seconds=offset))

# --- Kind classification (pre, post, live, howto, gallery) -------------------
_PRE_PATTERNS = [
    r"\bpreview\b",
    r"\bpredicted\s*line[- ]?up\b", r"\bline[- ]?up(s)?\b", r"\bprobable\s*xi\b",
    r"\bteam news\b", r"\bhow .* could line up\b", r"\bthree ways .* could line up\b",
    r"\btalking points\b", r"\bkeys to\b", r"\bwhat to expect\b",
]
_POST_PATTERNS = [
    r"\bmatch report\b", r"\bplayer ratings\b", r"\bpost[- ]?match\b",
    r"\bwhat we learned\b", r"\btakeaways\b", r"\breaction\b", r"\banalysis\b",
]
_LIVE_PATTERNS = [r"\blive blog\b", r"\bliveblog\b", r"\bas it happened\b", r"/live[-/]"]
_HOWTO_PATTERNS = [r"\bhow to watch\b", r"\bwhat channel\b", r"\btv channel\b", r"\blive stream\b", r"\bstream\b"]
_GALLERY_PATTERNS = [r"\bgallery\b", r"\bin pictures\b", r"\bphotos:\b", r"/gallery/"]

def _matches_any(patterns: List[str], text: str) -> bool:
    t = (text or "").lower()
    for p in patterns:
        if re.search(p, t, re.IGNORECASE):
            return True
    return False

def _classify_kind(it: Dict[str, Any]) -> Optional[str]:
    s = " ".join([
        it.get("title") or "",
        it.get("summary") or "",
        it.get("url") or "",
    ])
    if _matches_any(_LIVE_PATTERNS, s):
        return "liveblog"
    if _matches_any(_HOWTO_PATTERNS, s):
        return "howto"
    if _matches_any(_GALLERY_PATTERNS, s):
        return "gallery"
    if _matches_any(_POST_PATTERNS, s):
        return "postmatch"
    if _matches_any(_PRE_PATTERNS, s):
        return "prematch"
    return None

# --- Summary polish -----------------------------------------------------------
def _polish_summary(it: Dict[str, Any]) -> None:
    """If summary is empty/very short, create a clean teaser from title."""
    summary = (it.get("summary") or "").strip()
    title = (it.get("title") or "").strip()
    if len(summary) >= 40:
        return
    teaser = title
    # limit to ~140 chars without cutting words
    if len(teaser) > 140:
        cut = teaser[:140]
        cut = cut[:cut.rfind(" ")] if " " in cut else cut
        teaser = cut + "…"
    it["summary"] = teaser

# --- CORE POLICY --------------------------------------------------------------
def apply_policy_core(items: List[Dict[str, Any]], team_code: str = "ARS", exclude_women: bool = True) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for it in items:
        title = it.get("title") or ""
        summary = it.get("summary") or ""
        prov = canonicalize_provider(it.get("provider", ""))

        # Allow-list
        if prov not in _ALLOWED_PROVIDERS:
            continue

        # Women/WSL and U19 exclusion — U18/U21/Academy remain allowed
        if exclude_women and (_is_women_or_u19(title) or _is_women_or_u19(summary)):
            continue

        # Arsenal relevance for official press (tightened)
        if prov in {"DailyMail", "EveningStandard"} and not _is_about_arsenal(it):
            continue

        # Classify and enforce editorial rules
        kind = _classify_kind(it)

        # Ban: live blogs, how-to-watch, galleries
        if kind in {"liveblog", "howto", "gallery"}:
            continue

        # Pre-match: EveningStandard only
        if kind == "prematch" and prov != "EveningStandard":
            continue

        # Post-match: PainInTheArsenal only
        if kind == "postmatch" and prov != "PainInTheArsenal":
            continue

        # Summary polish last (safe, display-only)
        _polish_summary(it)

        filtered.append(it)

    # Exact-URL dedupe
    filtered = _dedupe(filtered)

    # Cross-provider near-duplicate collapse (previews/reports)
    filtered = collapse_near_dupes(filtered)

    # Declump for stable ordering
    _declump_same_minute(filtered)

    # Stable sort
    def _tie_key(x: Dict[str, Any]):
        return (
            _iso(x.get("publishedUtc")),
            _score(x),
            (x.get("title") or "").lower(),
            (x.get("id") or x.get("url") or "").lower()
        )
    filtered.sort(key=_tie_key, reverse=True)
    return filtered

# --- PER-PAGE CAPS ------------------------------------------------------------
_PROVIDER_CAPS_DEFAULT = {
    "EveningStandard": 4,
    "DailyMail": 4,
    "Arseblog": 3,
    "PainInTheArsenal": 3,
    "ArsenalInsider": 3,
}

def _fill_with_limit(sorted_items: List[Dict[str, Any]],
                     start_index: int,
                     page_size: int,
                     counts: Dict[str, int],
                     limit_for: Dict[str, int],
                     selected_idx: set) -> List[int]:
    chosen = []
    i = start_index
    n = len(sorted_items)
    while len(chosen) + len(selected_idx) < page_size and i < n:
        if i in selected_idx:
            i += 1
            continue
        it = sorted_items[i]
        prov = canonicalize_provider(it.get("provider", ""))
        limit = limit_for.get(prov, 2)
        if counts.get(prov, 0) < limit:
            chosen.append(i)
            counts[prov] = counts.get(prov, 0) + 1
        i += 1
    return chosen

def page_with_caps(sorted_items: List[Dict[str, Any]],
                   page: int,
                   page_size: int,
                   caps: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    caps = {**_PROVIDER_CAPS_DEFAULT, **(caps or {})}
    start_index = max(0, (page - 1) * page_size)
    n = len(sorted_items)
    if start_index >= n:
        return []
    counts: Dict[str, int] = defaultdict(int)
    selected_idx: set = set()

    # Pass 1 — strict caps
    p1_idx = _fill_with_limit(sorted_items, start_index, page_size, counts, caps, selected_idx)
    selected_idx.update(p1_idx)
    if len(selected_idx) >= page_size:
        return [sorted_items[i] for i in sorted(selected_idx)][:page_size]

    # Pass 2 — cap + 1
    soft1 = {k: v + 1 for k, v in caps.items()}
    p2_idx = _fill_with_limit(sorted_items, start_index, page_size, counts, soft1, selected_idx)
    selected_idx.update(p2_idx)
    if len(selected_idx) >= page_size:
        return [sorted_items[i] for i in sorted(selected_idx)][:page_size]

    # Pass 3 — cap + 2
    soft2 = {k: v + 2 for k, v in caps.items()}
    p3_idx = _fill_with_limit(sorted_items, start_index, page_size, counts, soft2, selected_idx)
    selected_idx.update(p3_idx)
    if len(selected_idx) >= page_size:
        return [sorted_items[i] for i in sorted(selected_idx)][:page_size]

    # Final unconditional top-up
    i = start_index
    while len(selected_idx) < page_size and i < n:
        if i not in selected_idx:
            selected_idx.add(i)
        i += 1

    return [sorted_items[i] for i in sorted(selected_idx)][:page_size]
