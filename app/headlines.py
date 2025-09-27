# app/headlines.py
# High-standard headline normalizer for Hilo News
# - No ellipses or dangling endings
# - Balanced length (~48–96 chars)
# - If a source title is incomplete/clickbaity, rebuild from the first clean summary sentence
# - Standalone (no app imports)

from __future__ import annotations
import re
from html import unescape

MIN_LEN = 48
MAX_LEN = 96

ELLIPSIS_RE = re.compile(r"(…|\.\.\.)")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
READMORE_RE = re.compile(r"(READ MORE:.*)$", re.I)
APPEARED_FIRST_ON_RE = re.compile(r"The post .* appeared first on .*", re.I)

# Bad endings
DANGLING_WORDS = {
    "after","as","with","for","to","and","or","amid","vs","v","versus",
    "because","while","when","over","before","following","from","at","by","on","off"
}
DANGLING_PUNCT = (":",";","-","–","—","/")

# Heuristic verb list for “complete thought” detection
VERB_TOKENS = {
    "is","are","was","were","be","being","been",
    "wins","beats","edges","secures","agrees","signs","extends","pens","joins","loans",
    "appoints","warns","confirms","hopes","pays","dies","suffers","hands","boosts","urges",
    "calls","backs","rules","axes","sacks","fires","names","sets","faces","returns","misses",
    "seals","draws","eyes","targets","recalls","admits","says","tells","laughs","hints","needs",
    "braces","clinches","earns"
}

CLICKBAIT_PATTERNS = [
    r"will be furious after hearing what.*",   # “fans will be furious…”
    r"bursts out laughing after hearing what.*",
    r"after hearing what's just been said about.*",
    r"you won't believe.*",
    r"what happened.*",
    r"ahead of$",
]

def _clean_text(s: str) -> str:
    s = unescape(str(s or ""))
    s = TAG_RE.sub(" ", s)
    s = WS_RE.sub(" ", s).strip()
    return s

def _strip_ellipses(s: str) -> str:
    s = ELLIPSIS_RE.sub("", s)
    s = re.sub(r"\s+([:;,\-–—/])$", "", s).strip()
    return s

def _remove_trailing_dangling(s: str) -> str:
    s = s.strip()
    # strip dangling punctuation
    while any(s.endswith(p) for p in DANGLING_PUNCT):
        s = s[:-1].rstrip()
    # strip dangling stopwords
    words = s.split()
    while words and words[-1].lower().strip("'’\"") in DANGLING_WORDS:
        words.pop()
    s = " ".join(words).strip()
    # final cleanup
    s = re.sub(r"[\s:;,\-–—/]+$", "", s).strip()
    return s

def _first_sentence(text: str) -> str:
    text = _clean_text(text)
    # remove boilerplate
    text = READMORE_RE.sub("", text).strip()
    text = APPEARED_FIRST_ON_RE.sub("", text).strip()
    text = re.sub(r"Photo by .*", "", text, flags=re.I).strip()
    # split on sentence boundary
    parts = re.split(r"(?<=[.!?])\s+", text)
    cand = (parts[0] if parts else text).strip()
    # remove trailing period in headline style
    cand = cand[:-1].strip() if cand.endswith((".", "…")) else cand
    return cand

def _looks_incomplete(t: str) -> bool:
    if not t:
        return True
    if any(re.search(pat, t, re.I) for pat in CLICKBAIT_PATTERNS):
        return True
    if any(t.endswith(p) for p in DANGLING_PUNCT):
        return True
    last = t.split()[-1].lower().strip("'’\"") if t.split() else ""
    if last in DANGLING_WORDS:
        return True
    # no verb and very short often means a fragment
    if len(t) < MIN_LEN and not any(re.search(rf"\b{v}\b", t, re.I) for v in VERB_TOKENS):
        return True
    return False

