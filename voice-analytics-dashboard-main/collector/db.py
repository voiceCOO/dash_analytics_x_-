"""
SQLite store for the VOICE analytics collector (stdlib sqlite3 only).

Why a DB and not just data.json: metrics ripen over 24-48h and we want
week-over-week growth, so we snapshot metrics each run and keep history.
data.json is the read-model the dashboard consumes; it is rebuilt from here.
"""

import os
import sqlite3
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "voice.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  handle TEXT PRIMARY KEY,
  label TEXT,
  name TEXT,
  sorsa_score REAL,
  followers INTEGER,
  following INTEGER,
  tweets_count INTEGER,
  verified INTEGER,
  score_week_delta REAL,
  new_followers_7d INTEGER,
  influencers INTEGER,
  projects INTEGER,
  vcs INTEGER,
  bot_pct REAL,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS account_snapshots (
  handle TEXT, ts TEXT, sorsa_score REAL, followers INTEGER, influencers INTEGER,
  PRIMARY KEY (handle, ts)
);

CREATE TABLE IF NOT EXISTS tweets (
  id TEXT PRIMARY KEY,
  handle TEXT,
  created_at TEXT,
  text TEXT,
  category TEXT,
  media TEXT
);

CREATE TABLE IF NOT EXISTS tweet_metrics (
  tweet_id TEXT, ts TEXT,
  impressions INTEGER, likes INTEGER, replies INTEGER,
  retweets INTEGER, quotes INTEGER, bookmarks INTEGER,
  PRIMARY KEY (tweet_id, ts)
);

CREATE TABLE IF NOT EXISTS retweeters (
  tweet_id TEXT, handle TEXT, score REAL, followers INTEGER,
  category TEXT, bot REAL, ts TEXT,
  PRIMARY KEY (tweet_id, handle)
);

-- Unified engagers: who interacted with a post and how (repost / comment / quote).
-- Likers are not here — Sorsa has no likers endpoint (X restricts that data).
CREATE TABLE IF NOT EXISTS engagers (
  tweet_id TEXT, handle TEXT, kind TEXT, score REAL, followers INTEGER,
  category TEXT, bot REAL, ts TEXT,
  PRIMARY KEY (tweet_id, handle, kind)
);

CREATE TABLE IF NOT EXISTS usage_log (
  ts TEXT PRIMARY KEY, used INTEGER, lim INTEGER, plan TEXT
);

CREATE TABLE IF NOT EXISTS lookup_cache (
  handle TEXT PRIMARY KEY, ts REAL, payload TEXT
);

