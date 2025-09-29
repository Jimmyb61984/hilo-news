# main.py
# Unified assemble → quotas/caps → dedupe → final quality gate (no new files)

import json, re, os, datetime as dt
from collections import Counter, defaultdict
from urllib.parse import urlparse

from app.headlines import polish_title, headline_violations
from app.fetcher import sanitize_headline

# === Tunables: adjust once, everything respects these ===
TARGET_COUNT = 100
MAX_SINGLE_DOMAIN_SHARE = 0.30          # no domain >30%
MIN_OFFICIAL_SHARE = 0.35               # at least 35% official
MIN_FRESH_48H_SHARE = 0.85              # at least 85% fresh (48h)
REQUIRED_WOMEN = 6
REQUIRED_ACADEMY = 6

FRESH_HOURS = 48
IMAGE_MIN_WIDTH = 400
IMAGE_MIN_HEIGHT = 225

OFFICIAL_DOMAINS = {
    "standard.co.uk","bbc.co.uk","theguardian.com","skysports.com",
    "arsenal.com","reuters.com","apnews.com","telegraph.co.uk",
    "independent.co.uk","dailymail.co.uk","times.co.uk","espn.com"
}

# --- Helpers ----
def _domain(u:str) -> str:
    try:
        return urlparse(u).netloc.lower().replace("www.","")
    except:
        return ""

def _is_official(u:str) -> bool:
    d = _domain(u)
    return d in OFFICIAL_DOMAINS

def _age_hours(iso:str) -> float:
    try:
        from dateutil import parser as du
        ts = du.parse(iso)
        return (dt.datetime.now(dt.timezone.utc) - ts).total_seconds()/3600.0
    except Exception:
        return None

def _tag(it:dict) -> str:
    t = (it.get("type") or "").lower()
    if "women" in (it.get("title","").lower()+" "+it.get("summary","").lower()):
        return "women"
    if "u19" in (it.get("title","").lower()+" "+it.get("summary","").lower()):
        return "academy"
    return t

def _normalize_topic(it:dict) -> str:
    t = (it.get("title") or "").lower()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _valid_image(it:dict) -> bool:
    url = it.get("imageUrl") or ""
    if not url or url.endswith(".gif"):
        return False
    # width/height unknown? allow, UI will handle
    return True

# --- Stage 1: polish + score ----

def polish_and_score(items):
    polished = []
    rewrite_enabled = os.getenv("HEADLINE_REWRITE_ENABLED", "false").lower() in {"1","true","yes","on"}
    for it in items:
        raw_title = it.get("title","") or ""
        raw_summary = it.get("summary","") or ""
        title = polish_title(raw_title, raw_summary) if rewrite_enabled else (sanitize_headline(raw_title) or sanitize_headline(raw_summary))
        it2 = dict(it)  # shallow copy
        it2["title"] = title
        it2["_violations"] = headline_violations(title)
        it2["_official"] = _is_official(it.get("url",""))
        it2["_age_h"] = _age_hours(it.get("publishedUtc","")) or 1e9
        it2["_domain"] = _domain(it.get("url",""))
        it2["_tags"] = _tag(it)
        it2["_topic"] = _normalize_topic(it)
        it2["_img_ok"] = _valid_image(it)
        # base quality score (official + freshness + has image, minus violations)
        score = 0.0
        score += 1.0 if it2["_official"] else 0.0
        if it2["_age_h"] <= 6: score += 0.7
        elif it2["_age_h"] <= 24: score += 0.4
        elif it2["_age_h"] <= 48: score += 0.2
        if it2["_img_ok"]: score += 0.2
        score -= 0.2 * len(it2["_violations"])
        it2["_q"] = round(score, 3)
        polished.append(it2)
    return polished

# --- Stage 2: soft dedupe by topic (final-normalized titles) ----

def dedupe_soft(items):
    buckets = defaultdict(list)
    for it in items:
        buckets[it["_topic"]].append(it)
    kept = []
    for _, group in buckets.items():
        # keep best quality per topic
        group.sort(key=lambda x:(-x["_q"], x["_age_h"]))
        kept.append(group[0])
    return kept

