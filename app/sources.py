# sources.py
"""
Defines the news providers and (for HTML sites) the CSS selectors
the scraper needs to find items, titles, links, summaries, and thumbnails.

You can keep expanding this list. For now, it’s tuned for Arsenal.
"""

from typing import Dict, Any

# For now, these URLs are team-specific. If you later add multi-team support,
# update build_feed_url to format placeholders per provider.
PROVIDERS: Dict[str, Dict[str, Any]] = {
    # --- RSS sources (already provide clean summaries) ---
    "Arseblog": {
        "type": "rss",
        "url": "https://arseblog.com/feed/",
    },
    "PainInTheArsenal": {
        "type": "rss",
        "url": "https://paininthearsenal.com/feed/",
    },

    # --- HTML sources (need selectors) ---
    "SkySports": {
        "type": "html",
        "url": "https://www.skysports.com/arsenal-news",
        "selectors": {
            # broad, resilient selectors that work across common layouts
            "item": "article, .news-list__item, li.news-list__item, .card, li.card",
            "title": "h3, h2, .news-list__headline, .card__headline, [data-type='headline'], .headline",
            "link": "a[href]",
            # optional summary/date/thumb (we also have strong fallbacks in fetcher)
            "summary": ".news-list__snippet, .card__standfirst, p, .teaser__copy",
            "date": "time[datetime], .timestamp, time, [data-time]",
            "thumb": "img[src], img[data-src], meta[property='og:image'], meta[name='twitter:image']",
        },
    },

    "ArsenalOfficial": {
        "type": "html",
        "url": "https://www.arsenal.com/news",
        "selectors": {
            "item": "article, .article-teaser, li.teaser, .teaser",
            "title": "h3, h2, .teaser__title, .article-teaser__title",
            "link": "a[href]",
            "summary": ".article-teaser__summary, .teaser__copy, p",
            "date": "time[datetime], time, [data-time]",
            "thumb": "img[src], img[data-src], meta[property='og:image'], meta[name='twitter:image']",
        },
    },
}


def build_feed_url(provider_name: str, *, team_code: str) -> str:
    """
    Later you can make this return team-specific URLs using team_code.
    For now all entries above are already team-specific and we just return the static URL.
    """
    meta = PROVIDERS.get(provider_name)
    if not meta:
        return ""
    return meta.get("url", "")

