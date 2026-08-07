# VOICE X Analytics Dashboard

A local, dependency-free (Python stdlib only) analytics dashboard for the VOICE
project's X/Twitter presence. It answers one question: **what content reaches new,
valuable people so we can double down on it.** Weekly Friday-to-Friday report for
the team, per-post and per-week detail, plus an account-score lookup tool.

Built by Ilya (Shimkey) with Claude across June-July 2026. This repo is the clean
handoff so you can finish it and make it great. Read this whole file first, then
skim the docs in `collector/` and the code. The "Roadmap" section at the bottom is
your to-do list.

---

## 1. What it does

Two accounts are tracked: **@voicehavefun** (VOICE main) and **@killianvoice**
(founder). For each we pull, per post and per week:

- Impressions, likes, replies, retweets, quotes, bookmarks, engagement rate.
- **Reach multiple** (`impressions / followers`) = how far past our own audience a
  post travelled. The core "new people" proxy.
- **Growth score** (0-100) = blend of reach, tier-1/2 people reached, and the Sorsa
  score of the people who engaged.
- **Engagers** (repliers + quoters + retweeters) each scored by their Sorsa Score,
  bucketed whale / strong / mid / low.
- **Geo / tiers**: per-post country split, mapped to tier-1 / tier-2 / tier-3
  (see `collector/config.example.json` -> `tierDefinitions`). Tier data is the one
  thing that is scraped manually from native X analytics (see section 5).
- A weekly **report + narrative verdict** ("what worked, what didn't, do this next").

There is also an **Account Lookup** page: type any @handle, get its Sorsa score,
followers, bot read, and top followers by score.

---

## 2. Stack & architecture

No frameworks, no pip installs. Python 3 standard library + plain HTML/CSS/JS.

```
Sorsa API  ->  collector (Python)  ->  SQLite (voice.db)  ->  data.json  ->  dashboard.html
```

- **Sorsa** (sorsa.io, the rebrand of TweetScout) is the only data engine. REST,
  single `ApiKey` header, no OAuth. It gives tweet metrics, engager lists + their
  Sorsa Score, follower stats, score history. It does NOT give geo (see section 5)
  and has no "likers" endpoint (X blocks that).
- The **collector** pulls from Sorsa, writes to SQLite, and exports a single
  `data.json` that the dashboard reads. The dashboard is 100% static: it just
  fetches `data.json` and renders.
- A tiny **server.py** serves the static files AND three API endpoints
  (`/api/lookup`, `/api/refresh`, `/api/score`) so the Sorsa key stays server-side.

### File map

Root (the dashboard, all static):
- `dashboard.html` - the main app (report, weeks, trends, leaderboard, per-post cards).
- `lookup.html` - the any-@handle Sorsa lookup page.
- `index.html` - redirect to the dashboard.
- `brand.css` - VOICE design tokens (colors, fonts, radii). Lifted from the real product.
- `assets/` - logo, fonts, favicon, wordmark.
- `data.json` - the generated data the dashboard renders (a real sample is included).
- `start-dashboard.bat` - run the local server on :8765.
- `share-dashboard.bat` - run + expose via a Cloudflare quick tunnel.
- `daily-collect.bat` - what the daily scheduled task runs.

`collector/` (the Python backend):
- `sorsa.py` - the Sorsa API client (`SorsaClient`) + a mock backend for offline dev.
- `db.py` - SQLite schema + helpers (`voice.db`).
- `collect.py` - the orchestrator. Pulls everything and exports `data.json`. `--mock` flag.
- `server.py` - static server + `/api/lookup|refresh|score`. Reads the key from config.json.
- `apply_geo.py` + `geo_input.json` - the manual geo pipeline (see section 5).
- `backfill_ids.py` + `backfill_ids.json` - pull old posts by tweet id (past the recent window).
- `manual.json` - human override layer (geo / category / verdicts) merged on export.
- `mock_seed.json` - frozen sample so `--mock` works with no key.
- `config.example.json` - copy to `config.json` and add your Sorsa key.
- `README.md` - collector-specific notes.

`vercel-deploy/` (the public team link, see section 6):
- A static copy of the dashboard + a serverless `api/lookup.py` for the score scanner.

---

## 3. Run it locally

1. Get a Sorsa API key (sorsa.io, the $49/mo 10K-request plan is enough for 2 accounts).
2. `cd collector`, copy `config.example.json` to `config.json`, paste your key into it.
3. From the repo root: double-click `start-dashboard.bat`
   (or `cd collector && python server.py 8765`).
4. Open `http://localhost:8765/dashboard.html`.

