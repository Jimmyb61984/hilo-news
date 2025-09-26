from __future__ import annotations

import re
from typing import Optional

# Heuristics for balanced, professional headlines (no ellipses, not stubby).
MIN_CHARS = 38
MAX_CHARS = 88
MIN_WORDS = 6
MAX_WORDS = 18

# Light list of boilerplate phrases to strip
TRAILING_PHRASES = [
    r"\s*-\s*Arsenal\s*$",
    r"\s*-\s*The\s+Arsenal\s*$",
    r"\s*\|\s*Arsenal\s*$",
    r"\s*-\s*Official\s*$",
    r"\s*-\s*Latest\s+News\s*$",
    r"\s*-\s*Football\s*$",
]

PARENS_RE = re.compile(r"\s*\([^)]*\)")
BRACKETS_RE = re.compile(r"\s*\[[^\]]*\]")
DUP_SPACES_RE = re.compile(r"\s{2,}")
ELLIPSES_RE = re.compile(r"\s*\u2026|\s*\.{3}\s*$")
QUOTE_TRIM_RE = re.compile(r"^[\"'‘’“”]+|[\"'‘’“”]+$")

def _collapse_ws(s: str) -> str:
    return DUP_SPACES_RE.sub(" ", s).strip()

def _strip_source_suffix(title: str) -> str:
    # Remove typical " - Source" suffixes without touching hyphenated words.
    parts = title.rsplit(" - ", 1)
    if len(parts) == 2 and len(parts[1].split()) <= 4:
        return parts[0]
    return title

def _remove_brackets(title: str) -> str:
    title = PARENS_RE.sub("", title)
    title = BRACKETS_RE.sub("", title)
    return _collapse_ws(title)

def _first_clause(title: str) -> str:
    # Keep up to the first sentence-ish boundary, but no ellipses.
    for sep in (": ", " – ", " — ", " - ", "; ", ". "):
        if sep in title:
            return title.split(sep)[0]
    return title

def _clean_base(title: str) -> str:
    t = title.strip()
    t = QUOTE_TRIM_RE.sub("", t)
    t = _strip_source_suffix(t)
    t = _remove_brackets(t)
    t = ELLIPSES_RE.sub("", t)  # remove trailing ...
    for pat in TRAILING_PHRASES:
        t = re.sub(pat, "", t, flags=re.IGNORECASE)
    t = _collapse_ws(t)
    return t

def _truncate_neatly(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    # Cut at a word boundary
    cut = text[:limit+1]
    cut = re.sub(r"\W+\w*$", "", cut)  # drop last partial word
    # Avoid stubby endings like 'and', 'but', 'after'
    cut = re.sub(r"(?:\b(and|but|or|after|as|with|for|to|of|at|in))\s*$", "", cut, flags=re.IGNORECASE)
    return cut

def _expand_with_summary(base: str, summary: Optional[str], team: Optional[str]) -> str:
    """If the title is too short, carefully add one key detail from the summary."""
    if not summary:
        return base
    # Extract a short informative fragment from the summary.
    txt = re.sub(r"<[^>]+>", "", summary)  # drop html
    txt = _collapse_ws(txt)
    # prefer up to first sentence
    m = re.split(r"[.?!]\s+", txt)
    frag = m[0] if m else txt
    frag = _truncate_neatly(frag, 48)
    if frag and len(f"{base}: {frag}") <= MAX_CHARS:
        # Avoid repeating words
        if frag.lower() not in base.lower():
            return f"{base}: {frag}"
    # Otherwise just return base
    return base

def _enforce_bounds(title: str, summary: Optional[str], team: Optional[str]) -> str:
    t = title
    if len(t) < MIN_CHARS or len(t.split()) < MIN_WORDS:
        t = _expand_with_summary(t, summary, team)
    if len(t) > MAX_CHARS or len(t.split()) > MAX_WORDS:
        t = _truncate_neatly(t, MAX_CHARS)
    # Final cleanup—no ellipses, tidy whitespace
    t = ELLIPSES_RE.sub("", t)
    t = _collapse_ws(t)
    return t

def rewrite_headline(provider: str, title: str, summary: Optional[str] = None, team: Optional[str] = None) -> str:
    """
    Produce a concise, professional headline:
    - No ellipses
    - Avoid incomplete stubs
    - Balanced length (≈38–88 chars, 6–18 words)
    - Preserve meaning; if already good, return original
    """
    if not title:
        return title

    base = _clean_base(title)

    # If base already looks good, lightly bound-check and return
    candidate = _enforce_bounds(base, summary, team)

    # Guard against becoming *shorter* than original in a bad way
    if len(candidate) < max(len(base) - 6, MIN_CHARS - 4):
        candidate = base

    # Never add punctuation at the end, and never ellipses
    candidate = candidate.rstrip(".!?:; \u2026")
    candidate = _collapse_ws(candidate)

    return candidate

