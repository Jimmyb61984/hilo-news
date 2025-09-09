# app/config.py

# Feeds mapped by team code.
# We support BOTH "ARS" and "Ars" so the backend works no matter which the app sends.

TEAM_FEEDS = {
    "ARS": {
        "A": [
            {"provider": "bbc_sport", "kind": "publisher", "section": "arsenal"},
            {"provider": "sky_sports", "kind": "publisher", "section": "arsenal"},
            {"provider": "arsenal_official", "kind": "club", "section": "news"}
        ],
        "B": [
            {"provider": "evening_standard", "kind": "publisher", "section": "arsenal"},
            {"provider": "the_times", "kind": "publisher", "section": "football"}
        ],
        "C": [
            {"provider": "arseblog", "kind": "fan", "section": "feed"},
            {"provider": "paininthearsenal", "kind": "fan", "section": "feed"}
        ]
    },
    "Ars": {  # identical to ARS to match your Unity code
        "A": [
            {"provider": "bbc_sport", "kind": "publisher", "section": "arsenal"},
            {"provider": "sky_sports", "kind": "publisher", "section": "arsenal"},
            {"provider": "arsenal_official", "kind": "club", "section": "news"}
        ],
        "B": [
            {"provider": "evening_standard", "kind": "publisher", "section": "arsenal"},
            {"provider": "the_times", "kind": "publisher", "section": "football"}
        ],
        "C": [
            {"provider": "arseblog", "kind": "fan", "section": "feed"},
            {"provider": "paininthearsenal", "kind": "fan", "section": "feed"}
        ]
    }
}

# Simple tier weights (unused for now but kept for future ranking)
TIER_WEIGHTS = {"A": 1.0, "B": 0.8, "C": 0.5}

# Pagination and cache
PAGE_SIZE_MAX = 100
CACHE_TTL_SECONDS = 180  # 3 minutes
