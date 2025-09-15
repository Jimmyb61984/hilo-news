from __future__ import annotations
from typing import List, Dict, Any, Optional
from collections import defaultdict

# --- Provider normalization ---------------------------------------------------
_CANON = {
    "arsenal.com": "ArsenalOfficial",
    "arsenalofficial": "ArsenalOfficial",
    "arsenalinsider.com": "ArsenalInsider",
    "paininthearsenal.com": "PainInTheArsenal",
    "arseblog.com": "Arseblog",
    "standard.co.uk": "EveningStandard",
    "dailymail.co.uk": "DailyMail",
    "skysports.com": "SkySports",
}
def canonicalize_provider(p: str) -> str:
    if not p:
        return "Unknown"
    key = p.strip().lower().replace("www.", "")
    return _CANON.get(key, p.strip())

# --- Women/Youth filter -------------------------------------------------------
_WOMEN_YOUTH_KEYS = [
    "women", "wsl", "fa wsl", "wfc", "academy", "u21", "u18",
    "under-21", "under 21", "under-18", "under 18", "youth"
]
def _is_women_youth(txt: str) -> bool:
    t = (txt or "").lower()
    return any(k in t for k in _WOMEN_YOUTH_KEYS)

# --- Relevance (ARS) ----------------------------------------------------------
_ARS_RELEVANCE_KEYS = [
    "arsenal", "gunners", "emirates", "arteta", "odegaard", "saka",
    "saliba", "trossard", "declan rice", "gunnersaurus"
]

_OFFICIALS = {"ArsenalOfficial", "EveningStandard", "DailyMail", "SkySports"}

def _iso(dt: str) -> str:
    if not dt:
        return "1970-01-01T00:00:00Z"
    return dt

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
    prov = canonicalize_provider(it.get("provider", ""))
    base = 1000 if prov in _OFFICIALS else 100
    has_img = 10 if it.get("imageUrl") else 0
    return base + has_img

# --- CORE POLICY (filters only: women/youth, relevance, dedupe, sort) --------
def apply_policy_core(items: List[Dict[str, Any]], team_code: str = "ARS", exclude_women: bool = True) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for it in items:
        title = it.get("title") or ""
        summary = it.get("summary") or ""
        prov = canonicalize_provider(it.get("provider", ""))

        if exclude_women and (_is_women_youth(title) or _is_women_youth(summary)):
            continue

        text = f"{title} {summary}".lower()
        if team_code == "ARS":
            if prov == "ArsenalOfficial" or "arsenal" in text or any(k in text for k in _ARS_RELEVANCE_KEYS):
                filtered.append(it)
            else:
                # keep core Arsenal blogs even if headline omits "Arsenal"
                if prov in {"Arseblog", "PainInTheArsenal", "ArsenalInsider"}:
                    filtered.append(it)
        else:
            filtered.append(it)

    filtered = _dedupe(filtered)
    filtered.sort(
        key=lambda x: (_iso(x.get("publishedUtc")), _score(x), (x.get("title") or "").lower()),
        reverse=True
    )
    return filtered

# --- PER-PAGE CAPS (applied only when composing a page) -----------------------
_PROVIDER_CAPS_DEFAULT = {
    "ArsenalOfficial": 6,
    "EveningStandard": 2,
    "DailyMail": 2,
    "SkySports": 2,
    "Arseblog": 3,
    "PainInTheArsenal": 3,
    "ArsenalInsider": 3,
}

def page_with_caps(sorted_items: List[Dict[str, Any]],
                   page: int,
                   page_size: int,
                   caps: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    """
    Compose a page from the globally sorted list:
      1) First pass: respect per-provider caps to ensure variety.
      2) Elastic overfill: if underfilled and inventory exists, fill the rest
         ignoring caps (but never duplicating) until page_size or exhaustion.
    """
    caps = {**_PROVIDER_CAPS_DEFAULT, **(caps or {})}

    start_index = max(0, (page - 1) * page_size)
    n = len(sorted_items)
    if start_index >= n:
        return []

    selected_idx = set()
    counts = defaultdict(int)
    out: List[Dict[str, Any]] = []

    # First pass — variety with caps
    i = start_index
    while len(out) < page_size and i < n:
        it = sorted_items[i]
        prov = canonicalize_provider(it.get("provider", ""))
        if counts[prov] < caps.get(prov, 2):
            out.append(it)
            counts[prov] += 1
            selected_idx.add(i)
        i += 1

    # Elastic overfill — ignore caps, keep order, avoid duplicates
    if len(out) < page_size:
        j = start_index
        while len(out) < page_size and j < n:
            if j not in selected_idx:
                out.append(sorted_items[j])
                selected_idx.add(j)
            j += 1

    return out

