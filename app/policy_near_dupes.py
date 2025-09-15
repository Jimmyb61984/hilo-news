from __future__ import annotations
from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta, timezone
import re
import hashlib

# Lightweight, deterministic near-duplicate collapse tuned for match content.

_PREVIEW_KEYS = [
    "preview", "pre-match", "predicted", "lineup", "line-up", "probable",
    "how to watch", "tv channel", "kick-off", "kick off", "odds"
]
_REPORT_KEYS = [
    "report", "match report", "player ratings", "ratings", "reaction",
    "talking points", "what we learned", "five things", "3 things"
]

_KEEP_PRIORITY = {
    # Higher number = keep over others in cluster for that class
    "ArsenalOfficial": 100,
    "EveningStandard": 90,
    "SkySports": 85,
    "DailyMail": 80,
    "Arseblog": 50,
    "PainInTheArsenal": 40,
    "ArsenalInsider": 30,
}

def _parse_dt(s: str) -> datetime:
    try:
        from dateutil import parser as du
        return du.parse(s).astimezone(timezone.utc)
    except Exception:
        return datetime(1970,1,1,tzinfo=timezone.utc)

def _bucket_key(item: Dict[str, Any]) -> Tuple[str, str]:
    """
    Normalize title into a coarse key that groups near-duplicates across providers.
    Removes team names/scores and boilerplate words, lowercases and squashes spaces.
    """
    t = (item.get("title") or "").lower()
    # strip punctuation and common boilerplate
    t = re.sub(r"[\-\–\—_:;|]+", " ", t)
    t = re.sub(r"\b(arsenal|gunners|fc|afc|vs|v|versus|nottingham|forest|athletic|club|bilbao|manchester|city|ucl|champions league)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # coarsen very long keys
    if len(t) > 80:
        t = t[:80]
    # Use a time bucket of 72h to avoid merging across matchdays
    dt = _parse_dt(item.get("publishedUtc") or "")
    day_bucket = (dt - timedelta(hours=dt.hour%24, minutes=dt.minute, seconds=dt.second, microseconds=dt.microsecond)).strftime("%Y-%m-%d")
    return (t, day_bucket)

def _classify(item: Dict[str, Any]) -> str:
    title = (item.get("title") or "").lower()
    def has_any(keys): return any(k in title for k in keys)
    if has_any(_PREVIEW_KEYS): return "preview"
    if has_any(_REPORT_KEYS): return "report"
    return "other"

def _provider_priority(p: str) -> int:
    return _KEEP_PRIORITY.get(p, 10)

def collapse_near_dupes(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Collapse clusters of near-duplicate items across providers, within a short
    temporal window (72h). Rules per product intent:

      - PREVIEW / LINEUPS: keep EveningStandard if present; else highest priority.
      - POST-MATCH (report/ratings/reaction): keep ArsenalOfficial if present;
        else highest priority.
      - OTHER: keep highest priority; prefer entries with imageUrl.
    """
    if not items:
        return items

    # Build clusters
    clusters = {}
    for it in items:
        k = _bucket_key(it)
        clusters.setdefault(k, []).append(it)

    out: List[Dict[str, Any]] = []
    for k, group in clusters.items():
        if len(group) == 1:
            out.append(group[0])
            continue

        # Classify the cluster by majority class, then apply keep rules
        counts = {"preview":0, "report":0, "other":0}
        for it in group:
            counts[_classify(it)] += 1
        cls = max(counts.items(), key=lambda x: x[1])[0]

        # Selection
        def score(it: Dict[str, Any]) -> tuple:
            prov = it.get("provider") or ""
            has_img = 1 if it.get("imageUrl") else 0
            pri = _provider_priority(prov)
            # Prefer newer slightly inside the cluster, then provider priority, then image, then title asc
            return (_parse_dt(it.get("publishedUtc") or ""), pri, has_img, -(len(it.get("title") or "")))

        keep: Dict[str, Any] = None
        if cls == "preview":
            # Prefer EveningStandard if present
            candidates = [x for x in group if (x.get("provider") == "EveningStandard")]
            keep = max(candidates, key=score) if candidates else max(group, key=score)
        elif cls == "report":
            # Prefer ArsenalOfficial if present
            candidates = [x for x in group if (x.get("provider") == "ArsenalOfficial")]
            keep = max(candidates, key=score) if candidates else max(group, key=score)
        else:
            keep = max(group, key=score)

        out.append(keep)

    return out
