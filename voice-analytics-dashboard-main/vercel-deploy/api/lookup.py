"""Vercel serverless endpoint: /api/lookup?handle=<@handle>
Mirrors the local server.py do_lookup output 1:1 so lookup.html works unchanged.
Sorsa key is read from the SORSA_API_KEY env var (never shipped in the bundle).
Warm-instance cache (1h) so repeated scans of the same handle don't re-hit Sorsa.
"""
from http.server import BaseHTTPRequestHandler
import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = "https://api.sorsa.io/v3"
TTL = 3600  # seconds; per warm instance
_CACHE = {}  # handle(lower) -> (ts, result)


def _get(path, username, key):
    url = BASE + path + "?" + urllib.parse.urlencode({"username": username})
    req = urllib.request.Request(url, headers={"ApiKey": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _verdict(sc, bot):
    sc = sc or 0
    bot = bot or 0
    if bot >= 40:
        return "bot-heavy audience - be careful"
    if sc >= 500:
        return "whale-tier CT presence"
    if sc >= 100:
        return "strong, real CT account"
    if sc >= 20:
        return "mid - some real reach"
    return "low score - little CT weight"


def _lookup(handle, key):
    handle = handle.lstrip("@").strip()
    info = _get("/info", handle, key)  # let a bad handle raise -> 502
    try:
        score = (_get("/score", handle, key) or {}).get("score")
    except Exception:
        score = None
    try:
        sc = _get("/score-changes", handle, key) or {}
    except Exception:
        sc = {}
    try:
        tf = (_get("/top-followers", handle, key) or {}).get("users", []) or []
    except Exception:
        tf = []
    out = {
        "handle": handle,
        "name": info.get("display_name") or handle,
        "sorsaScore": score,
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
        "mock": False,
        "cached": False,
    }
    out["verdict"] = _verdict(out["sorsaScore"], out["botPct"])
    return out


class handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        handle = (q.get("handle", [""])[0]).strip().lstrip("@")
        if not handle:
            return self._send({"error": "handle required"}, 400)
        key = os.environ.get("SORSA_API_KEY")
        if not key:
            return self._send({"error": "SORSA_API_KEY not set on server"}, 500)
        h = handle.lower()
        hit = _CACHE.get(h)
        if hit and time.time() - hit[0] < TTL:
            res = dict(hit[1])
            res["cached"] = True
            return self._send(res)
        try:
            res = _lookup(handle, key)
        except urllib.error.HTTPError as e:
            return self._send({"error": "Sorsa HTTP %d" % e.code}, 502)
        except Exception as e:
            return self._send({"error": str(e)}, 502)
        _CACHE[h] = (time.time(), res)
        return self._send(res)
