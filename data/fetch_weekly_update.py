#!/usr/bin/env python3
"""One-shot weekly refresh for one season: player-week stats, schedules
(games.csv, which also carries betting lines), and BOTH roster files --
the season-level snapshot (roster_<season>.csv, used by data/crosscheck_
roster_2026.py) and the true per-week roster (roster_weekly_<season>.csv,
the source model/score_week.py's team-mapping, new-competitor, and
availability checks actually read).

Idempotent, like the other fetch scripts here: always overwrites the
local file with whatever's currently published, safe to re-run any time.
Run this before model/score_week.py and evaluation/verify_week.py each
week -- a stale roster file is otherwise invisible until someone notices
wrong team-change or new-competitor suppressions (see CLAUDE.md); this is
the fix, not a one-off patch.

Uses direct release-download URLs for schedules/rosters/weekly_rosters
(each a single, non-deprecated release tag with a predictable per-season
filename) rather than resolving them through manifest_nflverse.json, which
only exists to handle stats_player_week's messier multi-tag history
(player_stats -> stats_player). Player-week stats are still resolved via
that tag directly here (not through the manifest file, so this script has
no dependency on data/refresh_manifest.py having been run first).
"""
import argparse
import gzip
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
NFLVERSE_REPO = "nflverse/nflverse-data"
API_BASE = f"https://api.github.com/repos/{NFLVERSE_REPO}"


def fetch(url, headers=None):
    r = requests.get(url, headers=headers, timeout=60)
    r.raise_for_status()
    return r.content


def maybe_gunzip_to(bytes_in, out_path: Path):
    try:
        data = gzip.decompress(bytes_in)
        out_path.write_bytes(data)
    except Exception:
        out_path.write_bytes(bytes_in)


def resolve_stats_url(season, headers):
    """The stats_player_week_<season>.csv asset lives under the current
    'stats_player' release tag (see CLAUDE.md's "Data sources" note on the
    player_stats -> stats_player migration)."""
    resp = requests.get(f"{API_BASE}/releases/tags/stats_player", headers=headers, timeout=60)
    resp.raise_for_status()
    name = f"stats_player_week_{season}.csv"
    for a in resp.json().get("assets", []):
        if a["name"] == name:
            return a["browser_download_url"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--outdir", default=str(REPO_ROOT / "nflverse_raw"))
    ap.add_argument("--token", default=None,
                     help="GitHub token for the stats-tag lookup API call; omit for an anonymous request "
                          "(a stale/invalid ambient GITHUB_TOKEN env var 401s authenticated calls -- "
                          "anonymous works fine for these public-repo lookups)")
    args = ap.parse_args()

    out = Path(args.outdir)
    out.mkdir(exist_ok=True)
    headers = {"Accept": "application/vnd.github+json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"

    failed = []

    print(f"[INFO] resolving stats_player_week_{args.season}.csv from the 'stats_player' release ...")
    try:
        stats_url = resolve_stats_url(args.season, headers)
    except Exception as e:
        print(f"[WARN] Could not query the stats_player release: {e}", file=sys.stderr)
        stats_url = None
    if stats_url:
        print(f"[INFO] Downloading stats_player_week_{args.season}.csv ...")
        maybe_gunzip_to(fetch(stats_url), out / f"stats_player_week_{args.season}.csv")
    else:
        print(f"[WARN] stats_player_week_{args.season}.csv not found in the 'stats_player' release", file=sys.stderr)
        failed.append(f"stats_player_week_{args.season}.csv")

    direct_targets = [
        ("schedules", "games.csv"),
        ("rosters", f"roster_{args.season}.csv"),
        ("weekly_rosters", f"roster_weekly_{args.season}.csv"),
    ]
    for tag, filename in direct_targets:
        url = f"https://github.com/{NFLVERSE_REPO}/releases/download/{tag}/{filename}"
        print(f"[INFO] Downloading {filename} (tag '{tag}') ...")
        try:
            maybe_gunzip_to(fetch(url), out / filename)
        except Exception as e:
            print(f"[WARN] Failed to download {filename}: {e}", file=sys.stderr)
            failed.append(filename)

    if failed:
        print(f"[WARN] {len(failed)} file(s) failed to refresh: {failed}")
        sys.exit(1)
    print(f"[DONE] refreshed stats/games/rosters for season {args.season} in {out}")


if __name__ == "__main__":
    main()
