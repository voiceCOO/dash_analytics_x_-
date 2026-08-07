"""
VOICE analytics collector — orchestrator.

  python collect.py --mock     # build everything offline from on-disk data
  python collect.py            # real run (needs a Sorsa key in config.json)

Flow:  Sorsa (or mock) --> SQLite (history) --> data.json (dashboard read-model)

The things Sorsa CANNOT give us (per-country audience geo, content category,
human verdict, account baseline) live in manual.json — a single human-editable
file. On first run it is seeded from the existing analytics/data.json so the
real Week-21 work is preserved. Each week you update geo + verdicts there after
the native-X-analytics browser scrape.
"""

import argparse
import json
import os
import re
import statistics
import time
from datetime import date, timedelta

import db
from sorsa import SorsaClient

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYTICS_DIR = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(HERE, "config.json")
MANUAL_PATH = os.path.join(HERE, "manual.json")
OUT_PATH = os.path.join(ANALYTICS_DIR, "data.json")


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# manual.json — seed from existing data.json so nothing is lost
# --------------------------------------------------------------------------
def seed_manual_if_missing():
    if os.path.exists(MANUAL_PATH):
        return load_json(MANUAL_PATH)
    existing = load_json(OUT_PATH, {})
    manual = {"geo": {}, "categories": {}, "verdicts": {}, "baseline": {}}
    weeks = existing.get("weeks", [])
    acct = (existing.get("account", "@voicehavefun")).lstrip("@")
    if weeks:
        manual["baseline"][acct] = weeks[0].get("baseline")
        for w in weeks:
            for p in w.get("posts", []):
                manual["geo"][p["id"]] = p.get("audience")
                manual["categories"][p["id"]] = p.get("category")
                manual["verdicts"][p["id"]] = p.get("verdict")
    save_json(MANUAL_PATH, manual)
    print(f"  seeded manual.json from existing data.json "
          f"({len(manual['geo'])} posts, baseline for {list(manual['baseline'])})")
    return manual


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def classify(text, quoted, fallback=None, media=None):
    """Best-effort auto-category. manual.json overrides this. Taxonomy locked with Ilya:
    Hot Take · Space · Contest/Giveaway · Partnership · Product/Feature ·
    Meme/Vibe · Engagement-bait · News/Update."""
    if fallback:
        return fallback
    t = (text or "").lower()
    words = set(t.split())
    has = lambda *ks: any(k in t for k in ks)
    # an upcoming-event time/date (e.g. "5pm UTC", "jun 17", "2pm")
    event_time = ("utc" in t
                  or bool(re.search(r'\b\d{1,2}\s*(:\d{2})?\s*(am|pm)\b', t))
                  or bool(re.search(r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s*\d{1,2}\b', t)))

    # SPACE — actual spaces AND their announcements (reminder / date-time + space cue / guests)
    if ("x.com/i/spaces" in t or "twitter.com/i/spaces" in t
            or has("reminder", "set your reminders", "voice & chill", "voice and chill",
                   "join our space", "join the space", "tune in", "going live", "🔴",
                   "come find out", "space recording", "listen to the space", "happening now",
                   "save the date", "this thursday", "this friday")
            or (event_time and has("space", "live", "join", "tune", "find out",
                                    "prepare yourself", "go live", "guests", "lineup"))):
        return "Space"
    # strong, unambiguous platform signals win over contest words (e.g. "JUST IN:
    # VOICE added 3 more Core tasks…" is a product update, not a giveaway)
    if has("just in", "core task", "core tasks", "now supports", "we built",
           "shipping", "testnet", "we're hiring", "hiring", "now live", "update is live"):
        return "Product/Feature"
    if has("contest", "giveaway", "give away", "raffle", " win ", " prize", "usdt",
           "pinata", "piñata", "airdrop", "earn punches", "wl spot", "whitelist", "winners announced"):
        return "Contest/Giveaway"
    if has("partner", "collab", "excited to announce", "teaming up", "ama with",
           "we're working with", "proud to", "getting louder"):
        return "Partnership"
    if has("introducing", "now live", "launch", "new feature", "feature", "testnet", "beta",
           "we're hiring", "hiring", "join the team", "roadmap", "just in", "added",
           "core task", "core tasks", "now supports", "shipping", "we built", "is live"):
        return "Product/Feature"
    if has("poll", " vs ", "reply below", "drop a", "tag a", "comment below", "rt if",
           "what's your", "whats your", "agree?", "thoughts?", "below will", "say "):
        return "Engagement-bait"
    if has("milestone", "recap", "this week we", "followers 🥳", "we just hit", "10,000 followers"):
        return "News/Update"
    if has("is dead", "the most", "nobody", "everyone", "unpopular", "hot take", "truth is",
           "stop ", "the only", "be loud", "be signal", " toxic", "overrated", "underrated",
           "opinions >", "you don't need", "imagine "):
        return "Hot Take"
    if ("gm" in words or "gn" in words or has("vibe", "mascot")) or (len(t) < 60 and (media or quoted)):
        return "Meme/Vibe"
    return "Hot Take" if len(t) > 80 else "Meme/Vibe"


def classify_theme(text, category=None):
    """Product-narrative theme (the lens Ilya cares about): VOICE is positioned as
    OpinionFi but content drifts toward SocialFi-style attention/engage-to-earn posts.
    Explicit keyword signals win; otherwise we infer from the content FORMAT, because
    a Hot Take *is* opinion content and a contest/engagement-bait *is* the SocialFi move.
    Returns: OpinionFi · SocialFi · Product · Community · Other."""
    t = (text or "").lower()
    has = lambda *ks: any(k in t for k in ks)
    if has("opinionfi", "opinion-fi", "opinion fi", "unpopular opinion", "your voice",
           "have a voice", "speak up", "be loud", "be signal", "conviction",
           "say what you", "the truth is", "nobody says"):
        return "OpinionFi"
    if has("socialfi", "social-fi", "social fi", "infofi", "info-fi", "yap", "yapping",
           "engage to earn", "engage-to-earn", "attention econ", "mindshare", "creator econ",
           "earn punches", "leaderboard", "quest"):
        return "SocialFi"
    if has("core task", "core tasks", "shipping", "shipped", "testnet", "beta",
           "now supports", "we built", "we shipped", "roadmap", "update is live"):
        return "Product"
    # infer from format — this is what makes the OpinionFi↔SocialFi drift visible
    fmt = category or ""
    if fmt == "Hot Take":
        return "OpinionFi"                                  # hot takes ARE the opinion content
    if fmt in ("Contest/Giveaway", "Engagement-bait"):
        return "SocialFi"                                   # attention / engage-to-earn style
    if fmt == "Product/Feature":
        return "Product"
    if fmt in ("Space", "Meme/Vibe", "News/Update", "Partnership"):
        return "Community"
    return "Other"


def friday_week(d):
    """Return (start_friday, end_thursday) for the Friday->Friday window of date d."""
    days_since_fri = (d.weekday() - 4) % 7  # Monday=0 ... Friday=4
    start = d - timedelta(days=days_since_fri)
    return start, start + timedelta(days=6)


def pct(part, whole):
    return round(part / whole * 100, 2) if whole else 0.0


def tier_of(code, tdefs):
    if code in tdefs["tier1"]:
        return 1
    if code in tdefs["tier2"]:
        return 2
    # everything else = long-tail (CIS / LatAm / Africa / unlisted) -> Tier 3
    return 3


# --------------------------------------------------------------------------
# collect: pull -> sqlite
# --------------------------------------------------------------------------
def collect_account(conn, client, acct, cfg):
    handle, label = acct["handle"], acct.get("label", acct["handle"])
    print(f"\n[{handle}] account stats + tweets…")
    db.upsert_account(conn, handle, label, client.account_stats(handle))

    tweets = client.tweets(handle, days_back=cfg.get("tweetsDaysBack", 16))
    print(f"  {len(tweets)} original posts (paginated, last {cfg.get('tweetsDaysBack', 16)}d)")
    limit = cfg.get("retweeterSampleLimit", 15)
    for t in tweets:
        t["handle"] = handle
        t["category"] = None  # auto-classified at export; manual.json overrides
        # quote-tweets carry no inline media — pull the quoted post's media
        if t.get("is_quote") and not t.get("media"):
            t["media"] = client.tweet_media(handle, t["id"])
        db.upsert_tweet(conn, t)
        m = t["metrics"]
        # Who engaged + their Sorsa score (reposts / comments / quotes).
        # Likers are not collected — Sorsa has no likers endpoint (X blocks it).
        if m["retweets"] > 0:
            rts = client.scored_retweeters(handle, t["id"], limit)
            db.replace_retweeters(conn, t["id"], rts)        # legacy reposters table
            db.replace_engagers(conn, t["id"], "repost", rts)
        if m["replies"] > 0:
            cms = client.scored_commenters(handle, t["id"], limit)
            db.replace_engagers(conn, t["id"], "comment", cms)
        if m["quotes"] > 0:
            qts = client.scored_quoters(handle, t["id"], limit)
            db.replace_engagers(conn, t["id"], "quote", qts)
    conn.commit()
    print(f"  scored {len(client._score_cache)} unique engager accounts (cached)")


# --------------------------------------------------------------------------
# export: sqlite + manual -> data.json
# --------------------------------------------------------------------------
def build_retweeter_block(rows, hq_threshold):
    if not rows:
        return {"count": 0, "avgScore": 0, "medianScore": 0,
                "highQualityPct": 0, "botPct": 0,
                "buckets": {"whale": 0, "strong": 0, "mid": 0, "low": 0}, "top": []}
    scores = [r["score"] or 0 for r in rows]
    buckets = {"whale": 0, "strong": 0, "mid": 0, "low": 0}
    for s in scores:
        if s >= 500: buckets["whale"] += 1
        elif s >= 100: buckets["strong"] += 1
        elif s >= 20: buckets["mid"] += 1
        else: buckets["low"] += 1
    hq = sum(1 for s in scores if s >= hq_threshold)
    bots = [r["bot"] or 0 for r in rows]
    top = [{"handle": r["handle"], "score": round(r["score"] or 0, 1),
            "followers": r["followers"], "category": r["category"]}
           for r in rows[:8]]
    return {
        "count": len(rows),
        "avgScore": round(statistics.mean(scores), 1),
        "medianScore": round(statistics.median(scores), 1),
        "highQualityPct": pct(hq, len(rows)),
        "botPct": round(statistics.mean(bots), 1),
        "buckets": buckets,
        "top": top,
    }


KIND_LABEL = {"repost": "repost", "comment": "reply", "quote": "quote"}


def build_engager_block(rows, hq_threshold):
    """All people who engaged (reposts + comments + quotes) with their scores.
    Deduped to distinct people (one person may both repost and reply — counted
    once for scoring, their actions merged). Likers are NOT here: Sorsa can't
    return them (X blocks that data)."""
    by_kind = {"repost": 0, "comment": 0, "quote": 0}
    for r in rows:
        k = r.get("kind", "repost")
        by_kind[k] = by_kind.get(k, 0) + 1

    # collapse by handle -> {score, followers, kinds set}
    people = {}
    for r in rows:
        h = r["handle"]
        if not h:
            continue
        p = people.setdefault(h, {"handle": h, "score": r["score"] or 0,
                                  "followers": r["followers"], "kinds": []})
        p["kinds"].append(r.get("kind", "repost"))
        p["score"] = max(p["score"], r["score"] or 0)
    plist = sorted(people.values(), key=lambda x: x["score"], reverse=True)

    if not plist:
        return {"count": 0, "actions": 0, "avgScore": 0, "medianScore": 0,
                "highQualityPct": 0, "byKind": by_kind,
                "buckets": {"whale": 0, "strong": 0, "mid": 0, "low": 0}, "top": []}

    scores = [p["score"] for p in plist]
    buckets = {"whale": 0, "strong": 0, "mid": 0, "low": 0}
    for s in scores:
        if s >= 500: buckets["whale"] += 1
        elif s >= 100: buckets["strong"] += 1
        elif s >= 20: buckets["mid"] += 1
        else: buckets["low"] += 1
    hq = sum(1 for s in scores if s >= hq_threshold)
    as_row = lambda p: {"handle": p["handle"], "score": round(p["score"], 1),
                        "followers": p["followers"], "kinds": sorted(set(p["kinds"]))}
    full = [as_row(p) for p in plist]
    return {
        "count": len(plist),                 # distinct people
        "actions": len(rows),                # total engagement actions
        "avgScore": round(statistics.mean(scores), 1),
        "medianScore": round(statistics.median(scores), 1),
        "highQualityPct": pct(hq, len(plist)),
        "byKind": by_kind,
        "buckets": buckets,
        "top": full[:8],                     # collapsed view
        "all": full[:60],                    # full list for the dropdown
    }


def quality_components(engagement, engagers, tier_score):
    """Composite 0-100 = 50% audience quality + 50% engagement rate.
    audience quality = avg engager Sorsa score (norm) blended with tier1+2 reach.
    Returns (composite, audienceQuality, engagementScore). Color assigned later by tercile."""
    er = engagement["engagementRate"]
    er_norm = min(100.0, er / 6.0 * 100.0)          # 6%+ ER -> 100

    avg_eng = engagers["avgScore"] if engagers["count"] else 0
    eng_norm = min(100.0, avg_eng / 3.0)            # avg engager score 300+ -> 100

    if tier_score is not None:
        audience = 0.6 * eng_norm + 0.4 * tier_score["tier1plus2"]
    elif engagers["count"]:
        audience = eng_norm
    else:
        audience = er_norm                          # no signal on audience -> judge on reach

    composite = 0.5 * audience + 0.5 * er_norm
    return round(composite, 1), round(audience, 1), round(er_norm, 1)


def growth_score(engagers, tier_score, reach_multiple):
    """0-100 'did this reach NEW, valuable people' — the content-growth lens, distinct
    from quality (which is audience+engagement). Sorsa-only proxy, blends:
      - spread:        reach× (impressions / our followers) — travelled beyond our bubble
      - valuable geo:  tier-1/2 humans actually reached (absolute)
      - people quality: avg Sorsa score of who engaged
    Graceful fallback when geo / engagers are missing."""
    reach_norm = min(100.0, (reach_multiple or 0) / 10.0 * 100.0)   # ×10 of followers -> 100
    avg_eng = engagers["avgScore"] if engagers["count"] else 0
    eng_norm = min(100.0, avg_eng / 3.0)                            # avg engager 300+ -> 100
    if tier_score is not None:
        tier_norm = min(100.0, (tier_score["tier1plus2People"] or 0) / 5000.0 * 100.0)  # 5k T1/2 ppl -> 100
        g = 0.40 * reach_norm + 0.35 * tier_norm + 0.25 * eng_norm
    elif engagers["count"]:
        g = 0.60 * reach_norm + 0.40 * eng_norm
    else:
        g = reach_norm
    return round(g, 1)


def build_post(conn, t, manual, tdefs, hq, followers=None):
    m = db.latest_metrics(conn, t["id"]) or {}
    imp = m.get("impressions", 0) or 0
    likes, replies = m.get("likes", 0), m.get("replies", 0)
    reposts, quotes, bms = m.get("retweets", 0), m.get("quotes", 0), m.get("bookmarks", 0)
    total_eng = likes + replies + reposts + quotes + bms

    engagement = {
        "impressions": imp, "likes": likes, "replies": replies,
        "reposts": reposts, "quotes": quotes, "bookmarks": bms,
        "engagementRate": pct(total_eng, imp),
        "likeRate": pct(likes, imp), "replyRate": pct(replies, imp),
        "repostRate": pct(reposts, imp), "bookmarkRate": pct(bms, imp),
    }

    rts = db.retweeters_for(conn, t["id"])
    retweeters = build_retweeter_block(rts, hq)
    engagers = build_engager_block(db.engagers_for(conn, t["id"]), hq)

    # media (json string in DB, list in mock)
    media = t.get("media")
    if isinstance(media, str):
        try:
            media = json.loads(media)
        except (ValueError, TypeError):
            media = []
    media = media or []

    # geo / category / verdict from manual layer
    geo = manual.get("geo", {}).get(t["id"])
    tier_score = None
    if geo and geo.get("countries"):
        for c in geo["countries"]:
            c["tier"] = tier_of(c["code"], tdefs)
        t1 = sum(c["pct"] for c in geo["countries"] if c["tier"] == 1)
        t2 = sum(c["pct"] for c in geo["countries"] if c["tier"] == 2)
        t3 = sum(c["pct"] for c in geo["countries"] if c["tier"] == 3)
        other = geo.get("other", 0) or 0
        # ABSOLUTE reach: % of the shown countries × this post's impressions = real humans.
        # (A 100k-view post at 10% T1 reaches more T1 people than a 5k-view Space at 30%.)
        tier_score = {"tier1": round(t1, 1), "tier2": round(t2, 1), "tier3": round(t3, 1),
                      "tier1plus2": round(t1 + t2, 1), "other": round(other, 1),
                      "tracked": round(sum(c["pct"] for c in geo["countries"]), 1),
                      "tier1People": round(imp * t1 / 100),
                      "tier2People": round(imp * t2 / 100),
                      "tier1plus2People": round(imp * (t1 + t2) / 100)}

    category = (manual.get("categories", {}).get(t["id"])
                or classify(t["text"], t.get("quoted", False), t.get("category"), media))
    verdict = manual.get("verdicts", {}).get(t["id"])

    composite, audienceQ, erScore = quality_components(engagement, engagers, tier_score)

    # growth lens: reach× = how far past our own audience this travelled (new-people proxy)
    reach_multiple = round(imp / followers, 1) if followers else None
    theme = classify_theme(t["text"], category)
    growthScore = growth_score(engagers, tier_score, reach_multiple)

    return {
        "id": t["id"], "date": (t["created_at"] or "")[:10], "text": t["text"],
        "category": category,
        "theme": theme,
        "media": media,
        "engagement": engagement,
        "retweeters": retweeters,
        "engagers": engagers,
        "audience": geo,
        "tierScore": tier_score,
        "reachMultiple": reach_multiple,
        "growthScore": growthScore,
        "quality": {"composite": composite, "audienceQuality": audienceQ,
                    "engagementScore": erScore, "color": None},  # color set by tercile
        "verdict": verdict,
    }


def assign_quality_colors(posts):
    """Green/yellow/red by tercile of composite score across the account's posts."""
    scored = [p for p in posts if p["quality"]["composite"] is not None]
    if not scored:
        return
    vals = sorted(p["quality"]["composite"] for p in scored)
    n = len(vals)
    lo = vals[max(0, n // 3 - 1)]
    hi = vals[min(n - 1, (2 * n) // 3)]
    for p in scored:
        c = p["quality"]["composite"]
        if n < 3:
            p["quality"]["color"] = "green" if c >= 55 else "yellow" if c >= 35 else "red"
        else:
            p["quality"]["color"] = "green" if c >= hi else "red" if c <= lo else "yellow"


def week_summary(posts):
    audited = [p for p in posts if p["tierScore"]]
    imps = [p["engagement"]["impressions"] for p in posts] or [0]
    quals = [p["quality"]["composite"] for p in posts] or [0]
    eng_scores = [p["engagers"]["avgScore"] for p in posts if p["engagers"]["count"]]
    best = max(posts, key=lambda p: p["quality"]["composite"], default=None)
    # pooled engagement rate (sum eng / sum impressions) — not a mean of per-post
    # ER, which low-impression posts would blow up into nonsense swings.
    tot_imp = sum(imps)
    tot_eng = sum(p["engagement"]["likes"] + p["engagement"]["replies"]
                  + p["engagement"]["reposts"] + p["engagement"]["quotes"]
                  + p["engagement"]["bookmarks"] for p in posts)
    # category breakdown: count + avg quality + avg impressions per category
    cats = {}
    for p in posts:
        c = p["category"] or "Other"
        cats.setdefault(c, {"count": 0, "q": [], "imp": []})
        cats[c]["count"] += 1
        cats[c]["q"].append(p["quality"]["composite"])
        cats[c]["imp"].append(p["engagement"]["impressions"])
    categories = sorted(
        ({"name": c, "count": v["count"],
          "avgQuality": round(statistics.mean(v["q"])),
          "avgImpressions": round(statistics.mean(v["imp"]))} for c, v in cats.items()),
        key=lambda x: (-x["count"], -x["avgQuality"]))
    # theme breakdown (product-narrative mix): count + avg reach× + avg growth per theme
    ths = {}
    for p in posts:
        th = p.get("theme") or "Other"
        ths.setdefault(th, {"count": 0, "reach": [], "g": []})
        ths[th]["count"] += 1
        if p.get("reachMultiple") is not None:
            ths[th]["reach"].append(p["reachMultiple"])
        ths[th]["g"].append(p.get("growthScore") or 0)
    themes = sorted(
        ({"name": k, "count": v["count"],
          "avgReachMultiple": round(statistics.mean(v["reach"]), 1) if v["reach"] else None,
          "avgGrowthScore": round(statistics.mean(v["g"]), 1) if v["g"] else 0} for k, v in ths.items()),
        key=lambda x: (-x["count"], -x["avgGrowthScore"]))
    rms = [p["reachMultiple"] for p in posts if p.get("reachMultiple") is not None]
    growths = [p.get("growthScore") or 0 for p in posts]
    top_growth = max(posts, key=lambda p: p.get("growthScore") or 0, default=None)
    return {
        "posts": len(posts),
        "totalImpressions": tot_imp,
        "avgImpressions": round(statistics.mean(imps)),
        "avgEngagementRate": pct(tot_eng, tot_imp),
        "avgQuality": round(statistics.mean(quals), 1),
        "avgEngagerScore": round(statistics.mean(eng_scores), 1) if eng_scores else 0,
        "avgReachMultiple": round(statistics.mean(rms), 1) if rms else None,
        "avgGrowthScore": round(statistics.mean(growths), 1) if growths else 0,
        "avgTier1plus2": round(statistics.mean([p["tierScore"]["tier1plus2"] for p in audited]), 1) if audited else None,
        "tier1plus2People": sum(p["tierScore"]["tier1plus2People"] for p in audited) if audited else None,
        "newFollowersDelta": None,  # filled in export() from snapshots (needs week dates)
        "bestPostId": best["id"] if best else None,
        "topGrowthId": top_growth["id"] if top_growth else None,
        "categories": categories,
        "themes": themes,
    }


def _delta(cur, prev):
    if cur is None or prev is None:
        return None
    return round(cur - prev, 1)


def week_deltas(cur, prev):
    """Week-over-week change of the week's own post performance (vs older week)."""
    if not prev:
        return None
    return {
        "avgImpressions": _delta(cur["avgImpressions"], prev["avgImpressions"]),
        "totalImpressions": _delta(cur["totalImpressions"], prev["totalImpressions"]),
        "avgEngagementRate": _delta(cur["avgEngagementRate"], prev["avgEngagementRate"]),
        "avgQuality": _delta(cur["avgQuality"], prev["avgQuality"]),
        "avgEngagerScore": _delta(cur["avgEngagerScore"], prev["avgEngagerScore"]),
        "avgReachMultiple": _delta(cur["avgReachMultiple"], prev["avgReachMultiple"]),
        "avgGrowthScore": _delta(cur["avgGrowthScore"], prev["avgGrowthScore"]),
        "avgTier1plus2": _delta(cur["avgTier1plus2"], prev["avgTier1plus2"]),
        "tier1plus2People": _delta(cur["tier1plus2People"], prev["tier1plus2People"]),
        "posts": _delta(cur["posts"], prev["posts"]),
    }


def week_follower_delta(snaps, start_iso, end_iso):
    """Net followers gained during a Friday→Thursday week, from daily snapshots.
    Baseline = latest snapshot on/before week start; end = latest on/before week end.
    None when the week predates our snapshots (e.g. May, before daily collection)."""
    pts = [s for s in snaps if s.get("followers") is not None]
    if not pts:
        return None
    base = end = None
    for s in pts:
        if s["day"] <= start_iso:
            base = s["followers"]
        if s["day"] <= end_iso:
            end = s["followers"]
    in_week = [s["followers"] for s in pts if start_iso <= s["day"] <= end_iso]
    if base is None and in_week:
        base = in_week[0]
    if end is None and in_week:
        end = in_week[-1]
    if base is None or end is None:
        return None
    return end - base


def export(conn, cfg, manual, mock):
    tdefs = cfg["tierDefinitions"]
    hq = cfg.get("highQualityScore", 100)
    out = {
        "generatedAt": db.now(),
        "lastUpdated": time.strftime("%Y-%m-%d"),
        "mock": mock,
        "tierDefinitions": tdefs,
        "usage": None,
        "accounts": [{"handle": a["handle"], "label": a.get("label", a["handle"]),
                      "primary": a.get("primary", False)} for a in cfg["accounts"]],
        "byAccount": {},
    }
    usage = db.latest_usage(conn)
    if usage:
        out["usage"] = {"used": usage["used"], "limit": usage["lim"], "plan": usage["plan"],
                        "pct": pct(usage["used"], usage["lim"])}

    for a in cfg["accounts"]:
        handle = a["handle"]
        acc = db.account(conn, handle) or {}
        snaps = db.snapshots(conn, handle)
        followers = acc.get("followers")
        posts = [build_post(conn, t, manual, tdefs, hq, followers) for t in db.tweets_for(conn, handle)]
        posts = [p for p in posts if p["engagement"]["impressions"] > 0]  # main posts only, no dead/reply entries
        assign_quality_colors(posts)  # green/yellow/red by tercile across all this account's posts

        # bucket into Friday->Friday weeks
        weeks = {}
        for p in posts:
            if not p["date"]:
                continue
            start, end = friday_week(date.fromisoformat(p["date"]))
            weeks.setdefault(start, []).append(p)

        # build oldest->newest so each week can diff against the previous one
        week_list = []
        prev_summary = None
        for start in sorted(weeks):
            wposts = weeks[start]
            end = start + timedelta(days=6)
            summ = week_summary(wposts)
            summ["newFollowersDelta"] = week_follower_delta(snaps, start.isoformat(), end.isoformat())
            week_list.append({
                "weekNumber": start.isocalendar()[1],
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "label": f"{start.strftime('%b %d')} → {end.strftime('%b %d')}",
                "summary": summ,
                "deltas": week_deltas(summ, prev_summary),
                "posts": wposts,
            })
            prev_summary = summ
        week_list.reverse()  # newest first for display

        out["byAccount"][handle] = {
            "handle": handle,
            "label": a.get("label", handle),
            "stats": {
                "sorsaScore": acc.get("sorsa_score"),
                "scoreWeekDelta": acc.get("score_week_delta"),
                "followers": acc.get("followers"),
                "newFollowers7d": acc.get("new_followers_7d"),
                "following": acc.get("following"),
                "tweetsCount": acc.get("tweets_count"),
                "influencers": acc.get("influencers"),
                "projects": acc.get("projects"),
                "vcs": acc.get("vcs"),
                "botPct": acc.get("bot_pct"),
            },
            "baseline": manual.get("baseline", {}).get(handle),
            "history": snaps,
            "weeks": week_list,
        }

    save_json(OUT_PATH, out)
    print(f"\n✓ wrote {OUT_PATH}")
    print(f"  accounts: {list(out['byAccount'])}")
    for h, b in out["byAccount"].items():
        print(f"   - {h}: {sum(len(w['posts']) for w in b['weeks'])} posts, {len(b['weeks'])} week(s)")


def backup_existing():
    if not os.path.exists(OUT_PATH):
        return
    bdir = os.path.join(ANALYTICS_DIR, "backups")
    os.makedirs(bdir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(bdir, f"data-{stamp}.json")
    with open(OUT_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  backed up old data.json -> backups/data-{stamp}.json")


def run_once(mock=False, quiet=False):
    """Full pipeline: Sorsa -> SQLite -> data.json. Returns a small status dict.
    Callable from the CLI (main) and from the server's Refresh button."""
    cfg = load_json(CONFIG_PATH)
    if not cfg:
        raise RuntimeError("config.json missing")
    manual = seed_manual_if_missing()
    backup_existing()
    key = cfg["sorsa"]["apiKey"]
    if not mock and (not key or key.startswith("PASTE_")):
        raise RuntimeError("No Sorsa key in config.json")

    client = SorsaClient(api_key=key, base_url=cfg["sorsa"]["baseUrl"],
                         mock=mock, plan=cfg["sorsa"].get("plan", "?"))
    conn = db.connect()
    client._score_cache = db.load_score_cache(conn, cfg.get("scoreCacheDays", 7))
    if not quiet:
        print(f"primed score cache: {len(client._score_cache)} accounts")
    for acct in cfg["accounts"]:
        collect_account(conn, client, acct, cfg)
    db.save_score_cache(conn, client._score_cache)
    db.log_usage(conn, client.usage())
    manual = load_json(MANUAL_PATH, manual)
    export(conn, cfg, manual, mock)
    conn.commit()
    conn.close()
    if not quiet:
        print(f"\n  Sorsa requests this run: {client.request_count}")
    return {"ok": True, "requests": client.request_count, "mock": mock,
            "accounts": [a["handle"] for a in cfg["accounts"]]}


def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="run offline with mock Sorsa data")
    args = ap.parse_args()
    run_once(mock=args.mock)


if __name__ == "__main__":
    main()
