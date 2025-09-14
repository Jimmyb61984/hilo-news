from __future__ import annotations
from typing import List, Dict, Any, Set
from urllib.parse import urlparse

# --- Canonical provider names ------------------------------------------------
PROVIDER_ALIASES = {
    "arsenal.com": "ArsenalOfficial",
    "www.arsenal.com": "ArsenalOfficial",
    "arseblog.com": "Arseblog",
    "www.arseblog.com": "Arseblog",
    "paininthearsenal.com": "PainInTheArsenal",
    "www.paininthearsenal.com": "PainInTheArsenal",
    "www.dailymail.co.uk": "DailyMail",
    "dailymail.co.uk": "DailyMail",
    "www.standard.co.uk": "EveningStandard",
    "standard.co.uk": "EveningStandard",
    "www.skysports.com": "SkySports",
    "skysports.com": "SkySports",
    "www.thetimes.co.uk": "TheTimes",
    "thetimes.co.uk": "TheTimes",
}

OFFICIAL_SET: Set[str] = {"ArsenalOfficial", "SkySports", "DailyMail", "EveningStandard", "TheTimes"}

# Caps to prevent dominance (applied before pagination)
PROVIDER_CAPS: Dict[str, int] = {
    "Arseblog": 3,
    "PainInTheArsenal": 3,
    # other fan caps can be added here
}

# Women / Youth / Academy filters (apply EARLY)
WOMEN_YOUTH_KEYWORDS: List[str] = [
    "women", "wsl", "academy", "u21", "u20", "u18", "u17", "youth", "girls", "development squad"
]

def canonicalize_provider(provider_or_url: str) -> str:
    """
    Map hostnames or shorthand to canonical provider names.
    Accepts either a hostname/URL or already-canonical name.
    """
    if provider_or_url in PROVIDER_ALIASES.values():
        return provider_or_url
    try:
        host = urlparse(provider_or_url).netloc or provider_or_url
        # if a full URL was passed, urlparse(...).netloc resolves the host; else leave the string
        if host in PROVIDER_ALIASES:
            return PROVIDER_ALIASES[host]
    except Exception:
        pass
    # bare key fallback (e.g., "Arseblog")
    return PROVIDER_ALIASES.get(provider_or_url, provider_or_url)

def _is_women_or_youth(item: Dict[str, Any]) -> bool:
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    return any(k in text for k in WOMEN_YOUTH_KEYWORDS)

def _score(item: Dict[str, Any]) -> float:
    # Quality weighting baseline: official higher
    base = 1.0
    if item.get("provider") in OFFICIAL_SET:
        base += 1.0
    # Small bump if image present (helps Panel1 eligibility)
    if item.get("imageUrl"):
        base += 0.2
    return base

def apply_policy(items: List[Dict[str, Any]], team_code: str = "ARS", exclude_women: bool = True) -> List[Dict[str, Any]]:
    """
    Pipeline:
    - canonicalize provider + infer type (official/fan)
    - early women/youth filter
    - dedupe by URL/title
    - per-provider caps
    - sort by publishedUtc desc, tie-break on score then title
    """
    # 1) Canonicalize + early filters + dedupe
    norm: List[Dict[str, Any]] = []
    seen_url: Set[str] = set()
    seen_title: Set[str] = set()

    for it in items:
        it["provider"] = canonicalize_provider(it.get("provider", ""))
        it["type"] = "official" if it["provider"] in OFFICIAL_SET else "fan"

        if exclude_women and _is_women_or_youth(it):
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

    # 2) Per-provider caps before final sort/pagination
    per_provider_count: Dict[str, int] = {}
    capped: List[Dict[str, Any]] = []
    for it in norm:
        prov = it["provider"]
        cap = PROVIDER_CAPS.get(prov, 999)
        cnt = per_provider_count.get(prov, 0)
        if cnt >= cap:
            continue
        per_provider_count[prov] = cnt + 1
        capped.append(it)

    # 3) Sort (descending)
    def key_fn(it: Dict[str, Any]):
        return (
            it.get("publishedUtc", ""),
            _score(it),
            it.get("title", "").lower()
        )
    capped.sort(key=key_fn, reverse=True)

    return capped

