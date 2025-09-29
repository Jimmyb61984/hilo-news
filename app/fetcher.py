# fetcher.py
from __future__ import annotations
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin
import hashlib
import re

import httpx
import feedparser
from bs4 import BeautifulSoup
from html import unescape

from app.sources import PROVIDERS
from app.policy import canonicalize_provider

# ------------------ FETCH CORE ------------------

HTTP_TIMEOUT = 10.0
UA = "HiloFeedBot/1.0 (+https://hilo.local)"

_client: Optional[httpx.AsyncClient] = None

async def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(HTTP_TIMEOUT),
            headers={"User-Agent": UA},
        )
    return _client

# ------------------ FEED HELPERS ------------------

def _parse_date_guess(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        from dateutil import parser as du
        return du.parse(s).astimezone(timezone.utc)
    except Exception:
        pass
    # RFC2822-ish fallbacks
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc)
        except Exception:
            continue
    return None

def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()

# ------------------ SUMMARY / TITLE HELPERS (emoji-safe) ---------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

_HTML_TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(text: str) -> str:
    if "<" not in (text or ""):
        return text or ""
    return _HTML_TAG_RE.sub(" ", text or "")

def sanitize_headline(raw: str) -> str:
    """Plain-text headline sanitizer: strip HTML tags, decode entities, collapse whitespace."""
    t = _strip_html(raw or "")
    t = unescape(t)
    t = _normalize_whitespace(t)
    # simple leak warning
    try:
        import logging
        if "<" in t or "&nbsp;" in t:
            logging.warning("TitleNotSanitizedLeak: %r", t[:140])
    except Exception:
        pass
    return t

def build_summary(text: str, min_sentences=3, max_sentences=5, hard_cap=900) -> str:
    """
    Try to build a concise extract from a body of text. Keep sentence boundaries,
    prefer 3–5 sentences, and hard-cap by characters. No ellipses to avoid clickbait look.
    """
    text = _normalize_whitespace(unescape(text or ""))
    if not text:
        return ""
    parts = _SENTENCE_SPLIT.split(text)
    summary = " ".join(parts[:max(min_sentences, min(max_sentences, len(parts)))])
    # try to end at punctuation
    if not summary.endswith((".", "!", "?")):
        i = min(len(parts), max_sentences)
        while i > 0:
            cand = " ".join(parts[:i]).strip()
            if cand.endswith((".", "!", "?")):
                summary = cand
                break
            i -= 1
    if len(summary) > hard_cap:
        cut = summary[:hard_cap]
        cut = cut[: cut.rfind(" ")] if " " in cut else cut
        # IMPORTANT: remove adding an ellipsis; return trimmed string only
        summary = cut
    return summary

def clean_title(title: str, provider: str) -> str:
    """
    Sanitize to plain text and remove trailing ' - Provider' / ' | Provider' / ' — Provider'.
    """
    # 1) sanitize to plain text
    t = sanitize_headline(title or "")
    if not t:
        return ""
    if provider:
        t = re.sub(rf"\s*[-|–—]\s*{re.escape(provider)}\s*$", "", t, flags=re.IGNORECASE)
        # Also handle space-separated brand with spaces (e.g., 'Evening Standard')
        brand = re.sub(r"([a-z])([A-Z])", r"\1 \2", provider).strip()
        if brand and brand.lower() != provider.lower():
            t = re.sub(rf"\s*[-|–—]\s*{re.escape(brand)}\s*$", "", t, flags=re.IGNORECASE)
    return t

# ------------------ PROVIDER PARSERS (examples trimmed for brevity) ------------------

async def fetch_rss(url: str) -> List[Dict[str, Any]]:
    feed = feedparser.parse(url)
    out = []
    for e in feed.entries:
        title = e.get("title") or ""
        link = e.get("link") or ""
        summary = e.get("summary") or e.get("description") or ""
        published = e.get("published") or e.get("updated") or ""
        dt = _parse_date_guess(published)
        out.append({
            "title": title,
            "url": link,
            "summary": build_summary(summary),
            "publishedUtc": dt.isoformat().replace("+00:00","Z") if dt else None,
        })
    return out

async def fetch_html_article(url: str) -> Dict[str, Any]:
    client = await get_client()
    r = await client.get(url, follow_redirects=True)
    r.raise_for_status()
    html = r.text

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
            if isinstance(data, dict):
                pub = data.get("datePublished") or data.get("dateModified")
                if pub:
                    dt = _parse_date_guess(pub)
                    if dt:
                        return dt
        except Exception:
            pass

    return {
        "title": clean_title(soup.title.text if soup.title else "", ""),
        "url": url,
        "summary": build_summary(soup.get_text(" ")),
        "publishedUtc": None,
    }

# ------------------ CANONICALIZATION ------------------

def canonicalize_item(it: Dict[str, Any]) -> Dict[str, Any]:
    prov = canonicalize_provider(it.get("provider") or "")
    url = it.get("url") or ""
    title = it.get("title") or ""
    summary = it.get("summary") or ""

    # Ensure plain-text title with no trailing brand
    title = clean_title(title, prov)

    # Ensure summary is compact
    summary = build_summary(summary)

    # Fallback for missing title
    if not title and summary:
        title = sanitize_headline(summary)

    return {
        "title": title,
        "url": url,
        "summary": summary,
        "imageUrl": it.get("imageUrl"),
        "publishedUtc": it.get("publishedUtc"),
        "provider": prov,
        "type": it.get("type") or "",
    }

# ------------------ (Other helpers & cluster logic remain unchanged) ------------------

def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def _is_fixtureish(title: str) -> bool:
    t = _clean(title)
    return (" vs " in t) or (" v " in t) or ("newcastle" in t) or ("man city" in t) or ("premier league" in t)

def _kind(title: str, summary: str) -> str:
    t = _clean(title + " " + summary)
    if "predicted lineup" in t or "xi vs" in t or "confirmed team news" in t:
        return "pre"
    if "player ratings" in t or "standout players" in t or "positives & negatives" in t:
        return "post"
    return "other"

from collections import defaultdict

def collapse_fixture_pre_post(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse multiple previews/reports for the same fixture, keep
    the strongest representative (simple heuristic: prefer official press,
    otherwise longest title/has image). This runs BEFORE page caps.
    """
    # Bucket only when clearly fixture-related to avoid false merges
    buckets = defaultdict(list)
    for it in items:
        title = it.get("title") or ""
        summary = it.get("summary") or ""
        if not _is_fixtureish(title):
            continue
        k = _kind(title, summary)
        if k == "other":
            continue
        # crude opponent signature by stripping common Arsenal tokens
        sig = _clean(re.sub(r"\barsenal\b", "", title))
        buckets[(sig, k)].append(it)

    keep_ids = set()
    for (sig, kind), group in buckets.items():
        # Prefer official providers first
        off = [g for g in group if g.get("type") == "official"]
        candidates = off if off else group
        # Then prefer has image, then longest clean title
        candidates.sort(key=lambda x: (bool(x.get("imageUrl")), len(x.get("title") or "")), reverse=True)
        keep_ids.add((candidates[0].get("id") or candidates[0].get("url")))

    out = []
    for it in items:
        title = it.get("title") or ""
        summary = it.get("summary") or ""
        id_or_url = it.get("id") or it.get("url")
        if _is_fixtureish(title) and _kind(title, summary) in {"pre", "post"}:
            if id_or_url in keep_ids:
                out.append(it)
            else:
                # drop duplicates within the same fixture-kind cluster
                continue
        else:
            out.append(it)
    return out
