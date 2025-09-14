from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
from typing import Dict, List, Tuple

# ---- tuneable knobs ----
WHITELIST = {
    "ArsenalOfficial",
    "Arseblog",
    "PainInTheArsenal",
    "ArsenalInsider",
    "DailyMail",
    "EveningStandard",
    "SkySports",
}
# keep PITA but cap it hard to avoid flooding until quality scoring arrives
SOURCE_CAPS = defaultdict(lambda: 3, {
    "ArsenalOfficial": 8,
    "Arseblog": 6,
    "PainInTheArsenal": 3,
    "ArsenalInsider": 3,
    "DailyMail": 4,
    "EveningStandard": 4,
    "SkySports": 4,
})

WOMEN_PATTERNS = [
    r"/women(/|$)", r"\bArsenal Women\b", r"\bWFC\b"
]
YOUTH_PATTERNS = [r"\bU18\b", r"\bU21\b", r"\bUnder-?18\b", r"\bUnder-?21\b"]

norm_gap = re.compile(r"\s+")

def _is_women(item) -> bool:
    u = (item.get("url") or "").lower()
    t = (item.get("title") or "")
    s = (item.get("summary") or "")
    for pat in WOMEN_PATTERNS:
        if re.search(pat, u, re.I) or re.search(pat, t, re.I) or re.search(pat, s, re.I):
            return True
    return False

def _is_youth(item) -> bool:
    blob = " ".join([item.get("url",""), item.get("title",""), item.get("summary","")])
    for pat in YOUTH_PATTERNS:
        if re.search(pat, blob, re.I):
            return True
    return False

def _norm_title(title: str) -> str:
    t = (title or "").lower().strip()
    t = re.sub(r"[-–—:|•]+", " ", t)
    t = norm_gap.sub(" ", t)
    return t

def _iso_to_dt(iso: str) -> datetime | None:
    try:
        # strict Z/offset handling
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return None

def _sort_key(item):
    dt = _iso_to_dt(item.get("publishedUtc") or "")
    return dt or datetime(1970,1,1, tzinfo=timezone.utc)

def _debug_counts(items: List[dict]) -> Dict:
    c = Counter([i.get("source") or ""] for i in items)
    return dict(sorted(c.items(), key=lambda kv: (-kv[1], kv[0])))

def apply_policy(items: List[dict], team_code: str = "ARS") -> List[dict]:
    filtered, seen_titles = [], set()
    drops = Counter()

    # Pre-scan: remove obvious junk + wrong team + wrong source
    for it in items:
        # source allowlist if present
        src = it.get("source") or ""
        if src and WHITELIST and src not in WHITELIST:
            drops["not_whitelisted"] += 1
            continue

        # team gate (if item carries teams)
        teams = it.get("teams") or []
        if teams and team_code and team_code not in teams:
            drops["wrong_team"] += 1
            continue

        # Women / Youth
        if _is_women(it):
            drops["women"] += 1
            continue
        if _is_youth(it):
            drops["youth"] += 1
            continue

        # dedupe by softened title
        nt = _norm_title(it.get("title",""))
        if nt and nt in seen_titles:
            drops["dedupe"] += 1
            continue
        if nt:
            seen_titles.add(nt)

        filtered.append(it)

    # Per-source caps
    capped, per_src = [], Counter()
    for it in sorted(filtered, key=_sort_key, reverse=True):
        src = it.get("source") or ""
        cap = SOURCE_CAPS[src]
        if per_src[src] >= cap:
            drops["cap:"+src] += 1
            continue
        per_src[src] += 1
        capped.append(it)

    # final chronological order
    capped.sort(key=_sort_key, reverse=True)
    return capped

def apply_policy_with_stats(items: List[dict], team_code: str = "ARS") -> Tuple[List[dict], Dict]:
    pre = list(items)
    before = _debug_counts(pre)
    out = apply_policy(pre, team_code=team_code)
    after = _debug_counts(out)
    stats = {
        "preCount": len(pre),
        "postCount": len(out),
        "sourcesBefore": before,
        "sourcesAfter": after,
    }
    return out, stats
