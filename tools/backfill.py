# tools/backfill.py
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
import sys
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

# Project imports
sys.path.append(".")
from app.sources import PROVIDERS
from app.db import ensure_schema, upsert_items
from app.policy import canonicalize_provider
from app.fetcher import _parse_date_guess  # reuse existing date parser

HTTP_TIMEOUT = 12.0
USER_AGENT = "Hilo-Backfill/2.0 (+https://hilo-news)"
MAX_PER_PROVIDER_DEFAULT = 400  # hard cap to avoid runaway crawls

# Providers we want to backfill now (fan & official are both fine)
DEFAULT_BACKFILL_SET = [
    "Arseblog",
    "ArsenalInsider",
    "PainInTheArsenal",
    "EveningStandard",
    "DailyMail",
    "SkySports",
    "ArsenalOfficial",
]

def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def _fetch_text(client: httpx.Client, url: str, debug: bool=False) -> Optional[str]:
    try:
        r = client.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
        if debug:
            print(f"[http {r.status_code}] {url}")
        if r.status_code == 200 and r.text:
            return r.text
    except Exception as e:
        if debug:
            print(f"[http error] {url} -> {e}")
    return None

def _is_same_host(a: str, b: str) -> bool:
    try:
        return urlparse(a).hostname == urlparse(b).hostname
    except Exception:
        return False

def _discover_sitemaps(client: httpx.Client, base: str, debug: bool=False) -> List[str]:
    """Try standard sitemap locations and robots.txt."""
    candidates = [
        urljoin(base + "/", "sitemap_index.xml"),
        urljoin(base + "/", "sitemap.xml"),
        urljoin(base + "/", "sitemap_index.xml.gz"),
        urljoin(base + "/", "sitemap.xml.gz"),
    ]
    # robots.txt discovery
    robots = _fetch_text(client, urljoin(base + "/", "robots.txt"), debug=debug)
    if robots:
        for line in robots.splitlines():
            if "sitemap:" in line.lower():
                try:
                    sm = line.split(":", 1)[1].strip()
                    if sm and sm not in candidates:
                        candidates.append(sm)
                except Exception:
                    pass
    found: List[str] = []
    for c in candidates:
        txt = _fetch_text(client, c, debug=debug)
        if txt and ("<urlset" in txt or "<sitemapindex" in txt):
            found.append(c)
    if debug:
        print(f"[sitemaps] {base} -> {found or 'none'}")
    return found

def _parse_xml_urls(xml_text: str) -> Tuple[List[Tuple[str, Optional[str]]], List[str]]:
    """Return (urlset entries [(loc, lastmod)], sitemaps [loc])."""
    urls: List[Tuple[str, Optional[str]]] = []
    maps: List[str] = []
    try:
        soup = BeautifulSoup(xml_text, "xml")
        for sm in soup.select("sitemap > loc"):
            loc = sm.get_text(strip=True)
            if loc:
                maps.append(loc)
        for u in soup.select("url"):
            loc_el = u.find("loc")
            if not loc_el:
                continue
            loc = loc_el.get_text(strip=True)
            lastmod_el = u.find("lastmod")
            lastmod = lastmod_el.get_text(strip=True) if lastmod_el else None
            if loc:
                urls.append((loc, lastmod))
    except Exception:
        pass
    return urls, maps

def _collect_urls_from_sitemaps(client: httpx.Client, base: str, since_iso: str, debug: bool=False, limit: int=MAX_PER_PROVIDER_DEFAULT) -> List[str]:
    since_dt = _parse_date_guess(since_iso) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    sitemaps = _discover_sitemaps(client, base, debug=debug)
    if not sitemaps:
        return []

    urls_out: List[str] = []
    visited_maps = set()

    def _walk_map(url: str):
        if url in visited_maps:
            return
        visited_maps.add(url)
        xml = _fetch_text(client, url, debug=debug)
        if not xml:
            return
        urlset, submaps = _parse_xml_urls(xml)
        # Heuristic: prefer post/article maps when present
        prioritized = [m for m in submaps if any(k in m.lower() for k in ("post", "news", "blog", "article"))] + \
                      [m for m in submaps if m not in submaps]
        targets = prioritized if submaps else []

        # Leaf urlset
        if urlset:
            for loc, lastmod in urlset:
                if not _is_same_host(base, loc):
                    continue
                if lastmod:
                    dt = _parse_date_guess(lastmod)
                    if dt and dt < since_dt:
                        continue
                urls_out.append(loc)
                if len(urls_out) >= limit:
                    return

        # Descend into submaps
        for sm in targets:
            if len(urls_out) >= limit:
                return
            _walk_map(sm)

    for sm in sitemaps:
        if len(urls_out) >= limit:
            break
        _walk_map(sm)

    # Deduplicate, keep order
    seen = set()
    deduped: List[str] = []
    for u in urls_out:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    if debug:
        print(f"[urls] collected={len(deduped)} (limit={limit}) from sitemaps for {base}")
    return deduped

