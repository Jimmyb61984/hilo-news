# app/config.py

TEAM_FEEDS = {
    "Ars": {
        "A": [
            {"provider": "bbc_sport", "kind": "publisher", "section": "arsenal"},
            {"provider": "sky_sports", "kind": "publisher", "section": "arsenal"},
            {"provider": "arsenal_official", "kind": "club", "section": "news"},
        ],
        "B": [
            {"provider": "evening_standard", "kind": "publisher", "section": "arsenal"},
            {"provider": "the_times", "kind": "publisher", "section": "football"},
        ],
        "C": [
            {"provider": "arseblog", "kind": "fan", "section": "feed"},
            {"provider": "paininthearsenal", "kind": "fan", "section": "feed"},
            # --- Pending mapping in sources.py ---
            # {"provider": "arsenalinsider", "kind": "fan", "section": "feed"},
            # ↑ Enable this as soon as sources.py defines "arsenalinsider".
        ],
    }
}

# Allow both "Ars" and "ARS" to resolve the same config (helps clients that
# send either variant).
TEAM_FEEDS["ARS"] = TEAM_FEEDS["Ars"]

TIER_WEIGHTS = {"A": 1.0, "B": 0.8, "C": 0.5}
PAGE_SIZE_MAX = 100
CACHE_TTL_SECONDS = 180  # 3 minutes

