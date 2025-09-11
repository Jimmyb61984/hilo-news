from typing import Optional

# Providers you requested:
# Trusted: Arsenal Official (HTML), BBC (RSS), Sky (HTML), Daily Mail (HTML),
#          Evening Standard (HTML), The Times (HTML)
# Fans:    Arseblog (RSS), ArsenalInsider (RSS), Pain in the Arsenal (RSS)

PROVIDERS = {
    # ===== TRUSTED =====
    "bbc_sport": {
        "type": "rss",
        "url": "https://feeds.bbci.co.uk/sport/football/teams/arsenal/rss.xml",
        "notes": "BBC Sport team RSS (Arsenal).",
    },
    "arsenal_official": {
        "type": "html",
        "url": "https://www.arsenal.com/news",
        "notes": "Official site; headlines-only.",
    },
    "sky_sports": {
        "type": "html",
        "url": "https://www.skysports.com/arsenal-news",
        "notes": "Sky Sports Arsenal page; headlines-only.",
    },
    "daily_mail": {
        "type": "html",
        "url": "https://www.dailymail.co.uk/sport/football/arsenal/index.html",
        "notes": "Daily Mail Arsenal section; headlines-only.",
    },
    "evening_standard": {
        "type": "html",
        "url": "https://www.standard.co.uk/sport/football/arsenal",
        "notes": "Evening Standard Arsenal; headlines-only.",
    },
    "the_times": {
        "type": "html",
        "url": "https://www.thetimes.co.uk/sport/football/teams/arsenal",
        "notes": "The Times Arsenal; headlines-only (paywalled; headline links only).",
    },

    # ===== FANS =====
    "arseblog": {
        "type": "rss",
        "url": "https://arseblog.com/feed/",
        "notes": "Arseblog RSS.",
    },
    "arsenalinsider": {
        "type": "rss",
        "url": "https://www.arsenalinsider.com/feed",
        "notes": "ArsenalInsider RSS.",
    },
    "paininthearsenal": {
        "type": "rss",
        "url": "https://paininthearsenal.com/feed/",
        "notes": "Pain in the Arsenal RSS.",
    },
}

def build_feed_url(provider: str, section: Optional[str] = None, team_code: Optional[str] = None) -> Optional[str]:
    meta = PROVIDERS.get(provider)
    if not meta:
        return None
    return meta.get("url")
