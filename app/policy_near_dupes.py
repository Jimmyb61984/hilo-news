from __future__ import annotations
from typing import List, Dict, Any, Tuple, Optional
from datetime import datetime, timedelta, timezone
import re

# Lightweight, deterministic near-duplicate collapse tuned for match content.

# Expanded keyword sets to catch more phrasing variants
_PREVIEW_KEYS = [
    "preview", "pre-match", "prematch", "match preview",
    "predicted", "prediction", "predicted xi", "predicted lineup",
    "lineup", "line up", "line-up", "lineups", "line-ups",
    "xi", "starting xi", "team news", "confirmed xi",
    "how to watch", "tv channel", "kick-off", "kick off", "odds"
]
_REPORT_KEYS = [
    "report", "match report", "full-time", "full time",
    "player ratings", "ratings", "reaction", "post-match", "post match",
    "talking points", "what we learned", "five things", "5 things", "3 things"
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

# Stronger normalization for clustering similar headlines
_NORMALIZE_DROP_WORDS = [
    # boilerplate / generic
    "arsenal", "gunners", "fc", "afc", "ucl", "champions league",
    "premier league", "pl", "vs", "v", "versus",
    # preview/report phrasing that we don't want to fragment clusters
    "match preview", "preview", "pre match", "pre-match", "prediction",
    "predicted", "predicted xi", "predicted lineup", "team news",
    "lineup", "line up", "line-up", "lineups", "line-ups", "xi",
    "starting xi", "confirmed xi", "how to watch", "tv channel",
    "kick-off", "kick off", "odds",
    "match report", "report", "full time", "full-time",
    "player ratings", "ratings", "reaction", "post match", "post-match",
    "talking points", "what we learned", "five things", "5 things", "3 things"
]

def _normalized_title_key(title: str) -> str:
    t = (title or "").lower()
    # unify punctuation to space
    t = re.sub(r"[^\w\s]", " ", t)
    # drop common/boilerplate words
    words = [w for w in t.split() if w not in _NORMALIZE_DROP_WORDS]
    t = " ".join(words)
    # collapse digits that often denote dates/scores/times
    t = re.sub(r"\b\d{1,2}[:\-]\d{1,2}\b", " ", t)   # scores / times like 2-1 / 20:00
    t = re.sub(r"\b\d{4}\b", " ", t)                 # years
    t = re.sub(r"\s+", " ", t).strip()
    # coarsen very long keys
    if len(t) > 80:
        t = t[:80]
    return t

# --- NEW: Opponent-aware fixture key for previews/reports ---------------------

TEAM_ALIASES = {
    # Spain
    "athletic club": "ATH",
    "athletic bilbao": "ATH",
    "ath bilbao": "ATH",
    "bilbao": "ATH",
    # England (examples; extend as needed)
    "nottingham forest": "NFO",
    "forest": "NFO",
    "manchester city": "MCI",
    "man city": "MCI",
    "city": "MCI",
    "manchester united": "MUN",
    "man united": "MUN",
    "man utd": "MUN",
    "chelsea": "CHE",
    "tottenham": "TOT",
    "spurs": "TOT",
    "liverpool": "LIV",
    "newcastle": "NEW",
    "west ham": "WHU",
    # Add more aliases over time as needed
}

def _normalize_opponent(text: str) -> Optional[str]:
    t = (text or "").lower()
    # Strip punctuation to make alias matching easier
    t = re.sub(r"[^\w\s]", " ", t)

    # Patterns:
    # "Arsenal vs X" | "Arsenal v X" | "Arsenal at X" | "Arsenal @ X"
    m = re.search(r"\barsenal\s+(?:vs|v|versus|at|@)\s+([a-z\s]+)\b", t)
    if not m:
        # Or "X vs Arsenal"
        m = re.search(r"\b([a-z\s]+)\s+(?:vs|v|versus)\s+arsenal\b", t)
    if not m:
        return None

    raw = m.group(1).strip()
    raw = re.sub(r"\s+", " ", raw)

    # Direct alias
    if raw in TEAM_ALIASES:
        return TEAM_ALIASES[raw]

    # Fuzzy-ish: choose the longest alias contained in raw
    best = None
    for alias, code in TEAM_ALIASES.items():
        if alias in raw:
            if best is None or len(alias) > len(best[0]):
                best = (alias, code)
    if best:
        return best[1]

    # Fallback: coarse slug of first 2 tokens as a stable key
    tokens = raw.split()
    if tokens:
        return "UNK:" + "-".join(tokens[:2])
    return None

def _bucket_key(item: Dict[str, Any]) -> Tuple[str, str]:
    """
    Use a fixture-aware key for previews/reports:
      (class, opponent_key, day_bucket) when opponent can be parsed,
    else fallback to normalized-title clustering.
    """
    title = item.get("title") or ""
    dt = _parse_dt(item.get("publishedUtc") or "")
    day_bucket = (dt - timedelta(hours=dt.hour%24, minutes=dt.minute, seconds=dt.second, microseconds=dt.microsecond)).strftime("%Y-%m-%d")

    cls = _classify(item)
    if cls in ("preview", "report"):
        text = " ".join([title, item.get("summary") or "", item.get("url") or ""])
        opp = _normalize_opponent(text)
        if opp:
            return (f"{cls}:{opp}", day_bucket)

    # Fallback for other content or if opponent not found
    t = _normalized_title_key(title)
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
    Collapse clusters of near-duplicate items across providers, within ~72h.
      - PREVIEW / LINEUPS: keep EveningStandard if present; else highest priority.
      - POST-MATCH (report/ratings/reaction): keep ArsenalOfficial if present;
        else highest priority official/tier-1.
      - OTHER: keep highest provider priority; prefer entries with imageUrl then newer.
    """
    if not items:
        return items

    # Build clusters
    clusters: Dict[Tuple[str,str], List[Dict[str, Any]]] = {}
    for it in items:
        k = _bucket_key(it)
        clusters.setdefault(k, []).append(it)

    out: List[Dict[str, Any]] = []
    for _, group in clusters.items():
        if len(group) == 1:
            out.append(group[0])
            continue

        # Classify cluster by majority type
        counts = {"preview":0, "report":0, "other":0}
        for it in group:
            counts[_classify(it)] += 1
        cls = max(counts.items(), key=lambda x: x[1])[0]

        def score(it: Dict[str, Any]) -> tuple:
            prov = it.get("provider") or ""
            pri = _provider_priority(prov)
            has_img = 1 if it.get("imageUrl") else 0
            dt = _parse_dt(it.get("publishedUtc") or "")
            # Prefer provider priority, then image presence, then recency, then shorter title
            return (pri, has_img, dt, -(len(it.get("title") or "")))

        if cls == "preview":
            candidates = [x for x in group if (x.get("provider") == "EveningStandard")]
            keep = max(candidates, key=score) if candidates else max(group, key=score)
        elif cls == "report":
            candidates = [x for x in group if (x.get("provider") == "ArsenalOfficial")]
            keep = max(candidates, key=score) if candidates else max(group, key=score)
        else:
            keep = max(group, key=score)

        out.append(keep)

    return out

