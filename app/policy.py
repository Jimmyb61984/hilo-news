from __future__ import annotations
from typing import List, Dict, Any, Set
from urllib.parse import urlparse

# --- Canonical provider names ------------------------------------------------
PROVIDER_ALIASES = {
    "arsenal.com": "ArsenalOfficial",
    "www.arsenal.com": "ArsenalOfficial",
    "arseblog.com": "Arseblog",
    "paininthearsenal.com": "PainInTheArsenal",
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

# Caps to prevent dominance (per *page*, but we apply before pagination to simplify)
PROVIDER_CAPS = {
    "Arseblog": 3,
    "PainInTheArsenal": 3,
    # Other fan sites may be added here as needed
}

# Women / Youth / Academy filters (apply EARLY)
WOMEN_YOUTH_KEYWORDS = [
    "women", "wsl", "academy", "u21", "u20", "u18", "u17", "youth", "girls", "development squad"
]

def canonicalize_provider(provider_or_url: str) -> str:
    if provider_or_url in PROVIDER_ALIASES.values():
        return provider_or_url
    try:
        host = urlparse(provider_or_url).netloc
        if host in PROVIDER_ALIASES:
            return PROVIDER_ALIASES[host]
    except Exception:
        pass
    # bare key fallback (e.g., "Arseblog")
    return PROVIDER_ALIASES.get(provider_or_url, provide_

