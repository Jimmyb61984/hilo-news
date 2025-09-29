# main.py
# Unified assemble → quotas/caps → dedupe → final quality gate (no new files)

import json, re, datetime as dt
from collections import Counter, defaultdict
from urllib.parse import urlparse

from app.headlines import polish_title, headline_violations

# === Tunables: adjust once, everything respects these ===
TARGET_COUNT = 100
MAX_SINGLE_DOMAIN_SHARE = 0.30          # no domain >30%
MIN_OFFICIAL_SHARE = 0.35               # at least 35% official
MIN_FRESH_48H_SHARE = 0.85              # at least 85% fresh (48h)
REQUIRED_WOMEN = 6
REQUIRED_ACADEMY = 6
REQUIRED_TACTICAL = 8

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
    return any(h in d for h in OFFICIAL_DOMAINS)

def _parse_dt(iso: str):
    try:
        return dt.datetime.fromisoformat(iso.replace("Z","+00:00")).astimezone(dt.timezone.utc)
    except Exception:
        return None

def _age_hours(iso: str):
    t = _parse_dt(iso)
    if not t: return None
    now = dt.datetime.now(dt.timezone.utc)
    return (now - t).total_seconds()/3600.0

KEYWORDS_WOMEN = re.compile(r"\b(women|wsl|arsenal women|slegers|caitlin|miedema|mead|little)\b", re.I)
KEYWORDS_ACADEMY = re.compile(r"\b(u18|u21|u23|academy|hale end|nwaneri|lewis-skelly|max dowman|loanee|loan)\b", re.I)
KEYWORDS_TACTICAL = re.compile(r"\b(tactic|tactical|analysis|xg|shape|press|build-up|pressing|structure|roles?)\b", re.I)

def _tag(item):
    text = " ".join([item.get("title",""), item.get("summary","")])
    tags = set()
    if KEYWORDS_WOMEN.search(text): tags.add("women")
    if KEYWORDS_ACADEMY.search(text): tags.add("academy")
    if KEYWORDS_TACTICAL.search(text): tags.add("tactical")
    return tags

def _normalize_topic(item):
    base = " ".join([item.get("title",""), item.get("summary","")]).lower()
    base = re.sub(r"<[^>]+>"," ", base)
    base = re.sub(r"[^a-z0-9 ]+"," ", base)
    base = re.sub(r"\b(arsenal|afc|fc|update|latest|report|exclusive|preview|reaction)\b"," ", base)
    base = re.sub(r"\s+"," ", base).strip()
    # order-independent bag for soft dedupe
    return " ".join(sorted(set(base.split())))[:160]

def _valid_image(item):
    url = (item.get("imageUrl") or "").strip()
    return bool(url and len(url) > 7 and not url.lower().endswith(".mp3"))

# --- Core: assemble page from raw items list (already fetched elsewhere) ---
def polish_and_score(items):
    polished = []
    for it in items:
        title = polish_title(it.get("title",""), it.get("summary",""))
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
        score += 2.0 if it2["_official"] else 0.0
        score += 1.0 if it2["_age_h"] <= 48 else 0.0
        score += 0.5 if it2["_img_ok"] else -1.0
        score -= 0.5 * len(it2["_violations"])
        it2["_q"] = score
        polished.append(it2)
    # sort by quality, freshest first as tiebreak
    return sorted(polished, key=lambda x:(-x["_q"], x["_age_h"]))

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

