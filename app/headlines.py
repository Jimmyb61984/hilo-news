"""
Headline rewriting utilities for balanced, high-quality titles.

Goals:
- Keep the core meaning of the original title.
- Avoid stubby, incomplete headlines and any trailing "…" or "...".
- Remove redundant provider/site suffixes.
- Balance length to a sensible range (defaults: 38–88 chars), without adding ellipses.
- Be idempotent (safe to call repeatedly).
"""

import re
from html import unescape
from typing import Optional

# Visible defaults (tunable via kwargs)
DEFAULT_MIN_LEN = 38
DEFAULT_MAX_LEN = 88
HARD_MIN = 26
HARD_MAX = 96

# Common separators publishers use in titles
SEP_CANDIDATES = (" | ", " - ", " — ", " – ")

# Site/provider hints that often appear inside the title and should be trimmed
PROVIDER_HINTS = (
    "bbc sport", "evening standard", "standard.co.uk", "daily mail", "dailymail",
    "arsenal.com", "arseblog", "pain in the arsenal", "arsenalinsider",
    "the guardian", "telegraph", "independent", "sky sports",
)

TRAILING_WEAK_WORDS = {
    "a","an","the","and","or","but","with","to","of","for","at","on","in","by","as","vs","v",
    "over","after","amid","from","about","into"
}

ELLIPSIS_CHARS = ("…", "...")


def squash_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def strip_html(text: str) -> str:
    # Basic tag stripper (summaries sometimes contain <p> etc.)
    text = re.sub(r"<[^>]+>", " ", text or "")
    return squash_ws(unescape(text))


def strip_provider_suffixes(t: str, provider: Optional[str]) -> str:
    base = t
    # Drop trailing " | Site" / " - Site"
    for sep in SEP_CANDIDATES:
        if sep in base:
            left, right = base.rsplit(sep, 1)
            if any(h in right.lower() for h in PROVIDER_HINTS):
                base = left
                break
    # Also drop explicit provider mentions at the end
    if provider:
        prov = provider.lower()
        base = re.sub(rf"\s*(\||-|—|–)?\s*{re.escape(prov)}\s*$", "", base, flags=re.I)
    return squash_ws(base)


def remove_trailing_ellipsis(t: str) -> str:
    for e in ELLIPSIS_CHARS:
        if t.endswith(e):
            t = t[: -len(e)].rstrip()
    return t


def drop_trailing_weak_fragment(t: str) -> str:
    # If headline ends with weak function word, drop that tail word
    words = t.split()
    if words and words[-1].lower() in TRAILING_WEAK_WORDS:
        words = words[:-1]
        return " ".join(words)
    return t


def first_sentence(text: str) -> str:
    # Take first sentence-like chunk from the summary
    txt = strip_html(text or "")
    m = re.search(r"(.+?)([.!?])(\s|$)", txt)
    return (m.group(1) if m else txt).strip()


def extract_clause(pattern: str, text: str) -> Optional[str]:
    m = re.search(pattern, strip_html(text or ""), flags=re.I)
    if m:
        return squash_ws(m.group(1))
    return None


