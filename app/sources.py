from typing import Optional

"""
sources.py
- Maps provider slugs (from config.py) to actual feed URLs.
- 'rss' providers return concrete URLs (static or via a builder).
- 'html' providers are placeholders for future safe headline parsing.
"""

PROVIDERS = {
    # ============== TRUSTED (THUMBNAILS ALLOWED) ==============

    # BBC Sport — team RSS via builder (pure Arsenal)
    "bbc_sport": {
        "type": "rss",
        "builder": "bbc_team_feed",
        "notes": "Official BBC Sport team RSS (Arsenal-only when section/team_code indicates)."
    },

    # Arsenal Official — site RSS (men's news feed)
    # If this ever changes, we’ll update the URL; it’s currently live and returns standard RSS.
    "arsenal_official": {
        "type": "rss",
        "url": "https://www.arsenal.com/news/rss",
        "notes": "Official Arsenal.com news RSS (club site)."
    },

    # ============== FAN SITES (TEXT-ONLY BACKEND POLICY) ==============

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
    # These are general publisher pages without stable, team-only RSS endpoints.
    # We’ll enable them later via safe HTML headline extraction or confirmed RSS.

    "sky_sports": {
        "type": "html",
        "url": "https://www.skysports.com/arsenal",
        "notes": "Team page is HTML. To enable, add parser to extract Arsenal-only headlines safely."
    },
    "evening_standard": {
        "type": "html",
        "url": "https://www.standard.co.uk/sport/football/arsenal",
        "notes": "Arsenal tag/page; HTML. Enable later with extractor."
    },
    "daily_mail": {
        "type": "html",
        "url": "https://www.dailymail.co.uk/sport/teampages/arsenal.html",
        "notes": "Team page; HTML. Enable later with extractor."
    },
    "the_times": {
        "type": "html",
        "url": "https://www.thetimes.co.uk/sport/football/teams/arsenal",
        "notes": "Likely paywalled; treat cautiously. Enable later with extractor."
    },
}

def bbc_team_feed(section: Optional[str], team_code: Optional[str]) -> Optional[str]:
    """
    Build BBC team RSS URL. For Arsenal, section='arsenal' or team_code='ARS' yields Arsenal RSS.
    """
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

    # 'html' providers are disabled for now (no scraping in fetcher).
    return None

