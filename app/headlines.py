# app/headlines.py
# Purpose: rewrite incoming headlines to be concise, specific, and complete
# without stubby endings or ellipses. Safe, dependency-free, and importable
# as: from app.headlines import rewrite_headline

from __future__ import annotations
import re
from typing import Optional

# Words a clean headline must NOT end with (lowercased)
_BAD_END_WORDS = {
    "of","for","to","from","with","by","as","at","on","in","into","over","under",
    "about","after","before","ahead","ahead of","because","since","than","that",
    "and","or","but","so","if","when","while","without","within"," versus","vs",
    "according","report","reports","breaking","exclusive"
}

# Trailing punctuation to strip
_BAD_TRAIL_PUNCT = tuple([":",";","-","–","—","/","\\","…","...","(", "[", "{", ","])

# Soft filler / clickbait phrases to reduce
_CLICKBAIT_SUBS = [
    (r"\bjust\b", ""),  # "just"
    (r"\breally\b", ""),  # "really"
    (r"\bvery\b", ""),   # "very"
    (r"\byou won['’]t believe\b", ""),
    (r"\bwhat(?:’|')s (?:just )?been said\b", ""),
    (r"\bwhat happened\b", ""),
    (r"\bfans will be furious\b", "controversial decision"),
    (r"\bcrystal clear\b", "clear"),
    (r"\bbursts? out laughing\b", "laughs"),
    (r"\bset to\b", "to"),
]

_ELIPSIS_RX = re.compile(r"(?:\u2026|\.{3,})")
_WS_RX = re.compile(r"\s+")
_QUOTES_EDGE_RX = re.compile(r'^[\s\"“”\'‘’]+|[\s\"“”\'‘’]+$')

_COLON_SPLIT_RX = re.compile(r"\s*:\s*")

# If a provider is helpful later (e.g., stricter rules for fan sites)
_FAN_SITES = {
    "paininthearsenal","arseblog","arsenalinsider","pain in the arsenal","arsenal insider"
}

def _clean_text(s: str) -> str:
    s = s.strip()
    s = _ELIPSIS_RX.sub(" ", s)  # remove ellipses
    s = _WS_RX.sub(" ", s)
    s = _QUOTES_EDGE_RX.sub("", s)
    # Remove dangling punctuation at the end
    while s and (s.endswith(_BAD_TRAIL_PUNCT) or s.endswith(" ")):
        s = s[:-1].rstrip()
    return s

def _ends_badly(s: str) -> bool:
    if not s: 
        return True
    tail = s.split()[-1].lower()
    if tail in _BAD_END_WORDS:
        return True
    # Also treat connectors like "of/for/ahead of" at the end
    if len(s.split()) >= 2:
        last2 = " ".join(s.split()[-2:]).lower()
        if last2 in _BAD_END_WORDS:
            return True
    # Avoid trailing non-alnum
    if not s[-1].isalnum():
        return True
    return False

def _apply_clickbait_subs(s: str) -> str:
    out = s
    for pat, rep in _CLICKBAIT_SUBS:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)
    # collapse spaces again
    out = _WS_RX.sub(" ", out).strip()
    return out

def _merge_colon_parts(s: str) -> str:
    """
    Many feeds are 'Topic: detail'. If shortening, prefer a single complete clause.
    Strategy: keep both, joined by ' — ', but allow later trimming rules to shorten.
    """
    parts = _COLON_SPLIT_RX.split(s, maxsplit=1)
    if len(parts) == 2:
        left, right = parts
        left, right = left.strip(), right.strip()
        # If left is too short or ends badly, prefer right; else join
        if _ends_badly(left) and right:
            return right
        if right:
            return f"{left} — {right}"
    return s

def _smart_shorten(s: str, max_words: int = 14, max_chars: int = 90) -> str:
    """
    Shorten without leaving stubby endings. Prefer word-limit first, then char-limit.
    Never end on a connector/preposition.
    """
    s = s.strip()
    words = s.split()
    if len(words) <= max_words and len(s) <= max_chars:
        return s

    # First, try removing parentheticals/brackets which are often tangential
    s2 = re.sub(r"\s*[\(\[][^\)\]]{0,80}[\)\]]", "", s).strip()
    s2 = _WS_RX.sub(" ", s2)
    if len(s2.split()) <= max_words and len(s2) <= max_chars and not _ends_badly(s2):
        return s2

    # If still too long, clip by words but keep completeness
    trimmed = words[:max_words]
    # Walk backwards removing bad tails
    while trimmed and (len(" ".join(trimmed)) > max_chars or _ends_badly(" ".join(trimmed))):
        trimmed = trimmed[:-1]

    out = " ".join(trimmed).strip()
    # If we over-trimmed, fall back to char-based but ensure clean ending
    if not out:
        out = s[:max_chars].rstrip()
        # remove partial trailing word
        out = re.sub(r"\W+\w?$", "", out).strip()

    # Final polish
    out = _clean_text(out)
    return out

def _normalize_provider(p: Optional[str]) -> str:
    if not p:
        return ""
    p = p.strip().lower()
    return p

def rewrite_headline(title: str, provider: Optional[str] = None) -> str:
    """
    Public API. Safe to import from app.main without circulars.
    Rules:
      - sanitize/normalize
      - merge colon parts sensibly
      - de-clickbait
      - length balance (words 7–14 / ~45–90 chars target)
      - never end on connector/preposition or punctuation/ellipsis
    """
    if not title or not isinstance(title, str):
        return ""

    prov = _normalize_provider(provider)

    t = title.strip()
    t = _clean_text(t)

    # If the title contains a colon, merge intelligently
    t = _merge_colon_parts(t)

    # Light de-clickbait pass (stronger for known fan sites)
    t = _apply_clickbait_subs(t)
    if prov in _FAN_SITES:
        # a slightly stronger second pass for fan sites
        t = _apply_clickbait_subs(t)

    # If it's already in a good length window and ends cleanly, keep it
    if 45 <= len(t) <= 90 and not _ends_badly(t):
        return t

    # If it's too short but ends badly (rare), try to keep more after colon if any
    if len(t) < 45 and _COLON_SPLIT_RX.search(title):
        t2 = _merge_colon_parts(title)  # try again from the original
        t2 = _apply_clickbait_subs(_clean_text(t2))
        if 45 <= len(t2) <= 90 and not _ends_badly(t2):
            return t2

    # Otherwise, smart shorten (or lightly expand via colon join already done)
    t = _smart_shorten(t, max_words=14, max_chars=90)

    # Ensure minimum heft—if still too short but clean, keep it (don’t pad with fluff)
    # Final clean
    t = _clean_text(t)

    # Guard against trailing connectors again
    if _ends_badly(t):
        # Best effort: remove the last weak word
        parts = t.split()
        while parts and parts[-1].lower() in _BAD_END_WORDS:
            parts.pop()
        t = _clean_text(" ".join(parts))

    return t