No key yet? You can still see the UI on sample data:
`cd collector && python collect.py --mock` then open the dashboard.

Pull fresh data: `cd collector && python collect.py` (this hits Sorsa).

---

## 4. The data pipeline (how a refresh works)

`collect.py`:
1. For each account: pull account stats (score, followers, influential followers)
   and a daily snapshot.
2. Pull recent tweets (paginated back `tweetsDaysBack` days), drop replies and
   0-impression posts.
3. For each tweet: pull engagers (repliers/quoters/retweeters), score each via Sorsa,
   dedupe by handle.
4. Merge the `manual.json` layer (geo, category overrides, verdicts).
5. Compute per-post metrics (reach x, growth score, quality color) and per-week
   summaries (Friday to Friday, WoW deltas).
6. Write everything to `voice.db` and export `data.json`.

Re-export without hitting Sorsa (after editing manual/geo): see the one-liner in
`collector/README.md`.

**Sorsa gotchas already solved** (do not relearn the hard way):
- `/user-tweets` paginates ~19 tweets per page; you must follow `next_cursor` or
  posts go missing.
- Never cache an account's OWN score (it freezes the score chart). `account_stats`
  fetches it fresh.
- Follower / influencer 7-day deltas come from our own daily snapshots, not from
  Sorsa sample counts (those undercount).
- `bot` field is already a percent (0-100), do not multiply by 100.
- Verified endpoint map is in `collector/README.md` and `sorsa.py`.

---

## 5. Geo / tiers (the one manual step, and the biggest open problem)

X shows a post's **Country** breakdown only to the post OWNER, at
`x.com/i/account_analytics/content/<tweet_id>` -> Audience Insights -> Country.
Sorsa does not have it. So today geo is filled semi-manually:

1. Log a browser into **@voicehavefun** (owner-only data).
2. For each post id, open its analytics page, read the top-5 countries + Other.
3. Put names + percentages into `collector/geo_input.json`.
4. `python apply_geo.py --export` maps names to code/flag/tier and rebuilds `data.json`.

This is why the tier-1+2 metric is empty on the freshest weeks (no geo yet). Making
this automatic (or replacing it with a real non-follower-reach signal) is the single
highest-value improvement. See Roadmap.

---

## 6. Deploy (public link for the team)

`vercel-deploy/` is a static build for Vercel, currently live at a project under
Ilya's Vercel account. The dashboard there is view-only (Refresh button hidden), plus
a serverless `api/lookup.py` that powers the score scanner using a `SORSA_API_KEY`
env var (never bundled).

- Deploy: `cd vercel-deploy && npx vercel --prod --yes`.
- Set the key once: `vercel-deploy/set-sorsa-key.bat` (pushes the key from your local
  config.json into Vercel env), then redeploy.
- The shared data is a **snapshot** of `data.json` at deploy time. To refresh what the
  team sees: run the collector, copy `data.json` into `vercel-deploy/`, redeploy.

Note: `vercel.json` deliberately does NOT use `cleanUrls` (it created broken
trailing-slash variants that broke relative asset paths). Keep nav links absolute.

---

## 7. Status

**Working:** live vitals, Friday report + narrative, per-week switcher with WoW
deltas, growth score / reach x, trends charts, engager leaderboard with scores,
per-post quality colors, account lookup, daily collector (Windows scheduled task),
Vercel deploy + serverless lookup.

**Roadmap (finish it and make it great):**
1. **Real "new people" data.** Reach x is a proxy. The real ask is: exact
   non-follower reach % and how many new follows EACH post drove. That needs the X
   scrape (like geo). This is the heart of the project.
2. **Automate geo** (section 5) so tier-1+2 is filled for fresh weeks without hand
   scraping. Biggest quality-of-life win.
3. **Deeper history.** Score/follower/influential charts only build forward from
   daily snapshots. Backfill earlier weeks via `backfill_ids.py` + geo.
4. **Competitor benchmarks.** Add rival handles to `config.accounts` and show VOICE
   vs them. (Handles were never provided.)
5. **Sharing hardening.** Stable named Vercel domain; if the public lookup gets used
   a lot, give it its own Sorsa key/plan so it can't drain the collector's quota.
6. **Theme classifier** (OpinionFi / SocialFi / Product / Community) is heuristic;
   could add a manual theme layer in `manual.json`.

**Constraints that are real, not bugs:**
- No "likers" data (X blocks it; only the paid X API v2 has it).
- Tier-2 near 0% for VOICE is accurate (audience is polarized US/UK vs tier-3 farmers).
- Sorsa has no long history; charts grow as days accrue.
