from typing import Optional

"""
sources.py
- Maps provider slugs to actual feed URLs and types.
- 'rss' providers: return concrete URLs (or via builder).
- 'html' providers: enabled with safe headline extraction (titles+links only).
"""

PROVIDERS = {
    # ============== TRUSTED (THUMBNAILS ALLOWED IF FEED PROVIDES) ==============
    "bbc_sport": {
        "type": "rss",
        "url": "https://feeds.bbci.co.uk/sport/football/teams/arsenal/rss.xml",
        "builder": "bbc_team_feed",
        "notes": "BBC Sport team RSS (Arsenal default static)."
    },

    # ============== CLUB (HTML headlines only) ==============
    "arsenal_official": {
        "type": "html",
        "url": "https://www.arsenal.com/news",
        "notes": "Official site; no public RSS. Headlines/links only."
    },

    # ============== FAN SITES (RSS) ==============
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

    # ============== PUBLISHERS (HTML headlines only) ==============
    "sky_sports": {
        "type": "html",
        "url": "https://www.skysports.com/arsenal",
        "notes": "Team page; headlines/links only."
    },
    "evening_standard": {
        "type": "html",
        "url": "https://www.standard.co.uk/sport/football/arsenal",
        "notes": "Tag page; headlines/links only."
    },
    "daily_mail": {
        "type": "html",
        "url": "https://www.dailymail.co.uk/sport/teampages/arsenal.html",
        "notes": "Team page; headlines/links only."
    },
    "the_times": {
        "type": "html",
        "url": "https://www.thetimes.co.uk/sport/football/teams/arsenal",
        "notes": "Likely paywalled; headlines/links only."
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
    """Resolve a provider to a concrete URL (or None if unknown)."""
    meta = PROVIDERS.get(provider)
    if not meta:
        return None
    if meta.get("type") == "rss":
        if meta.get("url"):
            return meta["url"]
        if meta.get("builder") == "bbc_team_feed":
            return bbc_team_feed(section, team_code)
        return None
    if meta.get("type") == "html":
        return meta.get("url")
    return None

