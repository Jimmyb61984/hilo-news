import re
from hashlib import sha1
from datetime import datetime
from typing import List, Dict, Any, Tuple

# Heuristics for source quality
OFFICIAL_SOURCES = {"EveningStandard", "DailyMail", "SkySports", "BBCSport", "TheGuardian", "Telegraph"}

# Common noisy suffixes we want to trim if they trail a title
_NOISE_SUFFIX_RE = re.compile(r"(?:\s*[-–—:]?\s*)?(?:update|latest|breaking|live)(?:\s+latest|\s+update)*\s*$", re.I)
_DUP_WORDS_RE    = re.compile(r"\b(\w+)(?:\s+\1){1,}\b", re.I)

# Basic verb list to sanity-check that something reads like a sentence
VERBS = {
    "is","are","was","were","be","being","been","has","have","had",
    "signs","signed","agrees","agree","agreed","confirms","confirm","confirmed",
    "predicts","backs","reveals","warns","drops","names","hopes","faces","eyes",
    "wants","targets","extends","suffers","says","sack","sacks","wins","win","beats","beat",
    "appoints","appointed","seals","seal","joins","join","returns","return","rules","rule"
}

def _canon(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()

def _ends_with_noise(title: str) -> str:
    return _NOISE_SUFFIX_RE.sub("", title)

def _strip_leading_arsenal(t: str) -> str:
    # Many fan sites prefix "Arsenal" redundantly; drop if followed by another capitalised phrase
    if t.lower().startswith("arsenal "):
        rest = t.split(" ", 1)[1]
        if rest and rest[0].isalpha():
            return rest
    return t

def _normalize_quotes_and_apostrophes(t: str) -> str:
    # normal quotes and common "its" -> "it's" when appropriate
    t = t.replace("’", "'").replace("‘", "'").replace("“","\"").replace("”","\"")
    t = re.sub(r"\bits\b", "it's", t)
    return t

def _kill_trailing_punct(t: str) -> str:
    return t.rstrip(" .,-–—:;_")

def _dedupe_repeated_words(t: str) -> str:
    # Collapse "update update", "latest latest" etc
    return _DUP_WORDS_RE.sub(lambda m: m.group(1), t)

def _has_verb(t: str) -> bool:
    tokens = {w.lower() for w in re.findall(r"[A-Za-z']+", t)}
    return any(v in tokens for v in VERBS)

def _first_sentence(summary: str) -> str:
    s = _canon(summary)
    # cut at first full stop or 150 chars as a backup
    m = re.search(r"[.!?]\s", s)
    cut = m.start()+1 if m else min(len(s), 150)
    return s[:cut].strip()

def _clip_to_range(t: str, lo: int, hi: int) -> str:
    t = _canon(t)
    if len(t) <= hi:
        return t
    # clip at last space before hi
    cut = t.rfind(" ", 0, hi)
    if cut == -1:
        return t[:hi]
    return t[:cut]

def rewrite_headline(title: str, summary: str, provider: str, lo: int = 56, hi: int = 88) -> str:
    """
    Rewrite clickbait / incomplete titles into clean, professional, sentence-case headlines.
    Keeps names and nouns intact, trims noisy suffixes, fixes duplication, and
    backfills from summary if the title lacks a verb or context.
    """
    t = _canon(title)
    t = _strip_leading_arsenal(t)
    t = _ends_with_noise(t)
    t = _dedupe_repeated_words(t)
    t = _normalize_quotes_and_apostrophes(t)
    t = _kill_trailing_punct(t)

    # Fix odd "for Arsenal ..." tails duplicated by some scrapers (keep first occurrence)
    t = re.sub(r"(?:\s*for Arsenal){2,}", " for Arsenal", t, flags=re.I)

    # Replace a few recurring clickbait framings we saw in samples
    t = re.sub(r"^Darren Bent\s+thinks\s+he\s+knows\s+what\s+Mikel\s+Arteta\s+is\s+going\s+to\s+do\s+with\s+Eberechi\s+Eze",
               "Darren Bent predicts how Arteta will use Eberechi Eze", t, flags=re.I)
    t = re.sub(r"^Mikel Arteta\s+just\s+made\s+", "Mikel Arteta has made ", t, flags=re.I)

    # If it still looks fragmentary (no verb or too short), lift from summary
    if not _has_verb(t) or len(t.split()) < 6:
        s1 = _first_sentence(summary or "")
        if s1 and len(s1.split()) >= 6:
            t = _kill_trailing_punct(s1)

    # Ensure sentence case (capitalise first letter, leave proper nouns)
    if t:
        t = t[0].upper() + t[1:]

    # Final clean-ups
    t = _kill_trailing_punct(t)
    t = _dedupe_repeated_words(t)
    t = _ends_with_noise(t)
    t = _canon(t)

    # Target a tight length range for visual consistency
    t = _clip_to_range(t, lo, hi)

    return t

def _img_ok(url: str) -> bool:
    if not url:
        return False
    u = url.lower()
    if u.endswith(".mp3") or "open.acast.com" in u:
        return False
    return True

def _iso(dt_str: str) -> str:
    try:
        return datetime.fromisoformat(dt_str.replace("Z","+00:00")).isoformat()
    except Exception:
        return ""

def _norm_key(item: Dict[str, Any]) -> Tuple[int, str]:
    """Return a sort key preferring official sources, fresh timestamps, and good images."""
    provider = item.get("provider") or ""
    official_rank = 0 if provider in OFFICIAL_SOURCES else 1
    ts = _iso(item.get("publishedUtc",""))
    img_score = 0 if _img_ok(item.get("imageUrl","")) else 1
    # Use hash of canonical title to group duplicates
    h = sha1((_canon(item.get("title","")).lower()).encode("utf-8")).hexdigest()
    return (official_rank, img_score, ts, h)

def dedupe_and_sort(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    bucket: Dict[str, Dict[str, Any]] = {}
    for it in items:
        t_norm = _canon((it.get("title") or "").lower())
        key = re.sub(r"[^a-z0-9 ]+", "", t_norm)
        # If we've seen this key, keep the better (official/img/newer)
        if key in seen:
            prev = bucket[key]
            if _norm_key(it) < _norm_key(prev):
                bucket[key] = it
        else:
            seen.add(key)
            bucket[key] = it
    # Sort by provider quality, image, time
    return sorted(bucket.values(), key=_norm_key)

def curate_and_polish(items: List[Dict[str, Any]], target_min: int = 56, target_max: int = 88) -> List[Dict[str, Any]]:
    curated: List[Dict[str, Any]] = []
    for it in items:
        # Basic field presence
        if not it or not it.get("title") or not it.get("url"):
            continue
        # Filter out bad images
        if not _img_ok(it.get("imageUrl","")):
            it = {**it, "imageUrl": ""}
        clean = rewrite_headline(it.get("title",""), it.get("summary",""), it.get("provider",""), lo=target_min, hi=target_max)
        if not clean:
            continue
        curated.append({**it, "title": clean})
    curated = dedupe_and_sort(curated)
    return curated
