
#!/usr/bin/env python3
"""
refresh_manifest.py  (v3 — uses GitHub Releases)
Builds a manifest of player-week and schedules CSVs from nflverse/nflverse-data Releases.
"""
import argparse, json, os, sys, requests

REPO = "nflverse/nflverse-data"
TAGS = {
    "player_stats": "player_stats",   # assets like stats_player_week_YYYY.csv
    "schedules":   "schedules",       # assets like schedules_YYYY.csv
}

def get_release_by_tag(tag, headers):
    url = f"https://api.github.com/repos/{REPO}/releases/tags/{tag}"
    r = requests.get(url, headers=headers, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"GET {url} -> {r.status_code} {r.text[:200]}")
    return r.json()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="manifest_nflverse.json")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    args = ap.parse_args()

    headers = {"Accept": "application/vnd.github+json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    manifest = {"player_stats": [], "schedules": []}

    # Query releases by tag and collect CSV asset download URLs
    for key, tag in TAGS.items():
        try:
            rel = get_release_by_tag(tag, headers)
            for a in rel.get("assets", []):
                name = a.get("name","").lower()
                if name.endswith(".csv") or name.endswith(".csv.gz"):
                    manifest[key].append({
                        "name": a["name"],
                        "download_url": a["browser_download_url"],
                        "tag": tag
                    })
        except Exception as e:
            print(f"[WARN] Could not fetch tag '{tag}': {e}")

    # Fallback: synthetically add URLs for common years if list is empty
    if not manifest["player_stats"]:
        for yr in range(2010, 2030):
            manifest["player_stats"].append({
                "name": f"stats_player_week_{yr}.csv",
                "download_url": f"https://github.com/{REPO}/releases/download/player_stats/stats_player_week_{yr}.csv",
                "tag": "player_stats"
            })
            manifest["player_stats"].append({
                "name": f"stats_player_week_{yr}.csv.gz",
                "download_url": f"https://github.com/{REPO}/releases/download/player_stats/stats_player_week_{yr}.csv.gz",
                "tag": "player_stats"
            })
    # player_stats (the old release) is frozen as of Jan 2022 and has no 2025+
    # assets. nflverse now publishes weekly player stats under a newer,
    # actively-maintained tag: stats_player ("Player Summary Stats"). Pull
    # stats_player_week_<year>.csv/.csv.gz from it ONLY for years not already
    # covered by player_stats, so the already-validated 2010-2024 fetch path
    # is untouched -- this only adds 2025+.
    existing_names = {e["name"] for e in manifest["player_stats"]}
    try:
        rel = get_release_by_tag("stats_player", headers)
        added = 0
        for a in rel.get("assets", []):
            name = a.get("name", "")
            if not (name.startswith("stats_player_week_") and (name.endswith(".csv") or name.endswith(".csv.gz"))):
                continue
            if name in existing_names:
                continue
            manifest["player_stats"].append({
                "name": name,
                "download_url": a["browser_download_url"],
                "tag": "stats_player",
            })
            added += 1
        print(f"[INFO] added {added} stats_player_week file(s) from the current 'stats_player' release (years not already covered by 'player_stats')")
    except Exception as e:
        print(f"[WARN] Could not fetch tag 'stats_player': {e}")

    if not manifest["schedules"]:
        # nflverse publishes one games.csv covering all seasons under the
        # "schedules" release tag (no per-year schedules_YYYY.csv exists).
        manifest["schedules"].append({
            "name": "games.csv",
            "download_url": f"https://github.com/{REPO}/releases/download/schedules/games.csv",
            "tag": "schedules"
        })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"[DONE] Wrote {len(manifest['player_stats'])} player_stats and {len(manifest['schedules'])} schedules -> {args.out}")

if __name__ == "__main__":
    try:
        import requests  # ensure installed
    except Exception:
        print("[ERROR] 'requests' not installed. Run: python -m pip install requests")
        sys.exit(2)
    main()
