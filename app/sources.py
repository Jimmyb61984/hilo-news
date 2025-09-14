# Declarative list of sources used by fetcher.
# We keep this small but representative, matching your goals:
# - Official/tier-1 with hero images -> Panel1
# - Fan sites -> Panel2

PROVIDERS = {
    # --- OFFICIAL / TIER-1 (prefer hero images) -----------------------------
    "ArsenalOfficial": {
        "type": "official",
        "mode": "html",
        "url": "https://www.arsenal.com/news",
        "base": "https://www.arsenal.com",
        "selectors": {
            "item": "div.teaser-item, .featured-article, .article-teaser",
            "link": "a[href]",
            "title": "a[aria-label], h3, h2",
            "summary": None,     # summary often not present on list
            "image": "img",      # we'll override with og:image from article page if missing
            "time": "time",      # if present; final time enforced via article page anyway
        },
    },
    "SkySports": {
        "type": "official",
        "mode": "rss",
        "url": "https://www.skysports.com/rss/12040",
    },
    "DailyMail": {
        "type": "official",
        "mode": "rss",
        "url": "https://www.dailymail.co.uk/sport/football/index.rss",
    },
    "EveningStandard": {
        "type": "official",
        "mode": "rss",
        "url": "https://www.standard.co.uk/sport/football/arsenal/rss",
    },
    "TheTimes": {
        "type": "official",
        "mode": "rss",
        "url": "https://www.thetimes.co.uk/sport/football/rss",
    },

    # --- FAN / BLOGS --------------------------------------------------------
    "Arseblog": {
        "type": "fan",
        "mode": "rss",
        "url": "https://arseblog.com/feed/",
    },
    "PainInTheArsenal": {
        "type": "fan",
        "mode": "rss",
        "url": "https://paininthearsenal.com/feed/",
    },
    "ArsenalInsider": {
        "type": "fan",
        "mode": "rss",
        "url": "https://www.arsenalinsider.com/feed/",
    },
}

