# app/sources.py
from typing import Dict, Any

PROVIDERS: Dict[str, Dict[str, Any]] = {
    # --- Fan sites (RSS/HTML) -> Panel2 ---
    "Arseblog": {
        "type": "rss",
        "url": "https://arseblog.com/feed/",
        "is_official": False,
    },
    "PainInTheArsenal": {
        "type": "rss",
        "url": "https://paininthearsenal.com/feed/",
        "is_official": False,
    },
    "ArsenalInsider": {
        "type": "html",
        "url": "https://www.arsenalinsider.com/news",
        "is_official": False,
        "selectors": {
            "item": "article",
            "title": "h2 a",
            "link": "h2 a",
            "summary": "p",
            "date": "time",
            "thumb": "img",
        },
    },

    # --- Official / Major media (HTML) -> Panel1 ---
    "SkySports": {
        "type": "html",
        "url": "https://www.skysports.com/arsenal-news",
        "is_official": True,
        "selectors": {
            "item": "article, .news-list__item, li.news-list__item, .card, li.card",
            "title": "h3, h2, .news-list__headline, .card__headline, [data-type='headline'], .headline",
            "link": "a[href]",
            "summary": ".news-list__snippet, .card__standfirst, p, .teaser__copy",
            "date": "time[datetime], .timestamp, time, [data-time]",
            "thumb": "img[src], img[data-src], meta[property='og:image'], meta[name='twitter:image']",
        },
    },
    "TheStandard": {
        "type": "html",
        "url": "https://www.standard.co.uk/sport/football/arsenal",
        "is_official": True,
        "selectors": {
            "item": "article, .teaser, li.teaser, .card, li.card",
            "title": "h3, h2, .teaser__headline, .headline, a[title]",
            "link": "a[href]",
            "summary": "p, .teaser__standfirst, .standfirst",
            "date": "time[datetime], time, [data-time]",
            "thumb": "img[src], img[data-src], meta[property='og:image'], meta[name='twitter:image']",
        },
    },
    "DailyMail": {
        "type": "html",
        "url": "https://www.dailymail.co.uk/sport/teampages/arsenal.html",
        "is_official": True,
        "selectors": {
            "item": "article, .article, li.article, .sport > article, .linkro-darkred",
            "title": "h2, h3, .linkro-darkred, a[title]",
            "link": "a[href]",
            "summary": "p, .mol-para-with-font",
            "date": "time[datetime], time, [data-ftime], [data-timestamp]",
            "thumb": "img[src], img[data-src], meta[property='og:image'], meta[name='twitter:image']",
        },
    },
    "TheTimes": {
        "type": "html",
        "url": "https://www.thetimes.co.uk/sport/football/team/arsenal",
        "is_official": True,
        "selectors": {
            "item": "article, .Article, .Item, li.Item, .teaser",
            "title": "h2, h3, .Item-headline, .teaser__headline",
            "link": "a[href]",
            "summary": "p, .Item-standfirst, .teaser__standfirst",
            "date": "time[datetime], time, [data-time]",
            "thumb": "img[src], img[data-src], meta[property='og:image'], meta[name='twitter:image']",
        },
    },
    "ArsenalOfficial": {
        "type": "html",
        "url": "https://www.arsenal.com/news",
        "is_official": True,
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
    meta = PROVIDERS.get(provider_name)
    if not meta:
        return ""
    return meta.get("url", "")