def apply_caps_and_quotas(items):
    # Start with best quality
    items = sorted(items, key=lambda x:(-x["_q"], x["_age_h"]))
    # Hard drop any with missing images or headline violations
    items = [x for x in items if x["_img_ok"] and not set(x["_violations"]) & {"html","cutoff","empty"}]

    # Source cap
    cap = int(MAX_SINGLE_DOMAIN_SHARE * TARGET_COUNT)
    per_domain = Counter()
    balanced = []
    for it in items:
        if per_domain[it["_domain"]] >= cap:
            continue
        per_domain[it["_domain"]] += 1
        balanced.append(it)

    # Ensure quotas by pulling in items with needed tags if missing
    def need(tag, req):
        return sum(1 for i in balanced if tag in i["_tags"]) < req

    if need("women", REQUIRED_WOMEN):
        fill = [i for i in items if "women" in i["_tags"] and i not in balanced]
        for i in fill:
            if sum(1 for j in balanced if "women" in j["_tags"]) >= REQUIRED_WOMEN: break
            balanced.append(i)

    if need("academy", REQUIRED_ACADEMY):
        fill = [i for i in items if "academy" in i["_tags"] and i not in balanced]
        for i in fill:
            if sum(1 for j in balanced if "academy" in j["_tags"]) >= REQUIRED_ACADEMY: break
            balanced.append(i)

    if need("tactical", REQUIRED_TACTICAL):
        fill = [i for i in items if "tactical" in i["_tags"] and i not in balanced]
        for i in fill:
            if sum(1 for j in balanced if "tactical" in j["_tags"]) >= REQUIRED_TACTICAL: break
            balanced.append(i)

    # Re-trim to target count by quality
    balanced = sorted(balanced, key=lambda x:(-x["_q"], x["_age_h"]))[:TARGET_COUNT]
    return balanced

def final_quality_gate(items):
    n = len(items)
    doms = [_domain(i.get("url","")) for i in items]
    c = Counter(doms)
    worst_dom, worst_count = (c.most_common(1)[0] if c else ("", 0))
    official = sum(1 for i in items if _is_official(i.get("url","")))
    ages_h = [i["_age_h"] for i in items if i["_age_h"] < 1e9]
    fresh = sum(1 for h in ages_h if h <= 48)
    violations = sum(len(i.get("_violations",[])) for i in items)
    missing_img = sum(1 for i in items if not i.get("_img_ok"))

    fail_reasons = []
    if n != TARGET_COUNT: fail_reasons.append(f"count={n} (target {TARGET_COUNT})")
    if worst_count > int(MAX_SINGLE_DOMAIN_SHARE * TARGET_COUNT):
        fail_reasons.append(f"domain_cap_exceeded: {worst_dom}={worst_count}")
    if official < int(MIN_OFFICIAL_SHARE * TARGET_COUNT):
        fail_reasons.append(f"official_share={official}/{TARGET_COUNT}")
    if ages_h and fresh < int(MIN_FRESH_48H_SHARE * len(ages_h)):
        fail_reasons.append(f"fresh_48h={fresh}/{len(ages_h)}")
    if missing_img > 0: fail_reasons.append(f"missing_images={missing_img}")
    if violations > 0: fail_reasons.append(f"headline_violations={violations}")

    # Build a concise report
    report = []
    report.append(f"Total items: {n} (target {TARGET_COUNT})")
    report.append(f"Largest domain: {worst_dom} = {worst_count} ({(worst_count*100.0/max(n,1)):.1f}%)")
    report.append(f"Official share: {official}/{n} ({(official*100.0/max(n,1)):.1f}%)")
    if ages_h:
        report.append(f"Fresh (<=48h): {fresh}/{len(ages_h)} ({(fresh*100.0/max(len(ages_h),1)):.1f}%)")
    report.append(f"Missing images: {missing_img}")
    report.append(f"Total headline violations: {violations}")

    # Score (diagnostic only)
    score = 10.0
    if n != TARGET_COUNT: score -= 2.0
    if worst_count > int(MAX_SINGLE_DOMAIN_SHARE * TARGET_COUNT): score -= 2.0
    if official < int(MIN_OFFICIAL_SHARE * TARGET_COUNT): score -= 1.0
    if ages_h and fresh < int(MIN_FRESH_48H_SHARE * len(ages_h)): score -= 1.0
    score -= min(2.0, 0.05 * violations)
    score -= min(1.0, 0.1 * missing_img)
    score = max(0.0, round(score, 1))

    ok = len(fail_reasons) == 0
    head = "PASS ✅ (10/10-ready)" if ok else "FAIL ❌ (blocked)"
    report.append(f"{head} — Score estimate: {score}/10")
    if not ok:
        report.append("Reasons: " + "; ".join(fail_reasons))

    return ok, "\n".join(report)

# === PUBLIC ENTRYPOINT ===
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

    # 4) Final quality gate (blocks if not 10/10 by spec)
    ok, report = final_quality_gate(stage3)
    print("\n=== PAGE QUALITY REPORT ===")
    print(report)
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
