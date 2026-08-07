"""
Local server for the VOICE dashboard.

Serves the static dashboard AND a /api/lookup endpoint so you can check any
account's Sorsa score / bot % / top followers from the dashboard itself — no
Sorsa web subscription needed, the API key stays server-side (never in the
browser). Results are cached in SQLite to save quota.

  py server.py            # port 8765
  py server.py 8766       # custom port

Runs in MOCK mode automatically if config.json has no real key, so it works
offline against the local sample pool.
"""

import json
import os
import sys
import threading
import urllib.parse
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import collect
import db
from sorsa import SorsaClient

REFRESH_LOCK = threading.Lock()
import time as _time
_SCORE_CACHE = {}  # handle -> (ts, {score, followers})

HERE = os.path.dirname(os.path.abspath(__file__))
ANALYTICS_DIR = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(HERE, "config.json")

with open(CONFIG_PATH, encoding="utf-8") as f:
    CFG = json.load(f)

KEY = CFG["sorsa"]["apiKey"]
MOCK = (not KEY) or KEY.startswith("PASTE_")
CLIENT = SorsaClient(api_key=KEY, base_url=CFG["sorsa"]["baseUrl"], mock=MOCK)
CACHE_DAYS = CFG.get("scoreCacheDays", 7)


def verdict(p):
    bot = p.get("botPct") or 0
    sc = p.get("sorsaScore") or 0
    if bot >= 40:
        return "bot-heavy audience — be careful"
    if sc >= 500:
        return "whale-tier CT presence"
    if sc >= 100:
        return "strong, real CT account"
    if sc >= 20:
        return "mid — some real reach"
    return "low score — little CT weight"


def do_lookup(handle):
    handle = handle.lstrip("@").strip()
    conn = db.connect()
    cached = db.get_lookup(conn, handle, CACHE_DAYS)
    if cached:
        conn.close()
        cached["cached"] = True
        return cached

    profile = CLIENT.lookup(handle)
    profile["mock"] = MOCK
    profile["cached"] = False
    profile["verdict"] = verdict(profile)
    db.set_lookup(conn, handle, profile)
    conn.close()
    return profile


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/lookup":
            qs = urllib.parse.parse_qs(parsed.query)
            handle = (qs.get("handle", [""])[0]).strip()
            if not handle:
                return self._json({"error": "handle required"}, 400)
            try:
                return self._json(do_lookup(handle))
            except Exception as e:
                return self._json({"error": str(e)}, 502)
        if parsed.path == "/api/refresh":
            return self.do_refresh()
        if parsed.path == "/api/score":
            return self.do_score()
        return super().do_GET()

    def do_score(self):
        """Live current Sorsa score + followers per account (2-min cache), so the
        headline numbers stay fresh between full collector runs."""
        if MOCK:
            return self._json({})
        out = {}
        for a in CFG["accounts"]:
            h = a["handle"]
            cached = _SCORE_CACHE.get(h)
            if cached and _time.time() - cached[0] < 120:
                out[h] = cached[1]
                continue
            try:
                info = CLIENT.info(h)
                val = {"score": CLIENT.score_raw(h).get("score"),
                       "followers": info.get("followers_count")}
                _SCORE_CACHE[h] = (_time.time(), val)
                out[h] = val
            except Exception:
                out[h] = None
        return self._json(out)

    def do_refresh(self):
        if MOCK:
            return self._json({"error": "MOCK mode — add a real Sorsa key to refresh"}, 400)
        if not REFRESH_LOCK.acquire(blocking=False):
            return self._json({"error": "refresh already running"}, 409)
        try:
            res = collect.run_once(mock=False, quiet=True)
            return self._json(res)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        finally:
            REFRESH_LOCK.release()

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    httpd = ThreadingHTTPServer(("", port), partial(Handler, directory=ANALYTICS_DIR))
    mode = "MOCK (no key)" if MOCK else "LIVE (Sorsa key)"
    print(f"VOICE dashboard + lookup API -> http://localhost:{port}/  [{mode}]")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
