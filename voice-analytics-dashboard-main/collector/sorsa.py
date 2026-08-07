"""
Sorsa API client (stdlib only) for api.sorsa.io/v3 + a mock mode.

Verified against the live API (2026-06-11). Key facts:
- Auth: `ApiKey` header. Base: https://api.sorsa.io/v3
- Some endpoints are GET (?username=), some POST (JSON body).
- Tweets carry flat metrics incl. `view_count` (impressions) — no X API needed.
- No batch scoring endpoint: retweeter scores come from per-account /score
  (cached in-run to save quota).

High-level methods (account_stats / tweets / scored_retweeters / usage /
lookup) return one normalized shape so the rest of the app is API-agnostic and
mock and live behave identically.
"""

import json
import os
import random
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYTICS_DIR = os.path.dirname(HERE)
ROOT_DIR = os.path.dirname(ANALYTICS_DIR)


def _load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def parse_twitter_date(s):
    """'Wed Jun 10 21:23:52 +0000 2026' -> ISO date 'YYYY-MM-DD'. Lenient."""
    if not s:
        return ""
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt).astimezone(timezone.utc).date().isoformat()
        except ValueError:
            continue
    return s[:10]


class SorsaError(Exception):
    pass


class SorsaClient:
    def __init__(self, api_key=None, base_url="https://api.sorsa.io/v3", mock=False, plan="10K"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.mock = mock
        self.plan = plan
        self.request_count = 0
        self._score_cache = {}      # handle -> score, dedupes within a run
        self._mock = _Mock() if mock else None

    # ---- transport ----
    def _request(self, method, path, params=None, body=None, retries=3):
        url = self.base_url + path
        data = None
        if method == "GET" and params:
            url += "?" + urllib.parse.urlencode(params)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        last_err = None
        for attempt in range(retries):
            self.request_count += 1
            req = urllib.request.Request(url, data=data, method=method,
                                         headers={"ApiKey": self.api_key or ""})
            if data:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # 4xx are real (don't retry); 5xx may be transient
                if e.code < 500 or attempt == retries - 1:
                    raise SorsaError(f"HTTP {e.code} {method} {path}: {e.read().decode('utf-8','ignore')[:200]}")
                last_err = e
            except (urllib.error.URLError, OSError) as e:
                # transient network / SSL blip — back off and retry
                last_err = e
                if attempt == retries - 1:
                    raise SorsaError(f"Network error {method} {path}: {e}")
            time.sleep(1.2 * (attempt + 1))
        raise SorsaError(f"Failed {method} {path}: {last_err}")

    def _get(self, path, **params):
        return self._request("GET", path, params=params)

    def _post(self, path, **body):
        return self._request("POST", path, body=body)

    # ---- raw endpoints (live shapes) ----
    def info(self, h):              return self._get("/info", username=h)
    def score_raw(self, h):         return self._get("/score", username=h)
    def score_changes(self, h):     return self._get("/score-changes", username=h)
    def followers_stats(self, h):   return self._get("/followers-stats", username=h)
    def new_followers_7d(self, h):  return self._get("/new-followers-7d", username=h)
    def top_followers(self, h):     return self._get("/top-followers", username=h)
    def user_tweets_raw(self, h, n): return self._post("/user-tweets", username=h, max_results=n)
    def retweeters_raw(self, link, n): return self._post("/retweeters", tweet_link=link, max_results=n)
    def comments_raw(self, link, n): return self._post("/comments", tweet_link=link, max_results=n)
    def quotes_raw(self, link, n):  return self._post("/quotes", tweet_link=link, max_results=n)
    def key_usage_info(self):       return self._get("/key-usage-info")

    def score_of(self, handle):
        h = (handle or "").lower()
        if h in self._score_cache:
            return self._score_cache[h]
        try:
            v = self.score_raw(handle).get("score")
        except SorsaError:
            v = None
        self._score_cache[h] = v
        return v

    # ---- high-level, normalized ----
    def usage(self):
        if self.mock:
            return {"used": 1840, "limit": 10000, "plan": self.plan}
        u = self.key_usage_info()
        total, rem = u.get("total_requests"), u.get("remaining_requests")
        used = (total - rem) if (total is not None and rem is not None) else None
        return {"used": used, "limit": total, "plan": self.plan}

    def account_stats(self, handle):
        if self.mock:
            return self._mock.account_stats(handle)
        info = self.info(handle)
        sc = self.score_changes(handle)
        fs = self.followers_stats(handle)
        nf = self.new_followers_7d(handle).get("users", [])
        try:
            fresh_score = self.score_raw(handle).get("score")  # never cached for our own accounts
        except SorsaError:
            fresh_score = self.score_of(handle)
        return {
            "handle": handle,
            "name": info.get("display_name"),
            "sorsaScore": fresh_score,
            "followers": info.get("followers_count"),
            "following": info.get("followings_count"),
            "tweets": info.get("tweets_count"),
            "verified": info.get("verified"),
            "scoreWeekDelta": sc.get("week_delta"),
            "newFollowers7d": len(nf),
            "influencers": fs.get("influencers_count"),
            "projects": fs.get("projects_count"),
            "vcs": fs.get("venture_capitals_count"),
            "botPct": None,  # not exposed by the API on this plan
        }

    @staticmethod
    def _media_from_entities(ents):
        """Sorsa returns `entities` as a list of media dicts: {type, link, preview}."""
        out = []
        for e in (ents or []):
            if isinstance(e, dict) and (e.get("preview") or e.get("link")):
                out.append({"type": e.get("type") or "photo",
                            "preview": e.get("preview") or e.get("link"),
                            "link": e.get("link")})
        return out

    def tweet_media(self, handle, tweet_id):
        """Resolve a post's media via /tweet-info — own entities, and if it's a
        quote-tweet with no own media, the QUOTED tweet's media (Sorsa returns the
        quoted entities empty inline, so we fetch the quoted tweet directly)."""
        try:
            t = self._post("/tweet-info", tweet_link=f"https://x.com/{handle}/status/{tweet_id}")
        except SorsaError:
            return []
        media = self._media_from_entities(t.get("entities"))
        if media:
            return media
        q = t.get("quoted_status") or {}
        qid, qh = q.get("id"), (q.get("user") or {}).get("username")
        qm = self._media_from_entities(q.get("entities"))
        if not qm and qid and qh:
            try:
                qt = self._post("/tweet-info", tweet_link=f"https://x.com/{qh}/status/{qid}")
                qm = self._media_from_entities(qt.get("entities"))
            except SorsaError:
                qm = []
        for m in qm:
            m["quoted"] = True
        return qm

    def tweet_by_id(self, handle, tweet_id):
        """Fetch & normalise a single tweet by id (for backfilling old posts that
        are past the recent user-tweets window). Resolves quoted media too."""
        try:
            t = self._post("/tweet-info", tweet_link=f"https://x.com/{handle}/status/{tweet_id}")
        except SorsaError:
            return None
        nt = self._norm_tweet(t)
        if nt["is_quote"] and not nt["media"]:
            nt["media"] = self.tweet_media(handle, tweet_id)
        return nt

    def _norm_tweet(self, t):
        q = t.get("quoted_status") or {}
        return {
            "id": str(t.get("id")),
            "created_at": parse_twitter_date(t.get("created_at")),
            "text": t.get("full_text", "") or "",
            "quoted": bool(t.get("is_quote_status")) or t.get("quoted_status") is not None,
            "is_quote": bool(t.get("is_quote_status")),
            "quoted_ref": ({"id": str(q.get("id")), "handle": (q.get("user") or {}).get("username")}
                           if q.get("id") else None),
            "is_reply": bool(t.get("is_reply")),
            "media": self._media_from_entities(t.get("entities")),
            "metrics": {
                "impressions": t.get("view_count", 0) or 0,
                "likes": t.get("likes_count", 0) or 0,
                "replies": t.get("reply_count", 0) or 0,
                "retweets": t.get("retweet_count", 0) or 0,
                "quotes": t.get("quote_count", 0) or 0,
                "bookmarks": t.get("bookmark_count", 0) or 0,
            },
        }

    def tweets(self, handle, days_back=16, max_pages=12):
        """Paginate /user-tweets (Sorsa returns only ~19/page regardless of
        max_results, but gives a `next_cursor`) until we've covered `days_back`
        days — so a full week is captured with NO gaps. Drops thread-replies and
        0-view dead entries."""
        if self.mock:
            return self._mock.tweets(handle)
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days_back)).isoformat()
        out, seen, cursor, pages = [], set(), None, 0
        while pages < max_pages:
            body = {"username": handle, "max_results": 100}
            if cursor:
                body["next_cursor"] = cursor
            res = self._post("/user-tweets", **body)
            tws = res.get("tweets", [])
            if not tws:
                break
            page_oldest = "9999"
            for t in tws:
                nt = self._norm_tweet(t)
                if nt["created_at"]:
                    page_oldest = min(page_oldest, nt["created_at"])
                if nt["id"] in seen:
                    continue
                seen.add(nt["id"])
                if nt["is_reply"] or nt["metrics"]["impressions"] == 0:
                    continue
                out.append(nt)
            pages += 1
            cursor = res.get("next_cursor")
            if not cursor or page_oldest < cutoff:
                break
        return out

    def scored_retweeters(self, handle, tweet_id, limit=15):
        if self.mock:
            return self._mock.scored_retweeters(tweet_id, limit)
        link = f"https://x.com/{handle}/status/{tweet_id}"
        try:
            users = self.retweeters_raw(link, limit).get("users", [])
        except SorsaError:
            return []
        return self._score_users(users[:limit])

    def _score_users(self, users):
        out = []
        for u in users:
            out.append({
                "username": u.get("username"),
                "name": u.get("display_name"),
                "score": self.score_of(u.get("username")),
                "followers": u.get("followers_count"),
                "category": None,
                "bot": 0,
            })
        return out

    def _authors_from_tweets(self, tweets, exclude_handle, limit):
        """Dedupe reply/quote authors (skip the post's own account), preserve order."""
        excl = (exclude_handle or "").lower()
        seen, users = set(), []
        for t in tweets:
            u = t.get("user") or {}
            un = (u.get("username") or "").lower()
            if not un or un == excl or un in seen:
                continue
            seen.add(un)
            users.append(u)
            if len(users) >= limit:
                break
        return users

    def scored_commenters(self, handle, tweet_id, limit=15):
        """Who replied to the post, with their Sorsa score (self-replies excluded)."""
        if self.mock:
            return self._mock.scored_retweeters("c" + str(tweet_id), limit)
        link = f"https://x.com/{handle}/status/{tweet_id}"
        try:
            tweets = self.comments_raw(link, max(limit * 3, 30)).get("tweets", [])
        except SorsaError:
            return []
        return self._score_users(self._authors_from_tweets(tweets, handle, limit))

    def scored_quoters(self, handle, tweet_id, limit=15):
        """Who quote-tweeted the post, with their Sorsa score."""
        if self.mock:
            return self._mock.scored_retweeters("q" + str(tweet_id), limit)
        link = f"https://x.com/{handle}/status/{tweet_id}"
        try:
            tweets = self.quotes_raw(link, max(limit * 2, 20)).get("tweets", [])
        except SorsaError:
            return []
        return self._score_users(self._authors_from_tweets(tweets, handle, limit))

    def lookup(self, handle):
        handle = (handle or "").lstrip("@").strip()
        if self.mock:
            return self._mock.lookup(handle)
        info = self.info(handle)
        sc = self.score_changes(handle)
        tf = self.top_followers(handle).get("users", [])
        return {
            "handle": handle,
            "name": info.get("display_name") or handle,
            "sorsaScore": self.score_of(handle),
            "followers": info.get("followers_count"),
            "friends": info.get("followings_count"),
            "tweets": info.get("tweets_count"),
            "verified": info.get("verified"),
            "category": None,
            "botPct": None,
            "scoreWeekDelta": sc.get("week_delta"),
            "topFollowers": [
                {"handle": u.get("username"), "score": u.get("score"),
                 "followers": u.get("followers_count"), "category": None}
                for u in tf[:10]
            ],
        }


