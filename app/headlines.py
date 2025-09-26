# app/headlines.py
import re
from html import unescape

PROVIDER_HINTS = ("Evening Standard","Daily Mail","DailyMail","Arseblog",
                  "Pain In The Arsenal","PainInTheArsenal","ArsenalInsider")
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
    if len(t) <= max_len: return t
    for sep in [":"," — "," – "," - "," | "," —"]:
        cut = t.rfind(sep, 0, max_len)
        if cut >= int(max_len*0.6): return t[:cut].rstrip()
    cut = t.rfind(" ", 0, max_len)
    if cut >= int(max_len*0.6): return t[:cut].rstrip() + "…"
    return t[:max_len].rstrip() + "…"

def clean_headline_balanced(title: str, summary: str | None = None) -> str:
    if not title: return ""
    t = _collapse_ws(_first_clause(_remove_brackets(_strip_source_suffix(unescape(title)))))
    if len(t) < MIN_LEN and summary:
        hook = _collapse_ws(unescape(summary)).split(",", 1)[0]
        hook = " ".join(hook.split()[:6])
        candidate = _collapse_ws(f"{t}: {hook}")
        if len(candidate) <= MAX_LEN:
            t = candidate
    if len(t) > MAX_LEN:
        t = _soft_truncate(t, MAX_LEN)
    return t
