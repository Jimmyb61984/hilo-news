from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx
import feedparser
from bs4 import BeautifulSoup

from app.sources import PROVIDERS
from app.policy import canonicalize_provider

HTTP_TIMEOUT = 12.0
MAX_ITEMS_PER_SOURCE = 40  # raw fetch cap before policy


def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_date_guess(text: str) -> Optional[datetime]:
    try:
        import email.utils as eut
        tup = eut.parsedate_tz(text)
        if tup:
            ts = eut.mktime_tz(tup)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        pass
    try:
        from dateutil import parser as du
        return du.parse(text).astimezone(timezone.utc)
    except Exception:
        return None


def _fetch_url_text(client: httpx.Client, url: str) -> Optional[str]:
    # tiny retry loop for resilience
    for _ in range(2):
        try:
            r = client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
            if r.status_code == 200 and r.text:
                return r.text
        except Exception:
            pass
    return None


def _extract_og_image(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    og = soup.find("meta", property="og:image")
    if og and og.get("content"):
        return urljoin(base_url, og["content"].strip())
    tw = soup.find("meta", attrs={"name": "twitter:image"})
    if tw and tw.get("content"):
        return urljoin(base_url, tw["content"].strip())
    img = soup.find("img")
    if img and img.get("src"):
        return urljoin(base_url, img["src"])
    return None


def _extract_arsenal_published(html: str) -> Optional[datetime]:
    soup = BeautifulSoup(html, "lxml")
    meta_time = soup.find("meta", property="article:published_time")
    if meta_time and meta_time.get("content"):
        try:
            from dateutil import parser as du
            return du.parse(meta_time["content"]).astimezone(timezone.utc)
        except Exception:
            pass
    t = soup.find("time")
    if t and t.get("datetime"):
        dt = _parse_date_guess(t["datetime"])
        if dt:
            return dt
    ld = soup.find("script", type="application/ld+json")
    if ld and ld.string:
        try:
            import json
            data = json.loads(ld.string)
            if isinstance(data, dict) and "datePublished" in data:
                dt = _parse_date_guess(data["datePublished"])
                if dt:
                    return dt
            if isinstance(data, list):
                for node in data:
                    if isinstance(node, dict) and "datePublished" in node:
                        dt = _parse_date_guess(node["datePublished"])
                        if dt:
                            return dt
        except Exception:
            pass
    return None


def _ensure_arsenal_publish_time(client: httpx.Client, item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Make a best effort to get the true publish time for ArsenalOfficial,
    but NEVER drop the item if extraction fails. Fallback to existing time.
    """
    url = item.get("url", "")
    if "arsenal.com" not in url:
        return item

    html = _fetch_url_text(client, url)
    if html:
        dt = _extract_arsenal_published(html)
        # enrich image if missing
        if not item.get("imageUrl"):
            soup = BeautifulSoup(html, "lxml")
            img = _extract_og_image(soup, url)
            if img:
                item["imageUrl"] = img
        if dt:
            item["publishedUtc"] = _to_utc_iso(dt)
        else:
            # explicit marker for observability
            item.setdefault("meta", {})["extraction"] = "fallback"
    else:
        item.setdefault("meta", {})["extraction"] = "no-html"

    return item


def _normalize_item(entry: Dict[str, Any], provider: str) -> Optional[Dict[str, Any]]:
    title = (entry.get("title") or "").strip()
    url = (entry.get("url") or "").strip()
    if not title or not url:
        return None
    summary = (entry.get("summary") or "").strip()
    image = entry.get("imageUrl")
    published = entry.get("publishedUtc")
    if not published:
        for key in ("published", "pubDate", "date"):
            if entry.get(key):
                dt = _parse_date_guess(entry[key])
                if dt:
                    published = _to_utc_iso(dt)
                    break
    if not published:
        published = _to_utc_iso(datetime.utcnow())
    return {
        "title": title,
        "url": url,
        "summary": summary,
        "imageUrl": image,
        "provider": canonicalize_provider(provider),
        "type": entry.get("type", "fan"),
        "publishedUtc": published,
    }


def _fetch_rss_source(client: httpx.Client, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    d = feedparser.parse(src["url"])
    for e in d.entries[:MAX_ITEMS_PER_SOURCE]:
        url = e.get("link") or ""
        title = e.get("title") or ""
        summary = e.get("summary") or e.get("subtitle") or ""
        published = None
        if e.get("published"):
            guess = _parse_date_guess(e["published"])
            published = _to_utc_iso(guess or datetime.utcnow())
        image = None
        media = e.get("media_content") or e.get("media_thumbnail") or []
        if media and isinstance(media, list) and media[0].get("url"):
            image = media[0]["url"]
        enclosure = e.get("enclosures") or []
        if not image and enclosure and enclosure[0].get("href"):
            image = enclosure[0]["href"]
        out.append({
            "title": title,
            "url": url,
            "summary": summary,
            "imageUrl": image,
            "publishedUtc": published,
            "type": src.get("type", "fan"),
        })
    return out


def _fetch_html_source(client: httpx.Client, src: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    html = _fetch_url_text(client, src["url"])
    if not html:
        return out
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select(src["selectors"]["item"])[:MAX_ITEMS_PER_SOURCE]
    for card in cards:
        a = card.select_one(src["selectors"]["link"])
        if not a or not a.get("href"):
            continue
        url = urljoin(src["base"], a["href"])
        title_el = card.select_one(src["selectors"]["title"]) if src["selectors"].get("title") else None
        title = title_el.get_text(strip=True) if title_el else (a.get("title") or a.get_text(strip=True) or "")
        if not title:
            continue
        summary = ""
        if src["selectors"].get("summary"):
            sum_el = card.select_one(src["selectors"]["summary"])
            if sum_el:
                summary = sum_el.get_text(strip=True)
        image = None
        img_sel = src["selectors"].get("image")
        if img_sel:
            img_el = card.select_one(img_sel)
            if img_el:
                for key in ("data-src", "data-original", "src"):
                    if img_el.get(key):
                        image = urljoin(src["base"], img_el.get(key))
                        break
        published = None
        time_sel = src["selectors"].get("time")
        if time_sel:
            t = card.select_one(time_sel)
            if t:
                for key in ("datetime", "title", "aria-label"):
                    if t.get(key):
                        dt = _parse_date_guess(t.get(key))
                        if dt:
                            published = _to_utc_iso(dt)
                            break
        out.append({
            "title": title,
            "url": url,
            "summary": summary,
            "imageUrl": image,
            "publishedUtc": published,
            "type": src.get("type", "official"),
        })
    return out


def fetch_news(team_code: str = "ARS", allowed_types: Optional[set] = None) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    headers = {"User-Agent": "Hilo/2.0 (+https://hilo-news)"}
    with httpx.Client(headers=headers, timeout=HTTP_TIMEOUT) as client:
        for provider_key, src in PROVIDERS.items():
            if allowed_types and src.get("type") not in allowed_types:
                continue
            try:
                if src["mode"] == "rss":
                    raw = _fetch_rss_source(client, src)
                else:
                    raw = _fetch_html_source(client, src)
            except Exception:
                raw = []

            for r in raw:
                r["type"] = src.get("type", r.get("type", "fan"))
                item = _normalize_item(r, provider_key)
                if not item:
                    continue

                # ArsenalOfficial publish-time: enrich but don't drop on failure
                item = _ensure_arsenal_publish_time(client, item)

                # Add hero image for official if missing (best effort)
                if item["type"] == "official" and not item.get("imageUrl"):
                    html = _fetch_url_text(client, item["url"])
                    if html:
                        soup = BeautifulSoup(html, "lxml")
                        og = _extract_og_image(soup, item["url"])
                        if og:
                            item["imageUrl"] = og

                items.append(item)
    return items

