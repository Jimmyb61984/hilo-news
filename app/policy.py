# app/policy.py
from __future__ import annotations

from typing import List, Dict, Any

# ---- Team aliases (lightweight; mirror of main) ----
_TEAM_ALIASES = {
    "ARS": ["Arsenal", "Gunners", "AFC", "Arsenal FC"],
}

def _aliases_for(code: str) -> List[str]:
    return _TEAM_ALIASES.get(code.upper(), [code.upper()])

# ---- Content gates ----
_WOMEN_KEYS: List[str] = [
    "women", "womens", "women’s", "women's", "awfc", "wfc",
    "fa wsl", "wsl", "barclays women's", "women's super league",
    "/women/", "/wsl/", "/awfc/", "/women-", "/womens-",
    "arsenal women", "arsenal-women", "arsenalwomen",
]

# ban highlight-style items from the **news** feed (they live in your highlights area)
_HIGHLIGHT_KEYS: List[str] = [
    "highlights:", "highlights -", " highlights ", "match highlights",
    "full match replay", "watch a full match", "replay:", "replay / vod",
    "watch the full match", "extended highlights", "short highlights", "ref cam",
    "gallery:", "photo gallery", " photos ", "images from", "in pictures",
]

# live blogs / recaps don’t belong in the evergreen news list
_LIVE_BLOG_KEYS: List[str] = [
    "live blog", "as it happened", "minute-by-minute", "min-by-min",
    "live result", "recap:", "recap /", "live updates",
]

# general-press domains where we require an Arsenal mention
_REQUIRE_ARSENAL_MENTION = {"DailyMail", "TheTimes", "TheStandard", "SkySports"}

def _contains_any(text: str, keys: List[str]) -> bool:
    t = (text or "").lower()
    return any(k in t for k in keys)

def _is_womens(item: Dict[str, Any]) -> bool:
    blob = f"{item.get('title','')} {item.get('summary','')} {item.get('url','')}"
    return _contains_any(blob, _WOMEN_KEYS)

def _is_highlight_like(item: Dict[str, Any]) -> bool:
    blob = f"{item.get('title','')} {item.get('summary','')}"
    url = (item.get("url") or "").lower()
    return _contains_any(blob, _HIGHLIGHT_KEYS) or _contains_any(url, ["-highlights", "/highlights/", "/gallery/"])

def _is_liveblog_like(item: Dict[str, Any]) -> bool:
    blob = f"{item.get('title','')} {item.get('summary','')}"
    return _contains_any(blob, _LIVE_BLOG_KEYS)

def _is_relevant_to_arsenal(item: Dict[str, Any], team_code: str) -> bool:
    aliases = _aliases_for(team_code)
    text = f"{item.get('title','')} {item.get('summary','')}".lower()
    url = (item.get('url') or "").lower()
    if any(a.lower() in text for a in aliases):
        return True
    if any(a.lower().replace(" ", "-") in url for a in aliases):
        return True
    return False

def apply_policy(items: List[Dict[str, Any]], *, team_code: str = "ARS") -> List[Dict[str, Any]]:
    """Apply business rules to a flat list of items. Keep this **pure** and side-effect free."""
    out: List[Dict[str, Any]] = []
    for it in items:
        # Men-only
        if _is_womens(it):
            continue

        # Remove highlight/gallery/replay/live-blog items from the **news** feed
        if _is_highlight_like(it) or _is_liveblog_like(it):
            continue

        # For big general-press domains, demand explicit Arsenal relevance
        src = (it.get("source") or "").strip()
        if src in _REQUIRE_ARSENAL_MENTION and not _is_relevant_to_arsenal(it, team_code):
            continue

        out.append(it)

    return out
