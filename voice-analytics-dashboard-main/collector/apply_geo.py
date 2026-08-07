"""
Apply scraped per-post geo (X native analytics → Country panel) into manual.json.

X shows each post's audience countries ONLY to the post owner, at
  x.com/i/account_analytics/content/<tweet_id>  →  Audience Insights → Country
(top-5 countries + "Other"). Sorsa has no geo, so this is the one manual feed.

Input file `geo_input.json` (same dir), shape:
  {
    "<tweet_id>": { "countries": [["United States", 18.7], ["Nigeria", 24.4], ...],
                    "other": 35.0 },
    ...
  }
Country names are the human names exactly as X shows them; this script maps them
to codes + flags + tier (tier comes from config.json tierDefinitions at export).

Run:
  python apply_geo.py            # merge geo_input.json -> manual.json
  python apply_geo.py --export   # ...then rebuild data.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MANUAL = os.path.join(HERE, "manual.json")
GEO_IN = os.path.join(HERE, "geo_input.json")

# name -> (ISO-ish code matching config tierDefinitions, flag emoji)
COUNTRY = {
    "United States": ("US", "🇺🇸"), "United Kingdom": ("UK", "🇬🇧"),
    "Germany": ("DE", "🇩🇪"), "France": ("FR", "🇫🇷"), "Netherlands": ("NL", "🇳🇱"),
    "Australia": ("AU", "🇦🇺"), "Canada": ("CA", "🇨🇦"), "Japan": ("JP", "🇯🇵"),
    "Poland": ("PL", "🇵🇱"), "South Korea": ("KR", "🇰🇷"), "Korea": ("KR", "🇰🇷"),
    "Israel": ("IL", "🇮🇱"), "Spain": ("ES", "🇪🇸"), "Italy": ("IT", "🇮🇹"),
    "Sweden": ("SE", "🇸🇪"), "Norway": ("NO", "🇳🇴"), "Denmark": ("DK", "🇩🇰"),
    "Finland": ("FI", "🇫🇮"), "Austria": ("AT", "🇦🇹"), "Switzerland": ("CH", "🇨🇭"),
    "Belgium": ("BE", "🇧🇪"), "Ireland": ("IE", "🇮🇪"), "New Zealand": ("NZ", "🇳🇿"),
    "Nigeria": ("NG", "🇳🇬"), "India": ("IN", "🇮🇳"), "Indonesia": ("ID", "🇮🇩"),
    "Bangladesh": ("BD", "🇧🇩"), "Pakistan": ("PK", "🇵🇰"), "Philippines": ("PH", "🇵🇭"),
    "Vietnam": ("VN", "🇻🇳"),
    # common others (stay tier 0 unless added to config tiers)
    "Turkey": ("TR", "🇹🇷"), "Brazil": ("BR", "🇧🇷"), "Russia": ("RU", "🇷🇺"),
    "Ukraine": ("UA", "🇺🇦"), "Argentina": ("AR", "🇦🇷"), "Mexico": ("MX", "🇲🇽"),
    "Portugal": ("PT", "🇵🇹"), "Romania": ("RO", "🇷🇴"), "Greece": ("GR", "🇬🇷"),
    "Thailand": ("TH", "🇹🇭"), "Malaysia": ("MY", "🇲🇾"), "Singapore": ("SG", "🇸🇬"),
    "United Arab Emirates": ("AE", "🇦🇪"), "Saudi Arabia": ("SA", "🇸🇦"),
    "China": ("CN", "🇨🇳"), "Hong Kong": ("HK", "🇭🇰"), "Taiwan": ("TW", "🇹🇼"),
    "Egypt": ("EG", "🇪🇬"), "Kenya": ("KE", "🇰🇪"), "Ghana": ("GH", "🇬🇭"),
    "South Africa": ("ZA", "🇿🇦"), "Colombia": ("CO", "🇨🇴"), "Venezuela": ("VE", "🇻🇪"),
    "Latvia": ("LV", "🇱🇻"), "Lithuania": ("LT", "🇱🇹"), "Estonia": ("EE", "🇪🇪"),
    "Sri Lanka": ("LK", "🇱🇰"), "Nepal": ("NP", "🇳🇵"), "Morocco": ("MA", "🇲🇦"),
    "Algeria": ("DZ", "🇩🇿"), "Ethiopia": ("ET", "🇪🇹"), "Peru": ("PE", "🇵🇪"),
    "Bolivia": ("BO", "🇧🇴"), "Uzbekistan": ("UZ", "🇺🇿"), "Kazakhstan": ("KZ", "🇰🇿"),
}


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true", help="rebuild data.json after merge")
    args = ap.parse_args()

    geo_in = load(GEO_IN, None)
    if not geo_in:
        raise SystemExit(f"no geo_input.json at {GEO_IN}")
    manual = load(MANUAL, {"geo": {}, "categories": {}, "verdicts": {}, "baseline": {}})
    manual.setdefault("geo", {})

    unknown = set()
    applied = 0
    for tid, blk in geo_in.items():
        countries = []
        for name, pct in blk.get("countries", []):
            code, flag = COUNTRY.get(name, (None, "🏳️"))
            if code is None:
                unknown.add(name)
                code = name[:2].upper()
            countries.append({"code": code, "name": name, "flag": flag, "pct": float(pct)})
        manual["geo"][tid] = {"countries": countries,
                              "other": float(blk.get("other", 0) or 0),
                              "source": "x-analytics"}
        applied += 1
        t1 = sum(c["pct"] for c in countries if c["code"] in
                 ["US", "UK", "DE", "FR", "NL", "AU", "CA", "JP"])
        print(f"  {tid}: {len(countries)} countries, T1≈{round(t1,1)}%")

    with open(MANUAL, "w", encoding="utf-8") as f:
        json.dump(manual, f, ensure_ascii=False, indent=2)
    print(f"✓ merged geo for {applied} post(s) into manual.json")
    if unknown:
        print(f"  ⚠ unmapped country names (add to COUNTRY): {sorted(unknown)}")

    if args.export:
        import collect, db
        cfg = load(os.path.join(HERE, "config.json"), {})
        conn = db.connect()
        collect.export(conn, cfg, manual, False)
        conn.close()


if __name__ == "__main__":
    main()
