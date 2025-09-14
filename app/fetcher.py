from __future__ import annotations
import re
from datetime import datetime, timezone
from typing import Optional
from bs4 import BeautifulSoup
import json

# --- NEW HELPERS ---

_ISO_RX = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+\-]\d{2}:\d{2})")

def _to_utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

def _extract_arsenal_published(html: str) -> Optional[str]:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # 1) meta og/article
    meta = soup.find("meta", attrs={"property": "article:published_time"})
    if meta and meta.get("content") and _ISO_RX.search(meta["content"]):
        return _ISO_RX.search(meta["content"]).group(0)

    # 2) <time datetime=...>
    t = soup.find("time", attrs={"datetime": True})
    if t:
        dt = t.get("datetime")
        if dt and _ISO_RX.search(dt):
            return _ISO_RX.search(dt).group(0)

    # 3) JSON-LD
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        # can be dict or list
        blocks = data if isinstance(data, list) else [data]
        for b in blocks:
            val = b.get("datePublished") or b.get("dateCreated")
            if isinstance(val, str):
                m = _ISO_RX.search(val)
                if m:
                    return m.group(0)
    return None

def ensure_arsenal_publish_time(item: dict, detail_html: str) -> bool:
    """
    Set item['publishedUtc'] from article page for ArsenalOfficial.
    Returns True if set; False if not found (caller may drop item to protect chronology).
    """
    if not item or not (item.get("source") == "ArsenalOfficial" or "arsenal.com" in (item.get("url") or "")):
        return True  # not applicable

    found = _extract_arsenal_published(detail_html or "")
    if not found:
        return False
    # normalize to Z
    try:
        # fromisoformat needs offset form; ensure Z→+00:00 for parsing then back to Z
        iso = found.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        item["publishedUtc"] = _to_utc_iso(dt)
        return True
    except Exception:
        return False

