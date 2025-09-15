from __future__ import annotations
from typing import List, Dict, Any, Optional
from collections import defaultdict

from app.policy_near_dupes import collapse_near_dupes  # NEW: near-duplicate collapse

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

# --- Women/U19 filter (U18 & U21 are allowed) --------------------------------
_WOMEN_U19_KEYS = [
    "women", "wsl", "fa wsl", "wfc",
    "u19", "under-19", "under 19"
]
def _is_women_or_u19(txt: str) -> bool:
    t = (txt or "").lower()
    return any(k in t for k in _WOMEN_U19_KEYS)

# --- Relevance (ARS) ----------------------------------------------------------
_ARS_RELEVANCE_KEYS = [
    "arsenal", "gunners", "emirates", "arteta", "odegaard", "saka",
    "saliba", "trossard", "declan rice", "gunnersaurus"
]

_OFFICIALS = {"ArsenalOfficial", "EveningStandard", "DailyMail", "SkySports"}

def _iso(dt: str) -> str:
    return dt or "1970-01-01T00:00:00Z"

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

# --- CORE POLICY (filters only: Women/WSL + U19, relevance, dedupe, sort) ----
def apply_policy_core(items: List[Dict[str, Any]], team_code: str = "ARS", exclude_women: bool = True) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for it in items:
        title = it.get("title") or ""
        summary = it.get("summary") or ""
        prov = canonicalize_provider(it.get("provider", ""))

        # Only exclude Women/WSL and U19 — U18/U21/Academy remain allowed
        if exclude_women and (_is_women_or_u19(title) or _is_women_or_u19(summary)):
            continue

        text = f"{title} {summary}".lower()
        if team_code == "ARS":
            if prov == "ArsenalOfficial" or "arsenal" in text or any(k in text for k in _ARS_RELEVANCE_KEYS):
                filtered.append(it)
            else:
                if prov in {"Arseblog", "PainInTheArsenal", "ArsenalInsider"}:
                    filtered.append(it)
        else:
            filtered.append(it)

    # Exact-URL dedupe
    filtered = _dedupe(filtered)

    # NEW: Cross-provider near-duplicate collapse (previews/reports), BEFORE sort/caps
    filtered = collapse_near_dupes(filtered)

    # Stable sort: primary = publishedUtc desc; then score; then title asc; then id/url asc
    # (tie-breakers ensure deterministic order when times are equal)
    def _tie_key(x: Dict[str, Any]):
        return (
            _iso(x.get("publishedUtc")),
            _score(x),
            (x.get("title") or "").lower(),
            (x.get("id") or x.get("url") or "").lower()
        )

    filtered.sort(key=_tie_key, reverse=True)
    return filtered

# --- PER-PAGE CAPS (with soft overfill) --------------------------------------
_PROVIDER_CAPS_DEFAULT = {
    "ArsenalOfficial": 6,
    "EveningStandard": 2,
    "DailyMail": 2,
    "SkySports": 2,
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
    """Return list of indexes selected under per-provider limits."""
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
    """
    Compose a page:
      Pass 1: strict caps
      Pass 2: soft caps (cap + 1)
      Pass 3: softer caps (cap + 2)
      Final: minimal unconditional fill to hit page_size if still short
    """
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

    # Final tiny top-up — ignore caps but preserve order, only if still short
    i = start_index
    while len(selected_idx) < page_size and i < n:
        if i not in selected_idx:
            selected_idx.add(i)
        i += 1

    return [sorted_items[i] for i in sorted(selected_idx)][:page_size]