CREATE TABLE IF NOT EXISTS score_cache (
  handle TEXT PRIMARY KEY, score REAL, ts REAL
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # lightweight migration: add columns to pre-existing tables
    for table, col, decl in [("tweets", "media", "TEXT"),
                             ("account_snapshots", "influencers", "INTEGER")]:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    conn.commit()
    return conn


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def upsert_account(conn, handle, label, s):
    conn.execute(
        """INSERT INTO accounts(handle,label,name,sorsa_score,followers,following,tweets_count,
             verified,score_week_delta,new_followers_7d,influencers,projects,vcs,bot_pct,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(handle) DO UPDATE SET
             label=excluded.label, name=excluded.name, sorsa_score=excluded.sorsa_score,
             followers=excluded.followers, following=excluded.following,
             tweets_count=excluded.tweets_count, verified=excluded.verified,
             score_week_delta=excluded.score_week_delta, new_followers_7d=excluded.new_followers_7d,
             influencers=excluded.influencers, projects=excluded.projects, vcs=excluded.vcs,
             bot_pct=excluded.bot_pct, updated_at=excluded.updated_at""",
        (handle, label, s.get("name"), s.get("sorsaScore"), s.get("followers"),
         s.get("following"), s.get("tweets"), 1 if s.get("verified") else 0,
         s.get("scoreWeekDelta"), s.get("newFollowers7d"), s.get("influencers"),
         s.get("projects"), s.get("vcs"), s.get("botPct"), now()),
    )
    conn.execute(
        "INSERT OR REPLACE INTO account_snapshots(handle,ts,sorsa_score,followers,influencers) VALUES(?,?,?,?,?)",
        (handle, now(), s.get("sorsaScore"), s.get("followers"), s.get("influencers")),
    )


def upsert_tweet(conn, t):
    media = json.dumps(t.get("media", []), ensure_ascii=False)
    conn.execute(
        """INSERT INTO tweets(id,handle,created_at,text,category,media) VALUES(?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             text=excluded.text, category=COALESCE(excluded.category, tweets.category),
             media=excluded.media""",
        (t["id"], t["handle"], t["created_at"], t["text"], t.get("category"), media),
    )
    m = t["metrics"]
    conn.execute(
        """INSERT OR REPLACE INTO tweet_metrics
           (tweet_id,ts,impressions,likes,replies,retweets,quotes,bookmarks)
           VALUES(?,?,?,?,?,?,?,?)""",
        (t["id"], now(), m.get("impressions", 0), m.get("likes", 0),
         m.get("replies", 0), m.get("retweets", 0),
         m.get("quotes", 0), m.get("bookmarks", 0)),
    )


def replace_retweeters(conn, tweet_id, accounts):
    conn.execute("DELETE FROM retweeters WHERE tweet_id=?", (tweet_id,))
    ts = now()
    conn.executemany(
        """INSERT OR REPLACE INTO retweeters
           (tweet_id,handle,score,followers,category,bot,ts) VALUES(?,?,?,?,?,?,?)""",
        [(tweet_id, a["username"], a.get("score"), a.get("followers"),
          a.get("category"), a.get("bot"), ts) for a in accounts],
    )


def replace_engagers(conn, tweet_id, kind, accounts):
    """Store one engagement kind ('repost'|'comment'|'quote') for a tweet."""
    conn.execute("DELETE FROM engagers WHERE tweet_id=? AND kind=?", (tweet_id, kind))
    ts = now()
    conn.executemany(
        """INSERT OR REPLACE INTO engagers
           (tweet_id,handle,kind,score,followers,category,bot,ts) VALUES(?,?,?,?,?,?,?,?)""",
        [(tweet_id, a["username"], kind, a.get("score"), a.get("followers"),
          a.get("category"), a.get("bot"), ts) for a in accounts if a.get("username")],
    )


def engagers_for(conn, tweet_id, kind=None):
    if kind:
        rows = conn.execute(
            "SELECT * FROM engagers WHERE tweet_id=? AND kind=? ORDER BY score DESC",
            (tweet_id, kind)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM engagers WHERE tweet_id=? ORDER BY score DESC", (tweet_id,)).fetchall()
    return [dict(r) for r in rows]


def log_usage(conn, usage):
    conn.execute(
        "INSERT OR REPLACE INTO usage_log(ts,used,lim,plan) VALUES(?,?,?,?)",
        (now(), usage.get("used"), usage.get("limit"), usage.get("plan")),
    )


# ---- read side (latest metric snapshot per tweet) ----
def latest_metrics(conn, tweet_id):
    row = conn.execute(
        "SELECT * FROM tweet_metrics WHERE tweet_id=? ORDER BY ts DESC LIMIT 1", (tweet_id,)
    ).fetchone()
    return dict(row) if row else None


def tweets_for(conn, handle):
    rows = conn.execute(
        "SELECT * FROM tweets WHERE handle=? ORDER BY created_at DESC", (handle,)
    ).fetchall()
    return [dict(r) for r in rows]


def retweeters_for(conn, tweet_id):
    rows = conn.execute(
        "SELECT * FROM retweeters WHERE tweet_id=? ORDER BY score DESC", (tweet_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def account(conn, handle):
    row = conn.execute("SELECT * FROM accounts WHERE handle=?", (handle,)).fetchone()
    return dict(row) if row else None


def latest_usage(conn):
    row = conn.execute("SELECT * FROM usage_log ORDER BY ts DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def snapshots(conn, handle):
    """One point per day (latest of each day) for score/followers/influential charts."""
    rows = conn.execute(
        """SELECT substr(ts,1,10) AS day, MAX(ts) AS mts, sorsa_score, followers, influencers
           FROM account_snapshots WHERE handle=?
           GROUP BY day ORDER BY day""", (handle,)).fetchall()
    return [{"day": r["day"], "score": r["sorsa_score"], "followers": r["followers"],
             "influencers": r["influencers"]} for r in rows]


def get_lookup(conn, handle, max_age_days):
    row = conn.execute("SELECT ts, payload FROM lookup_cache WHERE handle=?",
                       (handle.lower(),)).fetchone()
    if not row:
        return None
    if time.time() - (row["ts"] or 0) > max_age_days * 86400:
        return None
    return json.loads(row["payload"])


def set_lookup(conn, handle, payload):
    conn.execute("INSERT OR REPLACE INTO lookup_cache(handle,ts,payload) VALUES(?,?,?)",
                 (handle.lower(), time.time(), json.dumps(payload)))
    conn.commit()


def load_score_cache(conn, max_age_days):
    """Fresh handle->score map, so daily runs don't re-score the same accounts."""
    cutoff = time.time() - max_age_days * 86400
    rows = conn.execute("SELECT handle, score FROM score_cache WHERE ts >= ?", (cutoff,)).fetchall()
    return {r["handle"]: r["score"] for r in rows}


def save_score_cache(conn, cache):
    ts = time.time()
    conn.executemany("INSERT OR REPLACE INTO score_cache(handle,score,ts) VALUES(?,?,?)",
                     [(h, s, ts) for h, s in cache.items()])
    conn.commit()