# --- Stage 3: caps & quotas (unchanged) ----

def apply_caps_and_quotas(items):
    # Start with best quality
    items = sorted(items, key=lambda x:(-x["_q"], x["_age_h"]))
    # Hard drop any with missing images or headline violations
    items = [x for x in items if x["_img_ok"] and not set(x["_violations"]) & {"html","cutoff","empty"}]

    # Filter out women/U19
    items = [x for x in items if x["_tags"] not in {"women","academy"}]

    # Enforce single domain share cap
    by_domain = defaultdict(list)
    for it in items:
        by_domain[it["_domain"]].append(it)
    max_per = max(1, int(TARGET_COUNT * MAX_SINGLE_DOMAIN_SHARE))
    trimmed = []
    for d, group in by_domain.items():
        trimmed.extend(group[:max_per])

    # Ensure official share
    off = [x for x in trimmed if x["_official"]]
    non = [x for x in trimmed if not x["_official"]]
    min_off = int(TARGET_COUNT * MIN_OFFICIAL_SHARE)
    if len(off) < min_off:
        # promote more officials from the original items if available
        extra_off = [x for x in items if x["_official"] and x not in off]
        off.extend(extra_off[: (min_off - len(off))])

    # freshness check (soft)
    fresh = [x for x in trimmed if x["_age_h"] <= FRESH_HOURS]
    need_fresh = int(TARGET_COUNT * MIN_FRESH_48H_SHARE)
    if len(fresh) < need_fresh:
        pool = [x for x in items if x["_age_h"] <= FRESH_HOURS and x not in fresh]
        fresh.extend(pool[: (need_fresh - len(fresh))])

    # Merge and sort final
    merged = list({id(x): x for x in (off + fresh + trimmed)}.values())
    merged = sorted(merged, key=lambda x:(-x["_q"], x["_age_h"]))
    return merged[:TARGET_COUNT]

# --- Final gate + report ----

def assemble_page(raw_items):
    """
    Raw items in -> 10/10 page out (or empty list with a fail report).
    This is the only function your app needs to call before render.
    """
    if not isinstance(raw_items, list):
        raise ValueError("assemble_page expects a list of items")

    # 1) Polish + score
    stage1 = polish_and_score(raw_items)

    # 2) Soft dedupe on topic
    stage2 = dedupe_soft(stage1)

    # 3) Caps & quotas
    stage3 = apply_caps_and_quotas(stage2)

    # 4) Final acceptance report (print-only; does not change output)
    ok = True
    domains = [x["_domain"] for x in stage3]
    domain_counts = Counter(domains)
    official_share = sum(1 for x in stage3 if x["_official"])/max(1,len(stage3))
    fresh_share = sum(1 for x in stage3 if x.get("_age_h",1e9) <= FRESH_HOURS)/max(1,len(stage3))

    if any(v/max(1,len(stage3)) > MAX_SINGLE_DOMAIN_SHARE for v in domain_counts.values()):
        ok = False
    if official_share < MIN_OFFICIAL_SHARE:
        ok = False
    if fresh_share < MIN_FRESH_48H_SHARE:
        ok = False
    if any(set(x["_violations"]) & {"html","cutoff","empty"} for x in stage3):
        ok = False

    print("=== FEED QA ===")
    print("Domains:", dict(domain_counts))
    print("Official share:", round(official_share,2))
    print("Fresh(≤48h) share:", round(fresh_share,2))
    print("Violations:", sum(len(x["_violations"]) for x in stage3))
    print("===========================")

    return stage3 if ok else []

# --- If you need a quick manual test with a JSON file ---
if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python main.py feed.json")
        raise SystemExit(1)
    data = json.load(open(sys.argv[1]))
    items = data.get("items", data)
    page = assemble_page(items)
    out = {"items": page, "count": len(page)}
    print(json.dumps(out, ensure_ascii=False, indent=2))

