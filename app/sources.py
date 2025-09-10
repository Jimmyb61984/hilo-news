from typing import Optional

"""
sources.py
- Maps provider slugs (from config.py) to actual feed URLs.
- 'rss' providers return concrete URLs.
- 'html' providers are placeholders (disabled until we add safe parsing).
"""

PROVIDERS = {
    # ============== TRUSTED (THUMBNAILS ALLOWED) ==============

    # BBC Sport — use a STATIC Arsenal team RSS (works even if caller
    # doesn't pass section/team_code).
    "bbc_sport": {
        "type": "rss",
        "url": "https://feeds.bbci.co.uk/sport/football/teams/arsenal/rss.xml",
        "notes": "Official BBC Sport team RSS (Arsenal-only)."
    },

    # Arsenal Official — site RSS (club news)
    "arsenal_official": {
        "type": "rss",
        "url": "https://www.arsenal.com/news/rss",
        "notes": "Official Arsenal.com news RSS."
    },

    # ============== FAN SITES (text-only per fetcher policy) ==============

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

    # ============== PLACEHOLDERS (DISABLED FOR NOW) ==============
    # HTML pages (no reliable team-only RSS). We’ll enable later via safe parsing.

    "sky_sports": {
        "type": "html",
        "url": "https://www.skysports.com/arsenal",
        "notes": "Team page is HTML; disabled until extractor is added."
    },
    "evening_standard": {
        "type": "html",
        "url": "https://www.standard.co.uk/sport/football/arsenal",
        "notes": "Arsenal tag/page; HTML; disabled until extractor is added."
    },
    "daily_mail": {
        "type": "html",
        "url": "https://www.dailymail.co.uk/sport/teampages/arsenal.html",
        "notes": "Team page; HTML; disabled until extractor is added."
    },
    "the_times": {
        "type": "html",
        "url": "https://www.thetimes.co.uk/sport/football/teams/arsenal",
        "notes": "Likely paywalled; HTML; disabled until extractor is added."
    },
}

# Builders kept for future use (other teams), but not needed for BBC Arsenal now.
def bbc_team_feed(section: Optional[str], team_code: Optional[str]) -> Optional[str]:
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

    ptype = meta.get("type")
    if ptype == "rss":
        if meta.get("url"):
            return meta["url"]
        if meta.get("builder") == "bbc_team_feed":
            return bbc_team_feed(section, team_code)
        return None

    # 'html' providers are disabled (no scraping in fetcher).
    return None