def tighten(text: str) -> str:
    # Remove bracketed cruft and multiple dashes, then cleanup spaces
    text = re.sub(r"\s*\([^)]*\)\s*", " ", text)
    text = re.sub(r"\s*\[[^\]]*\]\s*", " ", text)
    text = re.sub(r"\s*—\s*|\s*–\s*|\s*-\s*", " – ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Remove trailing punctuation we don't want in headlines
    text = re.sub(r"[–—\-:,;]+$", "", text).strip()
    return text


def fit_to_window(t: str, min_len: int, max_len: int) -> str:
    t = t.strip()
    if len(t) <= max_len:
        return t
    # Trim softly to last full word within bounds, no ellipsis
    short = t[:max_len+1]
    short = re.sub(r"\s+\S*$", "", short).strip()
    # Avoid ultra-short after trim
    if len(short) < max(min_len, HARD_MIN):
        short = t[:max(min_len, HARD_MIN)]
        short = re.sub(r"\s+\S*$", "", short).strip()
    # Remove trailing punctuation
    short = re.sub(r"[–—\-:,;.!?]+$", "", short).strip()
    return short


def build_from_summary(head_stub: str, summary: Optional[str]) -> Optional[str]:
    if not summary:
        return None
    # Prefer "after ..." / "ahead of ..." / "as ..." clauses from summary
    for kw, pat in [
        ("after", r"after\s+([^.,;]+)"),
        ("ahead of", r"ahead of\s+([^.,;]+)"),
        ("as", r"as\s+([^.,;]+)"),
        ("amid", r"amid\s+([^.,;]+)"),
        ("with", r"with\s+([^.,;]+)"),
    ]:
        clause = extract_clause(pat, summary)
        if clause and clause.lower() not in head_stub.lower():
            return f"{head_stub} {kw} {clause}"
    # Else fall back to first sentence of the summary
    s1 = first_sentence(summary)
    if not s1:
        return None
    # Avoid duplicating the stub; take a short tail from the sentence that isn't redundant
    tail = s1
    # Remove any leading repetition of the stub phrase
    if head_stub and tail.lower().startswith(head_stub.lower()):
        tail = tail[len(head_stub):].lstrip(" :–-")
    # Keep it compact
    words = tail.split()
    if len(words) > 14:
        tail = " ".join(words[:14])
    tail = re.sub(r"[.?!]+$", "", tail)
    # Join with an en dash for readability
    return f"{head_stub} – {tail}"


def rewrite_headline(
    title: str,
    provider: Optional[str] = None,
    summary: Optional[str] = None,
    *,
    min_len: int = DEFAULT_MIN_LEN,
    max_len: int = DEFAULT_MAX_LEN,
    hard_min: int = HARD_MIN,
    hard_max: int = HARD_MAX,
) -> str:
    """
    Rewrite a single headline to meet style/length constraints.
    Never raises; falls back to the original on any issue.
    """
    try:
        if not title:
            return title

        t = squash_ws(unescape(title))

        # 1) Remove explicit provider suffixes like " | Daily Mail"
        t = strip_provider_suffixes(t, provider)

        # 2) Remove any trailing ellipsis and weak tail word
        had_ellipsis = t.endswith(ELLIPSIS_CHARS) or any(t.endswith(e) for e in ELLIPSIS_CHARS)
        t = remove_trailing_ellipsis(t)
        t = drop_trailing_weak_fragment(t)

        # 3) If a colon split looks like "Headline: subtitle", keep only the left
        if ":" in t:
            left, right = t.split(":", 1)
            if len(left) >= 24 and (len(right) < len(left) or any(h in right.lower() for h in PROVIDER_HINTS)):
                t = left.strip()

        # 4) If it still looks truncated or too short, try to complete from summary
        if had_ellipsis or len(t) < min_len:
            candidate = build_from_summary(t, summary)
            if candidate:
                t = candidate

        # 5) Tighten punctuation/spacing and trim to hard window
        t = tighten(t)
        t = fit_to_window(t, min_len=min_len, max_len=max_len)

        # 6) Guard rails: ensure within absolute bounds
        if len(t) < hard_min and summary:
            # Ensure we don't return a stub; append a tiny clarifier
            extra = build_from_summary(t, summary) or first_sentence(summary)
            extra = tighten(extra or "")
            if extra and extra.lower() not in t.lower():
                t = fit_to_window(f"{t} – {extra}", min_len=min_len, max_len=min_len)

        if len(t) > hard_max:
            t = fit_to_window(t, min_len=min_len, max_len=hard_max)

        # Final cleanup: no trailing punctuation
        t = re.sub(r"[–—\-:,;.!?]+$", "", t).strip()

        return t or title
    except Exception:
        return title
