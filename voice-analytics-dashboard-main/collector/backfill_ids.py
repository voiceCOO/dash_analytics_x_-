"""
Backfill specific old posts by tweet id (past the recent user-tweets window).

Reads `backfill_ids.json` = { "<handle>": ["<tweetId>", ...] } and, for each id,
pulls the tweet (metrics + media via /tweet-info), then its scored engagers
(reposters / commenters / quoters) — same data the daily collector gathers for
recent posts. Drops replies and 0-view entries. Geo is added separately via
apply_geo.py after the X-analytics scrape.

  python backfill_ids.py            # merge + rebuild data.json
"""
import json
import os
import sys

import collect
import db
from sorsa import SorsaClient

HERE = os.path.dirname(os.path.abspath(__file__))
IDS_PATH = os.path.join(HERE, "backfill_ids.json")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = collect.load_json(collect.CONFIG_PATH)
    ids_map = collect.load_json(IDS_PATH, {})
    if not ids_map:
        raise SystemExit(f"no backfill_ids.json at {IDS_PATH}")

    client = SorsaClient(api_key=cfg["sorsa"]["apiKey"], base_url=cfg["sorsa"]["baseUrl"],
                         plan=cfg["sorsa"].get("plan", "?"))
    conn = db.connect()
    client._score_cache = db.load_score_cache(conn, cfg.get("scoreCacheDays", 7))
    limit = cfg.get("retweeterSampleLimit", 12)
    added = skipped = miss = 0

    for handle, ids in ids_map.items():
        for tid in ids:
            tid = str(tid)
            t = client.tweet_by_id(handle, tid)
            if not t:
                miss += 1
                print(f"  miss {tid}")
                continue
            if t["is_reply"] or t["metrics"]["impressions"] == 0:
                skipped += 1
                continue
            t["handle"] = handle
            t["category"] = None
            db.upsert_tweet(conn, t)
            m = t["metrics"]
            if m["retweets"] > 0:
                rts = client.scored_retweeters(handle, tid, limit)
                db.replace_retweeters(conn, tid, rts)
                db.replace_engagers(conn, tid, "repost", rts)
            if m["replies"] > 0:
                db.replace_engagers(conn, tid, "comment", client.scored_commenters(handle, tid, limit))
            if m["quotes"] > 0:
                db.replace_engagers(conn, tid, "quote", client.scored_quoters(handle, tid, limit))
            added += 1
            print(f"  +{tid}  {t['created_at']}  imp={m['impressions']}  {t['text'][:34].replace(chr(10),' ')}")
        conn.commit()

    db.save_score_cache(conn, client._score_cache)
    db.log_usage(conn, client.usage())
    manual = collect.load_json(collect.MANUAL_PATH, {})
    collect.export(conn, cfg, manual, False)
    conn.close()
    print(f"\nbackfilled {added}, skipped {skipped} (reply/0-view), miss {miss}; "
          f"Sorsa requests {client.request_count}")


if __name__ == "__main__":
    main()
