# tools/backfill.py
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
import sys
import re

import httpx
from bs4 import BeautifulSoup

# Import from your app (existing code)
sys.path.append(".")
from app.sources import PROVIDERS  # your providers map
from app.db import ensure_schema, upsert_items
from app.policy import canonicalize_provider  # existing helper
from app.fetcher import _parse_date_guess  # reuse your robust date guesser if present

HTTP_TIMEOUT = 12.0

WORDPRESS_PROVIDERS = {
    # provider_key in your sources -> optional archive base override
    # If None: we use the provider 'url' from sources for page/ pagination.
    "Arseblog": None,
    "ArsenalInsider": None,
    "PainInTheArsenal": None,
}

def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _fetch_url_text(client: httpx.Client, url: str) -> Optional[str]:
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
        return og.get("content").strip()
    tw = soup.find("meta", attrs={"name": "twitter:image"})
    if tw and tw.get("content"):
        return tw.get("content").strip()
    img = soup.find("img")
    if img and img.get("src"):
        return img.get("src").strip()
    return None

def _norm_item(entry: Dict[str, Any], provider: str) -> Optional[Dict[str, Any]]:
    title = (entry.get("title") or "").strip()
    url = (entry.get("url") or "").strip()
    if not title or not url:
        return None

    summary = (entry.get("summary") or "").strip()
    image = entry.get("imageUrl")
    published = entry.get("publishedUtc")

    # If published missing, try to guess from entry['published'] etc.
    if not published:
        for key in ("published", "pubDate", "date"):
            if entry.get(key):
                dt = _parse_date_guess(entry[key]) if entry.get(key) else None
                if dt:
                    published = _to_utc_iso(dt)
                    break
    if not published:
        # fallback to "now" to avoid losing the item
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

def _parse_article_datetime(soup: BeautifulSoup) -> Optional[datetime]:
    # Try ld+json
    ld = soup.find("script", type="application/ld+json")
    if ld and ld.string:
        try:
            import json
            data = json.loads(ld.string)
            # could be dict or list
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

    # meta tags
    meta_time = soup.find("meta", property="article:published_time")
    if meta_time and meta_time.get("content"):
        dt = _parse_date_guess(meta_time["content"])
        if dt:
            return dt

    # time tag
    t = soup.find("time")
    if t:
        for k in ("datetime", "title", "aria-label"):
            if t.get(k):
                dt = _parse_date_guess(t.get(k))
                if dt:
                    return dt

    return None

def _extract_from_listing(card: Any, src: Dict[str, Any], base_url: str) -> Optional[Dict[str, Any]]:
    """Use your existing selectors from sources.py for HTML mode."""
    a = card.select_one(src["selectors"]["link"])
    if not a or not a.get("href"):
        return None
    href = a["href"].strip()
    if not href.startswith("http"):
        # best effort absolute; sources.py should have base
        from urllib.parse import urljoin
        url = urljoin(src.get("base") or base_url, href)
    else:
        url = href

    title_el = None
    if src["selectors"].get("title"):
        title_el = card.select_one(src["selectors"]["title"])
    title = title_el.get_text(strip=True) if title_el else (a.get("title") or a.get_text(strip=True) or "")

    if not title:
        return None

    # summary optional
    summary = ""
    if src["selectors"].get("summary"):
        s_el = card.select_one(src["selectors"]["summary"])
        if s_el:
            summary = s_el.get_text(strip=True)

    # image optional
    image = None
    img_sel = src["selectors"].get("image")
    if img_sel:
        img_el = card.select_one(img_sel)
        if img_el:
            for key in ("data-src", "data-original", "src"):
                if img_el.get(key):
                    image = img_el.get(key).strip()
                    break

    # Try to read time from listing if available
    published = None
    time_sel = src["selectors"].get("time")
    if time_sel:
        t = card.select_one(time_sel)
        if t:
            for k in ("datetime", "title", "aria-label"):
                if t.get(k):
                    dt = _parse_date_guess(t.get(k))
                    if dt:
                        published = _to_utc_iso(dt)
                        break

    return {
        "title": title,
        "url": url,
        "summary": summary,
        "imageUrl": image,
        "publishedUtc": published,
        "type": src.get("type", "fan"),
    }

