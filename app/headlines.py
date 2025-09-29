# app/headlines.py
# Unified headline polish + guardrails (no external deps)

import re

# === Tunables ===
BANNED_CLICKBAIT = {
    "quietly","unbelievable","shocking","you won't believe","you will not believe",
    "it's incredible","insane","jaw-dropping","brutal","brutally","impossible to ignore",
    "just said the quiet part","clickbait"
}
STOP_END = {"a","an","the","for","to","of","on","vs","than","with","from","at","by","as","about"}
TITLE_WORDS_MIN, TITLE_WORDS_MAX = 8, 14

VERB_HINTS = {
    "is","are","was","were","be","been","being",
    "agrees","signs","returns","suffers","confirms","predicts","names","wins","beats",
    "reveals","admits","says","warns","backs","drops","rules","selects","extends","offers","joins",
}

def _title_case(t: str) -> str:
    if not t: return ""
    # Simple titlecase that preserves acronyms & vs
    words = t.split()
    out = []
    for i,w in enumerate(words):
        wl = w.lower()
        if wl in {"vs","v"}:
            out.append(wl.upper())
        elif wl in STOP_END and i != 0:
            out.append(wl)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)

def _de_clickbait(t: str) -> str:
    tl = (t or "").lower()
    for b in BANNED_CLICKBAIT:
        if b in tl:
            tl = tl.replace(b, "")
    tl = re.sub(r"\s+", " ", tl).strip(" -:—–")
    return tl

def _clean_html(t: str) -> str:
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = (t.replace("&amp;", "&")
           .replace("&nbsp;", " ")
           .replace("&lt;", "<")
           .replace("&gt;", ">"))
    return re.sub(r"\s+", " ", t).strip()

def _has_verb(t: str) -> bool:
    words = {w.lower().strip('",.!?:;—-') for w in (t or "").split()}
    return any(v in words for v in VERB_HINTS)

def _bad_trailing(t: str) -> bool:
    """Detect endings indicating an incomplete/unsafe headline."""
    if not t:
        return True
    s = t.strip()
    if not s:
        return True
    if re.search(r'[:—–-]\s*$', s):
        return True
    if re.search(r'\s+(a|an|the|for|to|of|on|vs|than|with|from|at|by|as|about)$', s, re.I):
        return True
    # unmatched quotes of common types
    if s.count('"') % 2 == 1 or s.count("“") != s.count("”") or s.count("‘") != s.count("’"):
        return True
    return False

def polish_title(raw: str, fallback_summary: str = "") -> str:
    """
    Produce a professional, full-sentence, de-clickbaited, 8–14 word headline.
    """
    t = _clean_html(raw) or _clean_html(fallback_summary)
    t = t.strip('"\u201c\u201d ')
    t = _de_clickbait(t)
    t = re.sub(r"\s+", " ", t).strip()

    if _looks_cutoff(t):
        t = re.sub(r"[:–—-]\s*$", "", t)
        t = re.sub(r"\b(a|an|the|for|to|of|on|vs|than|with|from|at|by|as|about)$", "", t, flags=re.I).strip()

    # HARD GUARDRAIL: fallback to sanitized source if trailing looks unsafe
    if _bad_trailing(t):
        base = _clean_html(raw) or _clean_html(fallback_summary)
        base = re.sub(r"[:–—-]\s*$", "", base)
        base = re.sub(r"\s+(a|an|the|for|to|of|on|vs|than|with|from|at|by|as|about)$", "", base, flags=re.I).strip()
        # soft cap length
        words = [w for w in base.split() if w]
        if len(words) > TITLE_WORDS_MAX:
            base = " ".join(words[:TITLE_WORDS_MAX])
        t = base

    # Ensure there is a verb; if not, graft a simple verb phrase from summary
    if not _has_verb(t) and fallback_summary:
        m = re.search(r"\b(is|are|agrees|signs|returns|suffers|confirms|predicts|names|wins|beats)\b.+", fallback_summary, re.I)
        if m:
            t = re.sub(r"[.:;—–-]\s*$", "", t)
            t = f"{t}: {m.group(0)}"

    # Enforce soft word window
    words = [w for w in re.split(r"\s+", t) if w]
    if len(words) < TITLE_WORDS_MIN:
        # pad with key nouns from summary if available
        if fallback_summary:
            extra = " ".join([w for w in re.findall(r"[A-Za-z0-9]+", fallback_summary)[:(TITLE_WORDS_MIN - len(words))]])
            t = f"{t} {extra}".strip()
    elif len(words) > TITLE_WORDS_MAX:
        t = " ".join(words[:TITLE_WORDS_MAX])

    return _title_case(t)

def _looks_cutoff(t: str) -> bool:
    t = (t or "").rstrip().strip("—-:")
    if not t:
        return True
    last = t.split()[-1].lower().strip('"\':,.;-')
    return (last in STOP_END) or t.endswith("...") or t.endswith("’") or t.endswith("'")

def headline_violations(title: str) -> list:
    errs = []
    t = (title or "").strip()
    if not t:
        errs.append("empty")
    if "<" in t or "</" in t:
        errs.append("html")
    if _looks_cutoff(t):
        errs.append("cutoff")
    if not _has_verb(t):
        errs.append("no_verb")

    low = t.lower()
    if any(b in low for b in BANNED_CLICKBAIT):
        errs.append("clickbait")

    wc = len([w for w in re.split(r"\s+", t) if w])
    if wc < TITLE_WORDS_MIN or wc > TITLE_WORDS_MAX:
        errs.append("length")

    return errs