def _extract_og_image(soup: BeautifulSoup) -> Optional[str]:
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

def _parse_article_datetime(soup: BeautifulSoup) -> Optional[datetime]:
    # ld+json (first)
    ld = soup.find("script", type="application/ld+json")
    if ld and ld.string:
        try:
            import json
            data = json.loads(ld.string)
            def _scan(node):
                if isinstance(node, dict):
                    if "datePublished" in node:
                        dt = _parse_date_guess(node["datePublished"])
                        if dt:
                            return dt
                    for v in node.values():
                        r = _scan(v)
                        if r:
                            return r
                elif isinstance(node, list):
                    for it in node:
                        r = _scan(it)
                        if r:
                            return r
                return None
            dt = _scan(data)
            if dt:
                return dt
        except Exception:
            pass
    # meta
    for key in ("article:published_time", "og:article:published_time"):
        mt = soup.find("meta", property=key)
        if mt and mt.get("content"):
            dt = _parse_date_guess(mt["content"])
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

def _norm(provider: str, url: str, soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
    # title
    title = None
    ogt = soup.find("meta", property="og:title")
    if ogt and ogt.get("content"):
        title = ogt["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)
    if not title:
        return None

    # summary (best effort)
    desc = soup.find("meta", attrs={"name": "description"})
    summary = desc["content"].strip() if desc and desc.get("content") else ""

    # image
    image = _extract_og_image(soup)

    # published
    dt = _parse_article_datetime(soup)
    published = _to_utc_iso(dt or datetime.now(timezone.utc))

    return {
        "title": title,
        "url": url,
        "summary": summary,
        "imageUrl": image,
        "provider": canonicalize_provider(provider),
        "type": PROVIDERS.get(provider, {}).get("type", "fan"),
        "publishedUtc": published,
    }

def backfill(since_iso: str, providers: Optional[List[str]], max_urls: int, debug: bool=False) -> int:
    ensure_schema()
    targets = providers or DEFAULT_BACKFILL_SET
    inserted_total = 0

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT) as client:
        for key in targets:
            src = PROVIDERS.get(key)
            if not src:
                print(f"[skip] Unknown provider: {key}")
                continue

            base = (src.get("base") or src.get("url") or "").rstrip("/")
            if not base.startswith("http"):
                print(f"[skip] {key}: no valid base/url to discover sitemaps")
                continue

            print(f"[sitemap] {key} …")
            urls = _collect_urls_from_sitemaps(client, base=base, since_iso=since_iso, debug=debug, limit=max_urls)
            if not urls:
                print(f"[sitemap] {key}: 0 URLs discovered")
                continue

            items: List[Dict[str, Any]] = []
            for u in urls:
                html = _fetch_text(client, u, debug=debug)
                if not html:
                    continue
                soup = BeautifulSoup(html, "lxml")
                norm = _norm(provider=key, url=u, soup=soup)
                if norm:
                    items.append(norm)

            if not items:
                print(f"[upsert] {key}: 0 inserted (no normalized items)")
                continue

            count = upsert_items(items)
            inserted_total += count
            print(f"[upsert] {key}: {count} inserted")

    return inserted_total

def _season_start_iso_utc(today: Optional[datetime] = None) -> str:
    d = (today or datetime.now(timezone.utc))
    season_year = d.year if d.month >= 7 else d.year - 1
    return f"{season_year}-08-01T00:00:00Z"

def main():
    p = argparse.ArgumentParser(description="Sitemap-driven backfill into SQLite (season depth).")
    p.add_argument("--from", dest="since", default=_season_start_iso_utc(), help="ISO date or YYYY-MM-DD (default: season start Aug 1)")
    p.add_argument("--providers", nargs="*", default=None, help=f"Subset of providers (default: {','.join(DEFAULT_BACKFILL_SET)})")
    p.add_argument("--max-urls-per-provider", type=int, default=200, help="Cap of article URLs per provider (default: 200)")
    p.add_argument("--debug", action="store_true", help="Verbose URLs and parsing")
    args = p.parse_args()

    since = args.since
    if re.match(r"^\d{4}-\d{2}-\d{2}$", since):
        since = since + "T00:00:00Z"

    print(f"[start] backfill since {since} providers={args.providers or DEFAULT_BACKFILL_SET} max={args.max_urls_per_provider}")
    total = backfill(since_iso=since, providers=args.providers, max_urls=args.max_urls_per_provider, debug=args.debug)
    print(f"[done] inserted={total}")

if __name__ == "__main__":
    main()

