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
        # Added for backfill (HTML archive crawl)
        "base": "https://arseblog.com",
        "selectors": {
            "item": "article",
            "link": "h2 a, h3 a, a[href*='/2025/'], a[href*='/2024/']",
            "title": "h2, h3",
            "image": "img",
            "time": "time",
            # no summary on listing; article page fallback handled by backfill
        },
    },
    "PainInTheArsenal": {
        "type": "fan",
        "mode": "rss",
        "url": "https://paininthearsenal.com/feed/",
        # Added for backfill (HTML archive crawl)
        "base": "https://paininthearsenal.com",
        "selectors": {
            "item": "article, .mm-article, .c-compact-river__entry",
            "link": "h2 a, h3 a, a[href*='/2025/'], a[href*='/2024/']",
            "title": "h2, h3, .c-entry-box--compact__title",
            "image": "img, figure img",
            "time": "time, .byline__date, .c-byline__item time",
        },
    },
    "ArsenalInsider": {
        "type": "fan",
        "mode": "rss",
        "url": "https://www.arsenalinsider.com/feed/",
        # Added for backfill (HTML archive crawl)
        "base": "https://www.arsenalinsider.com",
        "selectors": {
            "item": "article, .post, .td_block_inner > .td_module_wrap, .jeg_posts > article",
            "link": "h2 a, h3 a, .entry-title a, a.jeg_post_title, a[href*='/2025/'], a[href*='/2024/']",
            "title": "h2, h3, .entry-title, .jeg_post_title",
            "image": "img, figure img",
            "time": "time, .entry-date",
        },
    },
}
