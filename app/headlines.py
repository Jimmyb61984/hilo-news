# app/headlines.py
import re
from html import unescape

# Known provider suffixes sometimes appended in titles; used to strip " - Source"
PROVIDER_HINTS = (
    "Evening Standard",
    "Daily Mail",
    "Arseblog",
    "Pain In The Arsenal",
    "PainInTheArsenal",
    "ArsenalInsider",
)

# Balance window: concise but not stubby
# Raise MIN_LEN a touch to avoid short, fragmenty headlines.
MIN_LEN = 42
MAX_LEN = 88

# Common separators seen in feeds
SEP_CANDIDATES = [" | ", " — ", " – ", " - "]

def _collapse_ws(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()

def _remove_brackets(t: str) -> str:
    # Drop short bracketed asides e.g., (Video), [Opinion], {Gallery}
    return re.sub(r"\s*[\(\[\{][^\)\]\}]{0,80}[\)\]\}]\s*", " ", t)

def _strip_source_suffix(t: str) -> str:
    # Remove trailing " - Daily Mail" / " — Evening Standard" etc.
    for sep in SEP_CANDIDATES:
        if sep in t:
            left, right = t.rsplit(sep, 1)
            if any(h.lower() in right.lower() for h in PROVIDER_HINTS) or "." in right:
                return left
    return t

def _first_clause(t: str) -> str:
    # Prefer the pre-colon clause if it’s reasonably headline-like
    if ":" in t:
        left, _ = t.split(":", 1)
        left = left.strip()
        if 26 <= len(left) <= 90:
            return left
    return t

def _soft_truncate(t: str, max_len: int) -> str:
    """Trim cleanly at a sensible boundary; only add ellipsis when we must."""
    if len(t) <= max_len:
        return t

    # Prefer to cut at major separators with NO ellipsis (reads clean)
    for sep in [":", " — ", " – ", " - ", " | "]:
        cut = t.rfind(sep, 0, max_len)
        if cut >= int(max_len * 0.6):
            return t[:cut].rstrip()

    # Next, cut on whitespace and add a single ellipsis
    cut = t.rfind(" ", 0, max_len)
    if cut >= int(max_len * 0.6):
        return t[:cut].rstrip() + "…"

    # Fallback: hard cut with ellipsis
    return t[:max_len].rstrip() + "…"

def _first_sentence(text: str) -> str:
    """Grab a sentence-like hook from summary to enrich short titles."""
    text = _collapse_ws(text)
    parts = re.split(r"(?<=[\.\!\?])\s+", text, maxsplit=1)
    return parts[0] if parts else text

def _build_dynamic_hook(base: str, summary: str, min_len: int, max_len: int) -> str | None:
    """Use a short, meaningful tail from the summary to lift short bases into range."""
    hook_src = _first_sentence(unescape(summary))
    tokens = hook_src.split()
    if not tokens:
        return None

    # Start around 12 words; expand/contract to land in the window
    n = min(12, len(tokens))

    # If still too short, expand up to 20 words
    while n < min(20, len(tokens)):
        candidate = _collapse_ws(f"{base}: {' '.join(tokens[:n])}")
        if len(candidate) >= min_len or len(candidate) >= len(base) + 10:
            break
        n += 1

    # If too long, contract down (but not below 5)
    while n > 5:
        candidate = _collapse_ws(f"{base}: {' '.join(tokens[:n])}")
        if len(candidate) <= max_len:
            return candidate
        n -= 1

    # Last resort, take the whole first sentence and trim softly
    candidate = _collapse_ws(f"{base}: {hook_src}")
    return _soft_truncate(candidate, max_len)

def clean_headline_balanced(title: str, summary: str | None = None) -> str:
    """Produce a professional, balanced-length headline from raw feed titles."""
    if not title:
        return ""

    base_full = _collapse_ws(_remove_brackets(_strip_source_suffix(unescape(title))))
    base = _collapse_ws(_first_clause(base_full))

    # If too short, enrich from summary (avoids stubby outputs)
    if len(base) < MIN_LEN and summary:
        enriched = _build_dynamic_hook(base, summary, MIN_LEN, MAX_LEN)
        if enriched:
            base = enriched

    # If still short and we chopped at ':', prefer the fuller base_full
    if len(base) < MIN_LEN and base_full != base:
        base = base_full

    # Final length guardrails
    if len(base) > MAX_LEN:
        base = _soft_truncate(base, MAX_LEN)

    return base

# Public API expected by main.py
def rewrite_headline(title: str, summary: str | None = None) -> str:
    """Stable wrapper so main.py can import without caring about internals."""
    return clean_headline_balanced(title, summary)

