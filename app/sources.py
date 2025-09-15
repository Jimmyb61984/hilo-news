# app/sources.py
# Declarative list of sources used by fetcher & backfill.
# Project goals reflected:
# - Official/tier-1 with strong images -> Panel1
# - Fan/blogs -> Panel2
# - Live fetching uses `mode`; backfill reuses CSS selectors (HTML crawl) even if mode is RSS.

PROVIDERS = {
    # --- OFFICIAL / TIER-1 (prefer hero images) -----------------------------
    "ArsenalOfficial": {
        "type": "official",
        "mode": "html",
        "url": "https://www.arsenal.com/news",
        "base": "https://www.arsenal.com",
        "selectors": {
            # Listing cards on arsenal.com/news (robust across common layouts)
            "item": "div.teaser-item, .featured-article, .article-teaser, article",
            "link": "a[href]",
            "title": "a[aria-label], h3, h2",
            "summary": None,     # summary often not in list; we’ll enrich from article page if needed
            "image": "img",      # final image enforced via og:image from article page in fetcher/backfill
            "time": "time",      # article page publish time takes precedence
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
            # article cards on listing pages (WordPress variants)
            "item": "article.post, article.type-post, article.hentry, article",
            # primary link to post (entry-title first, fallback to common patterns)
            "link": "h2.entry-title a, .entry-title a, header h2 a, h2 a, h3 a, a[rel='bookmark'], a[href*='/2025/'], a[href*='/2024/']",
            # visible title on card (fallbacks)
            "title": "h2.entry-title, .entry-title, header h2, h2, h3",
            # best-effort image (featured or first image)
            "image": "img.wp-post-image, figure img, .post-thumbnail img, img",
            # published hint on listing if present
            "time": "time[datetime], time.entry-date, time.published, time",
        },
    },
    "PainInTheArsenal": {
        "type": "fan",
        "mode": "rss",
        "url": "https://paininthearsenal.com/feed/",
        # Added for backfill (HTML archive crawl)
        "base": "https://paininthearsenal.com",
        "selectors": {
            # article tiles/cards used across FanSided/MinuteMedia skins
            "item": "article, .mm-card, .mm-article, .c-compact-river__entry, .c-entry-box--compact",
            # link to post—cover common classes & fallbacks
            "link": "h2 a, h3 a, a.c-entry-box--compact__title, a.mm-card__title-link, a.jeg_post_title, a[href*='/2025/'], a[href*='/2024/']",
            # title element variants
            "title": "h2, h3, .c-entry-box--compact__title, .mm-card__title, .entry-title",
            # image element variants
            "image": "img, figure img, .mm-card__image img, .c-entry-box--compact__image img",
            # published time variants on listing
            "time": "time[datetime], time, .byline__date, .c-byline__item time, .entry-date",
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
            "time": "time[datetime], time, .entry-date",
        },
    },
}
