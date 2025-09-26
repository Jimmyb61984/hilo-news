# app/headlines.py
from __future__ import annotations
import re
import html
from typing import Optional

__all__ = ["rewrite_headline"]

_BAD_ENDINGS = {
    "after", "amid", "as", "with", "for", "to", "vs", "v", "at", "over", "from",
    "by", "on", "in", "of", "and", "or", "but", "yet", "so", "because"
}

_MIN_LEN = 38
_MAX_LEN = 92


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _complete_after_phrase(base: str, summary: str, url: str) -> str:
    """
    If a title ends with 'after' (or 'after…'), complete it using summary/URL hints.
    Falls back to a neutral phrasing rather than leaving an ellipsis.
    """
    stem = base.rstrip(". ").rstrip()
    if not stem.lower().endswith("after"):
        stem += " after"

    s_low = _collapse_ws(_strip_tags(summary)).lower()
    u_low = (url or "").lower()

    if ("brain injury" in s_low) or ("head injury" in s_low) or ("brain-injury" in u_low):
        return f"{stem} head injury"
    if "collision" in s_low:
        return f"{stem} a collision"
    if "injury" in s_low:
        return f"{stem} on-field injury"
    if ("illness" in s_low) or ("condition" in s_low):
        return f"{stem} a sudden illness"
    return f"{stem} an incident in match"


def _tidy_end(text: str) -> str:
    # Normalize unicode ellipsis
    t = text.replace("\u2026", "...")
    # Convert mid-ellipsis to dash, but remove trailing ellipses
    t = t.replace("...", " — ")
    t = re.sub(r"[.\u2026]{3,}$", "", t).rstrip()
    # Drop stray trailing punctuation
    return t.rstrip(":-—–|").strip()


def _cut_dangling_tail(text: str) -> str:
    if not text:
        return text
    words = text.split()
    if not words:
        return text
    last = words[-1].rstrip(".,;:!?").lower()
    if last in _BAD_ENDINGS or text.endswith((" -", " —", " –", ":")):
        # Prefer cutting back to a hard separator; otherwise drop the final word.
        for sep in (" — ", " – ", " - ", ": ", " | "):
            pos = text.rfind(sep)
            if pos != -1:
                return text[:pos].strip()
        return " ".join(words[:-1]).strip()
    return text


def _extend_if_short(text: str, summary: Optional[str]) -> str:
    if text and len(text) >= _MIN_LEN:
        return text
    if not summary:
        return text
    s = _collapse_ws(_strip_tags(summary))
    if not s:
        return text
    first = re.split(r"(?<=[.!?])\s+", s)[0]
    # Avoid duplicating the title
    if first and not first.lower().startswith(text.lower()):
        candidate = f"{text}: {first}" if text else first
    else:
        candidate = first or text
    # Fit to max without adding ellipses
    if len(candidate) > _MAX_LEN:
        cut = max(
            candidate.rfind(", ", 0, _MAX_LEN),
            candidate.rfind(" ", 0, _MAX_LEN),
        )
        candidate = candidate[:cut] if cut > 0 else candidate[:_MAX_LEN]
    return candidate.rstrip(":-—–|").strip()


def _cap_if_long(text: str) -> str:
    if len(text) <= _MAX_LEN:
        return text
    cut = max(
        text.rfind(". ", 0, _MAX_LEN),
        text.rfind(" — ", 0, _MAX_LEN),
        text.rfind(" – ", 0, _MAX_LEN),
        text.rfind(": ", 0, _MAX_LEN),
        text.rfind(", ", 0, _MAX_LEN),
        text.rfind(" ", 0, _MAX_LEN),
    )
    t = text[:cut].strip() if cut > 0 else text[:_MAX_LEN].rstrip()
    return t.rstrip(":-—–|").strip()


def rewrite_headline(
    title: str,
    provider: Optional[str] = None,
    summary: Optional[str] = None,
    url: Optional[str] = None,
) -> str:
    """
    Balance headline length (aim ~58–80 chars, cap 92), remove stubby '…' endings,
    and complete common dangling patterns like 'after…' using summary/URL hints.
    Does not import from any other app module (avoids circular imports).
    """
    if not title:
        return ""

    # Base cleanup
    t = html.unescape(_collapse_ws(_strip_tags(title)))
    s = summary or ""
    u = url or ""

    # If it ends with 'after…', complete that phrase first
    if re.search(r"\bafter[.…]*\s*$", t, flags=re.IGNORECASE):
        t = re.sub(r"[.…]*\s*$", "", t)  # strip trailing dots
        t = _complete_after_phrase(t, s, u)

    # General tidying and dangling-tail fix
    t = _tidy_end(t)
    t = _cut_dangling_tail(t)

    # Extend if too short using the first clean sentence from summary
    t = _extend_if_short(t, s)

    # Keep within maximum without adding ellipses
    t = _cap_if_long(t)

    # Final punctuation spacing tidy
    t = re.sub(r"\s+([,.;:!?])", r"\1", t).strip()
    return t