def _shorten_at_word_boundary(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    cut = s[:limit].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip()
    cut = re.sub(r"[\s:;,\-–—/]+$", "", cut)
    return cut

def _squeeze(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    # drop parentheticals first
    s1 = re.sub(r"\s*\([^)]*\)", "", s).strip()
    if len(s1) <= limit:
        return s1
    s = s1
    # prune right clause after separators
    for sep in (" — ", " – ", " - ", ": "):
        if sep in s:
            left, right = s.split(sep, 1)
            left = left.strip()
            if len(left) > limit:
                return _shorten_at_word_boundary(left, limit)
            remain = limit - len(left) - len(sep)
            if remain > 8:
                right_short = _shorten_at_word_boundary(right.strip(), remain)
                return f"{left}{sep}{right_short}".strip()
            return left
    # blunt cut
    return _shorten_at_word_boundary(s, limit)

def _build_from_summary(summary: str, subject_hint: str | None = None) -> str:
    sent = _first_sentence(summary or "")
    if not sent:
        return ""
    # soften tabloid phrasing from summaries
    sent = re.sub(r"\bwill be furious\b", "are furious", sent, flags=re.I)
    sent = re.sub(r"\bwill be happy\b", "is encouraged", sent, flags=re.I)
    sent = re.sub(r"\baccording to\b.*", "", sent, flags=re.I).strip()

    # If we have a subject hint and it isn’t present, prepend it when short
    if subject_hint and subject_hint.lower() not in sent.lower() and len(sent) < MAX_LEN - len(subject_hint) - 3:
        sent = f"{subject_hint}: {sent}"

    # make sure no ellipses / dangling
    sent = _strip_ellipses(sent)
    sent = _remove_trailing_dangling(sent)
    return sent

def _normalize_lite(t: str) -> str:
    repl = [
        (r"\s{2,}", " "),
        (r"\bManchester City\b", "Man City"),
        (r"\bSaint James[’']? Park\b", "St James’ Park"),
        (r"\s+-\s+", " — "),  # prefer em dash over hyphen for clause join
    ]
    for pat, rpl in repl:
        t = re.sub(pat, rpl, t)
    return t.strip()

def rewrite_headline(title: str, provider: str | None = None, summary: str | None = None) -> str:
    """
    Deterministic, conservative rewrite.
    - Uses source title if it's clean and complete.
    - Otherwise constructs a clean, complete line from the summary's first sentence.
    """
    t = _clean_text(title)
    t = _strip_ellipses(t)

    # If title is incomplete or clickbaity, prefer summary sentence
    if _looks_incomplete(t) and summary:
        # Use the title as a subject hint when helpful (e.g., contains a proper name/club)
        hint = None
        # extract a simple subject hint (first 4 words not ending in dangling terms)
        tokens = [w for w in t.split() if w]
        if tokens:
            # drop trailing dangling words
            while tokens and tokens[-1].lower().strip("'’\"") in DANGLING_WORDS:
                tokens.pop()
            hint = " ".join(tokens[:4]).strip(":;—- ")
            hint = hint if hint and len(hint) >= 3 else None

        t = _build_from_summary(summary, subject_hint=hint)

    # If still empty (no summary), fall back to cleaned title
    if not t:
        t = _remove_trailing_dangling(_clean_text(title))

    t = _normalize_lite(t)

    # Length balancing
    if len(t) < MIN_LEN and summary:
        # Extend using a concise clause from summary
        add = _first_sentence(summary)
        # avoid duplication
        if add and add.lower() not in t.lower():
            sep = " — " if " — " not in t else ": "
            room = MAX_LEN - len(t) - len(sep)
            if room > 12:
                t = f"{t}{sep}{_shorten_at_word_boundary(add, room)}"

    if len(t) > MAX_LEN:
        t = _squeeze(t, MAX_LEN)

    # Final polish: remove any trailing punctuation/stopword and periods
    t = _remove_trailing_dangling(t)
    if t.endswith("."):
        t = t[:-1].rstrip()

    # Absolutely no ellipses
    t = _strip_ellipses(t)

    return t
