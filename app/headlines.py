# app/headlines.py
import re
from html import unescape

PROVIDER_HINTS = (
    "Evening Standard", "Daily Mail", "DailyMail", "Arseblog",
    "Pain In The Arsenal", "PainInTheArsenal", "ArsenalInsider"
)

# Balance window: keep things concise but not stubby
MIN_LEN = 38
MAX_LEN = 78

SEP_CANDIDATES = [" | ", " — ", " – ", " - "]


def _strip_source_suffix(t: str) -> str:
    for sep in SEP_CANDIDATES:
        if sep in t:
            left, right = t.rsplit(sep, 1)
            if any(h.lower() in right.lower() for h in PROVIDER_HINTS) or "." in right:
                return left
    return t


def _first_clause(t: str) -> str:
    # Prefer the pre-colon clause if it's a sensible headline length
    if ":" in t:
        left, _ = t.split(":", 1)
        if 24 <= len(left.strip()) <= 90:
            return left.strip()
    return t


def _remove_brackets(t: str) -> str:
    return re.sub(r"\s*[\(\[\{][^\)\]\}]{0,80}[\)\]\}]\s*", " ", t)


def _collapse_ws(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _soft_truncate(t: str, max_len: int) -> str:
    if len(t) <= max_len:
        return t
    for sep in [":", " — ", " – ", " - ", " | ", " —"]:
        cut = t.rfind(sep, 0, max_len)
        if cut >= int(max_len * 0.6):
            return t[:cut].rstrip()
    cut = t.rfind(" ", 0, max_len)
    if cut >= int(max_len * 0.6):
        return t[:cut].rstrip() + "…"
    return t[:max_len].rstrip() + "…"


def _first_sentence(text: str) -> str:
    # Take the first sentence-ish chunk for a hook
    text = _collapse_ws(text)
    m = re.split(r"(?<=[\.\!\?])\s+", text, maxsplit=1)
    return m[0] if m else text


def _build_dynamic_hook(base: str, summary: str, min_len: int, max_len: int) -> str | None:
    """
    Build a short, meaningful tail (from summary) to bring base into the target window.
    We try 12 words and adjust to land between MIN_LEN and MAX_LEN.
    """
    hook_src = _first_sentence(unescape(summary))
    tokens = hook_src.split()

    if not tokens:
        return None

    # Start near 12 words, then expand/contract as needed
    n = min(12, len(tokens))
    # Try to land >= MIN_LEN while staying <= MAX_LEN
    # Expand up to 18 words if still short; shrink down to 4 if too long
    while n < min(18, len(tokens)):
        candidate = _collapse_ws(f"{base}: {' '.join(tokens[:n])}")
        if len(candidate) >= min_len or len(candidate) >= len(base) + 8:
            break
        n += 1

    while n > 4:
        candidate = _collapse_ws(f"{base}: {' '.join(tokens[:n])}")
        if len(candidate) <= max_len:
            return candidate
        n -= 1

    # Last resort: trimmed sentence
    candidate = _collapse_ws(f"{base}: {hook_src}")
    return _soft_truncate(candidate, max_len)


def clean_headline_balanced(title: str, summary: str | None = None) -> str:
    if not title:
        return ""

    # Keep a "base" without the pre-colon reduction so we can reuse if needed
    base = _collapse_ws(_remove_brackets(_strip_source_suffix(unescape(title))))
    t = _collapse_ws(_first_clause(base))

    # If too short, try to enrich from summary (not clickbaity; just enough detail)
    if len(t) < MIN_LEN and summary:
        candidate = _build_dynamic_hook(t, summary, MIN_LEN, MAX_LEN)
        if candidate:
            t = candidate

    # Still short and no summary? Prefer the fuller base (not just the pre-colon)
    if len(t) < MIN_LEN and base != t:
        t = base

    # Final length guardrails
    if len(t) > MAX_LEN:
        t = _soft_truncate(t, MAX_LEN)

    return t


# Public API expected by main.py
def rewrite_headline(title: str, summary: str | None = None) -> str:
    """
    Stable wrapper used by main.py. Intentionally thin so future tuning
    doesn't break imports.
    """
    return clean_headline_balanced(title, summary)
