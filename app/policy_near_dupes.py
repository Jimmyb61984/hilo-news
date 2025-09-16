from __future__ import annotations
from typing import List, Dict, Any
import re
from collections import defaultdict

# Very light-weight text cleaner
def _clean(txt: str) -> str:
    return re.sub(r"\s+", " ", (txt or "").strip().lower())

# Expanded synonym sets to group fixture-centric pieces better
_PRE_HINTS = [
    "preview", "predicted lineup", "predicted line-up", "probable xi",
    "team news", "how arsenal could line up", "three ways arsenal could line up",
    "talking points", "what to expect", "keys to",
]
_POST_HINTS = [
    "match report", "player ratings", "post-match", "what we learned",
    "takeaways", "reaction", "analysis",
]

# Very gentle: only use when titles clearly overlap on the same opponent/fixture
_OPPONENT_REGEX = re.compile(r"\bv(?:s|ersus)\b|\bvs\.\b|\bathletic club\b|\bbilbao\b|\bnottingham forest\b|\bman chester\b|\bman city\b|\bchelsea\b|\btottenham\b|\bspurs\b")

def _is_fixtureish(title: str) -> bool:
    t = _clean(title)
    return bool(_OPPONENT_REGEX.search(t)) or "arsenal" in t

def _kind(title: str, summary: str) -> str:
    s = _clean(title + " " + summary)
    if any(h in s for h in _POST_HINTS):
        return "post"
    if any(h in s for h in _PRE_HINTS):
        return "pre"
    return "other"

def collapse_near_dupes(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Cluster highly similar pre/post pieces around a fixture and keep
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
        sig = _clean(re.sub(r"\barsenal\b|\bgunners\b", "", title))
        sig = re.sub(r"[^a-z0-9 ]+", "", sig)[:80]
        buckets[(sig, k)].append(it)

    # For each bucket, choose best item
    keep_ids = set()
    for (_, _k), group in buckets.items():
        if not group:
            continue
        # rank: official > has image > longer title
        def _rank(x: Dict[str, Any]) -> tuple:
            prov = (x.get("provider") or "").strip()
            is_official = 1 if prov in {"EveningStandard", "DailyMail"} else 0
            has_img = 1 if x.get("imageUrl") else 0
            return (is_official, has_img, len(x.get("title") or ""))
        best = sorted(group, key=_rank, reverse=True)[0]
        keep_ids.add(best.get("id") or best.get("url"))

    # Filter: keep chosen in buckets, everything else untouched
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