def _crawl_wordpress_listing(client: httpx.Client, provider_key: str, src: Dict[str, Any], since_iso: str, max_pages: int) -> List[Dict[str, Any]]:
    """Paginate /page/{n} and extract items using your selectors."""
    out: List[Dict[str, Any]] = []
    base = src.get("archive") or src.get("url") or ""
    base = base.rstrip("/")

    since_dt = _parse_date_guess(since_iso) or datetime(1970,1,1, tzinfo=timezone.utc)

    for page in range(1, max_pages + 1):
        page_url = f"{base}/page/{page}/"
        html = _fetch_url_text(client, page_url)
        if not html:
            break
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select(src["selectors"]["item"]) if src.get("selectors") else []
        if not cards:
            # stop paginating if nothing matched
            break

        page_count = 0
        for card in cards:
            entry = _extract_from_listing(card, src, base_url=base)
            if not entry:
                continue

            # If listing didn't include published, try article page quickly for published + OG image
            if not entry.get("publishedUtc"):
                art_html = _fetch_url_text(client, entry["url"])
                if art_html:
                    art_soup = BeautifulSoup(art_html, "lxml")
                    dt = _parse_article_datetime(art_soup)
                    if dt:
                        entry["publishedUtc"] = _to_utc_iso(dt)
                    if not entry.get("imageUrl"):
                        og = _extract_og_image(art_soup, base)
                        if og:
                            entry["imageUrl"] = og

            # Date guard
            pub_dt = None
            if entry.get("publishedUtc"):
                pub_dt = _parse_date_guess(entry["publishedUtc"])
            if not pub_dt:
                # If still unknown, keep (we’ll allow but cannot early-stop on date)
                pass
            else:
                if pub_dt < since_dt:
                    # This and the rest of the page are likely older; continue to next page
                    continue

            norm = _norm_item(entry, provider=provider_key)
            if norm:
                out.append(norm)
                page_count += 1

        # If the page yielded nothing we care about, move on; early stop heuristic is above.
        if page_count == 0:
            # But don’t break immediately—next page might still contain entries if archive layout is odd.
            pass

    return out

def backfill(since_iso: str, providers: Optional[List[str]], max_pages: int) -> int:
    """Backfill selected providers (or defaults) up to since_iso; return inserted count."""
    ensure_schema()
    selected = providers or list(WORDPRESS_PROVIDERS.keys())

    headers = {"User-Agent": "Hilo-Backfill/1.0"}
    inserted = 0

    with httpx.Client(headers=headers, timeout=HTTP_TIMEOUT) as client:
        for key in selected:
            src = PROVIDERS.get(key)
            if not src:
                print(f"[skip] Unknown provider key: {key}")
                continue
            if src.get("mode") != "html":
                print(f"[skip] {key}: not an HTML-mode provider; RSS backfill is not implemented in phase 1.")
                continue

            print(f"[crawl] {key} ...")
            items = _crawl_wordpress_listing(client, key, src, since_iso=since_iso, max_pages=max_pages)
            if not items:
                print(f"[crawl] {key}: 0 items found")
                continue

            count = upsert_items(items)
            inserted += count
            print(f"[upsert] {key}: {count} inserted")

    return inserted

def _season_start_iso_utc(today: Optional[datetime] = None) -> str:
    d = (today or datetime.utcnow())
    season_year = d.year if d.month >= 7 else d.year - 1
    return f"{season_year}-08-01T00:00:00Z"

def main():
    p = argparse.ArgumentParser(description="Backfill season-length articles into SQLite.")
    p.add_argument("--from", dest="since", default=_season_start_iso_utc(), help="ISO date or YYYY-MM-DD (default: season start Aug 1)")
    p.add_argument("--providers", nargs="*", default=None, help="Subset of providers to backfill (default: WordPress set)")
    p.add_argument("--max-pages-per-provider", type=int, default=20, help="Max archive pages per provider (default: 20)")
    args = p.parse_args()

    # Normalize since to full ISO w/ Z
    since = args.since
    if re.match(r"^\d{4}-\d{2}-\d{2}$", since):
        since = since + "T00:00:00Z"

    print(f"[start] backfill since {since} providers={args.providers or 'WordPress-defaults'} pages={args.max_pages_per_provider}")
    count = backfill(since_iso=since, providers=args.providers, max_pages=args.max_pages_per_provider)
    print(f"[done] inserted={count}")

if __name__ == "__main__":
    main()
