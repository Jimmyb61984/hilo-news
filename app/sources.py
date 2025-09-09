from typing import Optional

"""
sources.py
- Maps provider slugs (from config.py) to actual feed URLs.
- Start with known-good RSS feeds; safely skip unknowns (we'll add later).
"""

# Provider registry.
# type: "rss" (we fetch immediately) or "html" (placeholder; currently disabled)
PROVIDERS = {
    # ✅ Known-good RSS feeds
    "bbc_sport": {
        "type": "rss",
        # For team-specific feeds we build per team/section.
        # BBC team feeds follow: https://feeds.bbci.co.uk/sport/football/teams/<team>/rss.xml
        "builder": "bbc_team_feed",
        "notes": "Official BBC Sport team RSS (Arsenal, etc)."
    },
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

    # 🟡 Placeholders (html): not fetched yet (we'll add scraper/RSS confirmation later)
    "sky_sports": {
        "type": "html",
        "url": None,
        "notes": "Team pages likely HTML only; we’ll add safe headline extraction later."
    },
    "evening_standard": {
        "type": "html",
        "url": None,
        "notes": "Arsenal tag/page—confirm RSS before enabling."
    },
    "the_times": {
        "type": "html",
        "url": None,
        "notes": "Paywalled; treat cautiously. Likely skip or link-out only."
    },
    "arsenal_official": {
        "type": "html",
        "url": None,
        "notes": "Club site news; confirm RSS or use safe HTML headlines later."
    },
}

def bbc_team_feed(section: Optional[str], team_code: Optional[str]) -> Optional[str]:
    """
    Build BBC team RSS URL.
    For Arsenal we can use either section='arsenal' or team_code='ARS'.
    """
    # Minimal map for now; expand later as you add more teams.
    team_slug = None
    if (section or "").lower() == "arsenal" or (team_code or "").upper() == "ARS":
        team_slug = "arsenal"

    if not team_slug:
        return None

    return f"https://feeds.bbci.co.uk/sport/football/teams/{team_slug}/rss.xml"

def build_feed_url(provider: str, section: Optional[str] = None, team_code: Optional[str] = None) -> Optional[str]:
    """
    Returns a concrete URL for a provider or None if not available.
    - For 'rss' providers:
        - If a static 'url' exists, return it.
        - If a 'builder' is specified, call it with (section, team_code).
    - For 'html' providers: return None for now (disabled).
    """
    meta = PROVIDERS.get(provider)
    if not meta:
        return None

    ptype = meta.get("type")
    if ptype == "rss":
        if "url" in meta and meta["url"]:
            return meta["url"]
        if meta.get("builder") == "bbc_team_feed":
            return bbc_team_feed(section, team_code)
        return None

    # 'html' not yet enabled — return None so fetcher can skip safely.
    return None
