from typing import Optional

"""
sources.py
- Maps provider slugs (from config.py) to actual feed URLs.
- 'rss' providers return concrete URLs.
- 'html' providers are placeholders (disabled until we add safe parsing).
"""

PROVIDERS = {
    # ============== TRUSTED (THUMBNAILS ALLOWED) ==============
    "bbc_sport": {
        "type": "rss",
        "url": "https://feeds.bbci.co.uk/sport/football/teams/arsenal/rss.xml",
        "builder": "bbc_team_feed",  # kept for future multi-team use
        "notes": "BBC Sport team RSS (Arsenal default static)."
    },
    "arsenal_official": {
        "type": "rss",
        "url": "https://www.arsenal.com/news/rss",
        "notes": "Official Arsenal.com news RSS."
    },

    # ============== FAN SITES (text-only policy) ==============
    "arseblog": {
        "type": "rss",
        "url": "https://arseblog.com/feed/",
        "notes": "Arsenal fan site RSS."
    },
    "paininthearsenal": {
        "type": "rss",
        "url": "https://paininthearsenal.com/feed/",
        "notes": "FanSided Arsenal site RSS."
    },
    "arsenalinsider": {
        "type": "rss",
        "url": "https://arsenalinsider.com/feed/",
        "notes": "Independent Arsenal fan site RSS."
    },

    # ============== PLACEHOLDERS (DISABLED) ==============
    "sky_sports": {
        "type": "html",
        "url": "https://www.skysports.com/arsenal",
        "notes": "HTML; disabled until extractor is added."
    },
    "evening_standard": {
        "type": "html",
        "url": "https://www.standard.co.uk/sport/football/arsenal",
        "notes": "HTML; disabled until extractor is added."
    },
    "daily_mail": {
        "type": "html",
        "url": "https://www.dailymail.co.uk/sport/teampages/arsenal.html",
        "notes": "HTML; disabled until extractor is added."
    },
    "the_times": {
        "type": "html",
        "url": "https://www.thetimes.co.uk/sport/football/teams/arsenal",
        "notes": "HTML; disabled until extractor is added."
    },
}

def bbc_team_feed(section: Optional[str], team_code: Optional[str]) -> Optional[str]:
    """Builder kept for future multi-team use."""
    team_slug = None
    if (section or "").lower() == "arsenal" or (team_code or "").upper() == "ARS":
        team_slug = "arsenal"
    if not team_slug:
        return None
    return f"https://feeds.bbci.co.uk/sport/football/teams/{team_slug}/rss.xml"

def build_feed_url(provider: str, section: Optional[str] = None, team_code: Optional[str] = None) -> Optional[str]:
    """
    Resolve a provider to a concrete URL (or None if disabled/HTML).
    """
    meta = PROVIDERS.get(provider)
    if not meta:
        return None
    if meta.get("type") == "rss":
        if meta.get("url"):
            return meta["url"]
        if meta.get("builder") == "bbc_team_feed":
            return bbc_team_feed(section, team_code)
        return None
    # HTML providers disabled by design
    return None