# --------------------------------------------------------------------------
# Mock — same normalized shapes, sourced from on-disk data (offline / no quota)
# --------------------------------------------------------------------------
class _Mock:
    def __init__(self):
        self._existing = (_load_json(os.path.join(HERE, "mock_seed.json"))
                          or _load_json(os.path.join(ANALYTICS_DIR, "data.json"), {}))
        pool = _load_json(os.path.join(ROOT_DIR, "sorsa_scores.json"), [])
        self.pool = [p for p in pool if p.get("ok") and p.get("sc") is not None]
        random.seed(42)

    def account_stats(self, handle):
        random.seed(abs(hash(handle)) % 100000)
        followers = 9732 if handle == "voicehavefun" else random.randint(2000, 9000)
        return {
            "handle": handle, "name": handle,
            "sorsaScore": round(random.uniform(180, 320), 1),
            "followers": followers, "following": random.randint(300, 1500),
            "tweets": random.randint(2000, 16000), "verified": True,
            "scoreWeekDelta": round(random.uniform(-5, 25), 1),
            "newFollowers7d": random.randint(120, 720),
            "influencers": random.randint(120, 260), "projects": random.randint(10, 40),
            "vcs": random.randint(0, 6), "botPct": None,
        }

    def tweets(self, handle):
        if handle == "voicehavefun":
            posts = (self._existing.get("weeks", []) or [{}])[0].get("posts", [])
            out = []
            for p in posts:
                e = p.get("engagement", {})
                out.append({
                    "id": p["id"], "created_at": p["date"], "text": p["text"],
                    "quoted": "QRT" in (p.get("category") or ""), "is_reply": False,
                    "metrics": {
                        "impressions": e.get("impressions", 0), "likes": e.get("likes", 0),
                        "replies": e.get("replies", 0), "retweets": e.get("reposts", 0),
                        "quotes": max(0, round(e.get("reposts", 0) * 0.2)),
                        "bookmarks": e.get("bookmarks", round(e.get("likes", 0) * 0.1)),
                    },
                })
            return out
        return self._synth_tweets(handle)

    def _synth_tweets(self, handle):
        seeds = [
            ("the market doesn't reward being early. it rewards being loud at the right time.", 8200, 240, 38, 51, 30),
            ("everyone wants a voice until it's time to actually say something", 5400, 180, 22, 33, 18),
            ("gm to everyone building in silence", 3100, 120, 14, 19, 9),
            ("opinions are the only asset that compounds in this space", 4700, 156, 19, 27, 14),
            ("most of crypto twitter is noise. be signal.", 2600, 98, 11, 12, 6),
            ("we're not here to be neutral", 1900, 71, 8, 9, 4),
        ]
        random.seed(abs(hash(handle)) % 10000)
        out = []
        for i, (text, imp, likes, rt, rep, bm) in enumerate(seeds):
            out.append({
                "id": f"mock-{handle}-{i}", "created_at": f"2026-05-{18 + i:02d}",
                "text": text, "quoted": False, "is_reply": False,
                "metrics": {"impressions": imp, "likes": likes, "replies": rep,
                            "retweets": rt, "quotes": round(rt * 0.25), "bookmarks": bm},
            })
        return out

    def scored_retweeters(self, tweet_id, limit):
        random.seed(abs(hash(tweet_id)) % 100000)
        n = min(len(self.pool), random.randint(8, max(8, limit)))
        sample = random.sample(self.pool, n)
        return [{"username": p.get("sn") or p.get("h"), "name": p.get("nm", ""),
                 "score": p.get("sc"), "followers": p.get("fol"),
                 "category": p.get("cat"), "bot": p.get("bot", 0)} for p in sample]

    def lookup(self, handle):
        key = handle.lower()
        by = {(p.get("sn") or p.get("h") or "").lower(): p for p in self.pool}
        if key in by:
            p = by[key]
            prof = {"name": p.get("nm") or handle, "sorsaScore": p.get("sc"),
                    "followers": p.get("fol"), "friends": p.get("fri"),
                    "tweets": p.get("tw"), "category": p.get("cat"), "botPct": p.get("bot")}
        else:
            random.seed(abs(hash(key)) % 100000)
            prof = {"name": handle, "sorsaScore": round(random.uniform(15, 400), 1),
                    "followers": random.randint(800, 40000), "friends": random.randint(200, 5000),
                    "tweets": random.randint(500, 40000),
                    "category": random.choice(["other", "trader", "project", "kol"]),
                    "botPct": round(random.uniform(0, 30), 1)}
        random.seed(abs(hash("tf" + key)) % 100000)
        tf = random.sample(self.pool, min(len(self.pool), 10))
        return {
            "handle": handle, "verified": False, "scoreWeekDelta": round(random.uniform(-5, 30), 1),
            "topFollowers": [{"handle": x.get("sn") or x.get("h"), "score": x.get("sc"),
                              "followers": x.get("fol"), "category": x.get("cat")} for x in tf],
            **prof,
        }
