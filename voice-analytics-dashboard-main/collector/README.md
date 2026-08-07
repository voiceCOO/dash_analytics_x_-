# VOICE Analytics Collector

Pulls Twitter/X analytics from the **Sorsa API** into the local dashboard.

```
Sorsa API  ──►  SQLite (voice.db, history)  ──►  ../data.json  ──►  dashboard.html
```

## Run it

```powershell
# offline demo, no key needed (uses real Week-21 posts + real Sorsa scores)
py collect.py --mock

# real run — needs a Sorsa key in config.json
py collect.py
```

Then view: double-click `..\start-dashboard.bat`, or `py -m http.server 8765`
in the `analytics\` folder and open http://localhost:8765/

## Plug in your Sorsa key

Edit `config.json` → `sorsa.apiKey`. Confirm `sorsa.baseUrl` and the exact
endpoint paths against https://docs.sorsa.io once the key is live (the client in
`sorsa.py` follows the documented endpoint names; a path tweak may be needed).

The `$49 / 10K requests` plan is enough for 2 accounts — a full run uses ~26
requests. Watch the **usage gauge** in the dashboard header (real runs log
`/key-usage-info`).

## What's automatic vs manual

| Data | Source |
|------|--------|
| Impressions, likes, reposts, replies, quotes, bookmarks, engagement % | **Sorsa (auto)** |
| Reposters + their Sorsa Score, bot %, quality buckets | **Sorsa (auto)** |
| Follower count, growth, account Sorsa Score | **Sorsa (auto)** |
| Per-country audience (geo / Tier 1-2-3) | **manual.json** — Sorsa has no geo |
| Content category, verdict, weekly baseline | **manual.json** |

`manual.json` is the one file you hand-edit. It was seeded from the existing
Week-21 work so nothing was lost.

## Weekly (Friday) workflow

1. `py collect.py` — pull fresh metrics + reposter scores
2. Scrape geo from `x.com/i/account_analytics` (audience + per-post countries),
   update `manual.json` → `geo` and `baseline`
3. Add `category` + `verdict` for new posts in `manual.json`
4. `py collect.py` again to merge → dashboard refreshes

(Steps 1–4 will be automated to run daily + a Friday-10:00 report once the key
is in and the geo-scrape step is wired through the browser.)

## Files

- `config.json` — Sorsa key, accounts, tier definitions
- `manual.json` — human layer: geo / category / verdict / baseline
- `sorsa.py` — Sorsa API client (stdlib urllib) + mock backend
- `db.py` — SQLite schema + read/write helpers
- `collect.py` — orchestrator (`--mock` flag)
- `voice.db` — SQLite history (auto-created)
- `mock_seed.json` — frozen sample so `--mock` is reproducible
