# app/config.py

# Arsenal team configuration (both 'Ars' and 'ARS' map here)
TEAM_FEEDS = {
    "Ars": {
        # Tier A — official / high-trust
        "A": [
            {"provider": "bbc_sport", "kind": "publisher", "section": "arsenal"},
            {"provider": "arsenal_official", "kind": "club", "section": "news"},
            # Sky Sports kept defined but disabled in sources.py (HTML page)
            {"provider": "sky_sports", "kind": "publisher", "section": "arsenal"},
        ],
        # Tier B — publishers we may enable later via HTML parsing or confirmed RSS
        "B": [
            {"provider": "evening_standard", "kind": "publisher", "section": "arsenal"},
            {"provider": "daily_mail", "kind": "publisher", "section": "arsenal"},
            {"provider": "the_times", "kind": "publisher", "section": "arsenal"},
        ],
        # Tier C — fan sites (text-only thumbnails policy in fetcher)
        "C": [
            {"provider": "arseblog", "kind": "fan", "section": "feed"},
            {"provider": "paininthearsenal", "kind": "fan", "section": "feed"},
            {"provider": "arsenalinsider", "kind": "fan", "section": "feed"},
        ],
    }
}

# Alias for uppercase code used by your client
TEAM_FEEDS["ARS"] = TEAM_FEEDS["Ars"]

# Weights & paging
TIER_WEIGHTS = {"A": 1.0, "B": 0.8, "C": 0.5}
PAGE_SIZE_MAX = 100
CACHE_TTL_SECONDS = 180  # 3 minutes
