from __future__ import annotations
from typing import List, Dict, Any, Set
from urllib.parse import urlparse
import re

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

    # Fan
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
    re.compile(r"\bgabriel\b", re.I),       # (CB) note: generic name, used with care below
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
    # examples observed in live feeds that slipped through:
    "sam kerr", "chelsea women", "barclays wsl"
]

def _is_women_or_youth(item: Dict[str, Any]) -> bool:
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    return any(k in text for k in WOMEN_YOUTH_KEYWORDS)

# --- Per-provider caps (apply BEFORE pagination) -----------------------------
# Caps tuned to force a healthy mix on page 1 (reduce dominance).
PROVIDER_CAPS: Dict[str, int] = {
    # Fan sites
    "Arseblog": 3,
    "PainInTheArsenal": 3,
    "ArsenalInsider": 3,

    # Tier-1/general outlets
    "DailyMail": 2,
    "SkySports": 2,
    "EveningStandard": 2,
    "TheTimes": 2,

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

def _score(item: Dict[str, Any]) -> float:
    # Quality weighting baseline: official gets a bump
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
    - early women/youth filter (if configured)
    - relevance filter (Arsenal-only) for general outlets; fan Arsenal sites always pass
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

        # Early women/youth filter
        if exclude_women and _is_women_or_youth(it):
            continue

        # Arsenal relevance (drop unrelated stories from general feeds)
        if not _is_relevant_to_arsenal(it):
            continue

        # Dedupe by URL and title
        url_key = (it.get("url") or "").strip().lower()
        title_key = (it.get("title") or "").strip().lower()
        if not url_key or not title_key:
            continue
        if url_key in seen_url or title_key in seen_title:
            continue
        seen_url.add(url_key)
        seen_title.add(title_key)

        norm.append(it)

    # 2) Per-provider caps (BEFORE final sort/pagination)
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

    # 3) Sort (descending publishedUtc, then score, then title)
    def key_fn(it: Dict[str, Any]):
        return (
            it.get("publishedUtc", ""),
            _score(it),
            it.get("title", "").lower()
        )

    capped.sort(key=key_fn, reverse=True)
    return capped

