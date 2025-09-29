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
    "wins","beats","edges","draws","signs","agrees","extends","returns",
    "suffers","rules","names","drops","backs","admits","confirms","predicts",
    "hopes","plans","targets","wants","appoints","fires","sacks","extends","agrees"
}

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

def _looks_cutoff(t: str) -> bool:
    t = (t or "").rstrip().strip("—-:")
    if not t:
        return True
    last = t.split()[-1].lower().strip('"\':,.;-')
    return (last in STOP_END) or t.endswith("...") or t.endswith("’") or t.endswith("'")

def _de_clickbait(t: str) -> str:
    low = (t or "").lower()
    for b in sorted(BANNED_CLICKBAIT, key=len, reverse=True):
        low = low.replace(b, "")
    low = re.sub(r"\b(update)\b(\s+\1\b)+", r"\1", low, flags=re.I)
    return " ".join(low.split())

def _title_case(t: str) -> str:
    def cap(w):
        if w.upper() in {"AFC","FA","UEFA","FIFA","VAR","UCL"}:
            return w.upper()
        if w.lower() in {"and","or","to","of","the","a","an","for","on","at","by","in","vs"}:
            return w.lower()
        return w[:1].upper() + w[1:]
    parts = re.split(r"(\s+|-|:)", t)
    return "".join(cap(w) if re.match(r"^[A-Za-z][A-Za-z'’]*$", w) else w for w in parts)

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

    # Ensure there is a verb; if not, graft a simple verb phrase from summary
    if not _has_verb(t) and fallback_summary:
        m = re.search(r"\b(is|are|agrees|signs|returns|suffers|confirms|predicts|names|wins|beats)\b.+",
                      fallback_summary, flags=re.I)
        if m:
            base = re.sub(r"[:–—-]\s*$", "", t)
            t = f"{base}: {m.group(0).strip()}"

    # Enforce word range
    words = [w for w in t.split() if w]
    if len(words) < TITLE_WORDS_MIN and fallback_summary:
        extra = " ".join(fallback_summary.split()[:max(0, TITLE_WORDS_MIN - len(words))])
        t = (t + " " + extra).strip()
    elif len(words) > TITLE_WORDS_MAX:
        t = " ".join(words[:TITLE_WORDS_MAX])

    # Final cleanup
    t = re.sub(r'""+', '"', t).strip(" -:–—")
    t = re.sub(r"\s+", " ", t).strip()
    return _title_case(t)

def headline_violations(title: str) -> list:
    """
    Return a list of violation codes for a given title.
    Used by the final page-quality gate.
    """
    errs = []
    t = (title or "").strip()
    if not t:
        errs.append("empty")
        return errs

    if "<" in t or "&lt;" in t or "&amp;" in t:
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

