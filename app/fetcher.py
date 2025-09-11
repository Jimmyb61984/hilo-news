import hashlib
import html
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

# --- HTTP session with realistic headers (helps against anti-bot blocks) ---
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
})

HTTP_TIMEOUT = 8.0

# --- Simple in-memory cache for thumbnails (and optional page html) ---
_THUMB_CACHE: Dict[str, Dict[str, Any]] = {}  # url -> {"ts": int, "thumb": str}
_CACHE_TTL = 60 * 60 * 24  # 24h


def _now() -> int:
    return int(time.time())


def _get_page(url: str) -> Optional[str]:
    try:
        resp = SESSION.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if 200 <= resp.status_code < 300 and resp.content:
            # Try to decode with apparent encoding fallback
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text
    except requests.RequestException:
        pass
    return None


def _extract_meta_image(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    # og:image
    tag = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
    if tag and tag.get("content"):
        return urljoin(base_url, tag["content"].strip())

    # twitter:image
    tag = soup.find("meta", attrs={"name": "twitter:image"}) or soup.find("meta", property="twitter:image")
    if tag and tag.get("content"):
        return urljoin(base_url, tag["content"].strip())

    # sometimes sites use itemprop
    tag = soup.find("meta", itemprop="image")
    if tag and tag.get("content"):
        return urljoin(base_url, tag["content"].strip())

    return None


def _extract_meta_description(soup: BeautifulSoup) -> Optional[str]:
    tag = soup.find("meta", attrs={"name": "description"})
    if tag and tag.get("content"):
        return tag["content"].strip()
    # OpenGraph description fallback
    tag = soup.find("meta", property="og:description")
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _first_paragraph_text(soup: BeautifulSoup) -> Optional[str]:
    # try article main first
    article = soup.find("article")
    if article:
        p = article.find("p")
        if p:
            text = p.get_text(separator=" ", strip=True)
            if text:
                return text
    # fallback: first non-empty <p>
    for p in soup.find_all("p"):
        text = p.get_text(separator=" ", strip=True)
        if text and len(text) > 40:
            return text
    return None


def _clean_text(raw: str, max_len: int = 220) -> str:
    # strip tags -> text, decode entities, collapse spaces
    text = BeautifulSoup(raw, "lxml").get_text(separator=" ", strip=True)
    text = html.unescape(text)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def _best_entry_summary(entry: Dict[str, Any]) -> Optional[str]:
    """
    Try multiple known feed fields for a usable summary, in order.
    """
    # 1) content[0].value – often full HTML for blogs (Arseblog)
    content = entry.get("content")
    if isinstance(content, list) and content:
        val = content[0].get("value")
        if val:
            cleaned = _clean_text(val)
            if cleaned:
                return cleaned

    # 2) summary_detail / summary
    sd = entry.get("summary_detail")
    if sd and sd.get("value"):
        cleaned = _clean_text(sd["value"])
        if cleaned:
            return cleaned

    if entry.get("summary"):
        cleaned = _clean_text(entry["summary"])
        if cleaned:
            return cleaned

    # 3) description
    if entry.get("description"):
        cleaned = _clean_text(entry["description"])
        if cleaned:
            return cleaned

    return None


def _generate_summary_from_page(url: str) -> Optional[str]:
    html_doc = _get_page(url)
    if not html_doc:
        return None
    soup = BeautifulSoup(html_doc, "lxml")

    # Prefer meta description
    desc = _extract_meta_description(soup)
    if desc:
        return _clean_text(desc)

    # Else first meaningful paragraph
    para = _first_paragraph_text(soup)
    if para:
        return _clean_text(para)

    return None


def _get_thumbnail(url: str) -> Optional[str]:
    # cache check
    cached = _THUMB_CACHE.get(url)
    if cached and (_now() - cached["ts"] < _CACHE_TTL):
        return cached.get("thumb")

    html_doc = _get_page(url)
    thumb = None
    if html_doc:
        soup = BeautifulSoup(html_doc, "lxml")
        thumb = _extract_meta_image(soup, base_url=url)

    _THUMB_CACHE[url] = {"ts": _now(), "thumb": thumb}
    return thumb


def _stable_id(source: str, url: str) -> str:
    h = hashlib.sha1(f"{source}|{url}".encode("utf-8")).hexdigest()
    return h


def fetch_feed(source_key: str, feed_url: str, team_codes: List[str]) -> List[Dict[str, Any]]:
    """
    Pulls articles from a given RSS/Atom URL and normalizes them.
    """
    out: List[Dict[str, Any]] = []
    parsed = feedparser.parse(feed_url)

    for entry in parsed.entries:
        link = entry.get("link") or entry.get("id")
        title = entry.get("title") or ""
        if not link or not title:
            continue

        # Summary – try feed fields first, then page fallback
        summary = _best_entry_summary(entry)
        if not summary:
            # fetch page and generate a summary
            summary = _generate_summary_from_page(link) or ""

        # Thumbnail – only fetch if we don't already have a clear image in feed
        thumb = None

        # Try media:thumbnail or media_content if present in feed
        media_thumb = None
        media = entry.get("media_thumbnail") or entry.get("media_content")
        if isinstance(media, list) and media:
            media_thumb = media[0].get("url")

        if media_thumb:
            thumb = media_thumb
        else:
            thumb = _get_thumbnail(link)

        item = {
            "id": _stable_id(source_key, link),
            "title": _clean_text(title, max_len=160),
            "source": source_key,
            "summary": summary,
            "url": link,
            "thumbnailUrl": thumb,
            "publishedUtc": _entry_published(entry),
            "teams": team_codes,
            "leagues": [],  # can populate later if needed
        }
        out.append(item)

    return out


def _entry_published(entry: Dict[str, Any]) -> str:
    # Prefer updated/parsing; fallback to now-ish
    # feedparser normalizes 'published_parsed' / 'updated_parsed'
    import datetime as dt

    tm = entry.get("published_parsed") or entry.get("updated_parsed")
    if tm:
        try:
            return dt.datetime(*tm[:6], tzinfo=dt.timezone.utc).isoformat()
        except Exception:
            pass
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).isoformat()

