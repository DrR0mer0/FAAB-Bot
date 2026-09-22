#!/usr/bin/env python3
"""Fetch play_by_play_<season>.parquet from nflverse's 'pbp' release tag
into nflverse_raw/ (gitignored).

Unlike the other fetch scripts in this repo (which always overwrite --
cheap CSVs that change week to week), a pbp parquet is a full season and
tens of MB; re-downloading an already-complete past season on every run
would be wasteful. So this one is idempotent in the stronger sense: a
season already present on disk is skipped unless --force is passed. A
season still in progress (the current season) should be re-fetched with
--force as the season progresses, same as any other weekly-refreshed file
elsewhere in this pipeline.

Direct download URL, same pattern as fetch_weekly_update.py's
direct_targets: https://github.com/{REPO}/releases/download/pbp/
play_by_play_<season>.parquet -- no manifest lookup needed, the filename
is predictable per season.
"""
import argparse
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
NFLVERSE_REPO = "nflverse/nflverse-data"

# Same season list as the rest of the pipeline: 2019 is a real schema gap
# in the stats releases and is excluded everywhere (see CLAUDE.md's "Data
# sources" note); pbp itself actually has a 2019 file, but it's left out
# here for consistency with what every other table in this DB covers.
DEFAULT_SEASONS = [s for s in range(2010, 2027) if s != 2019]


def fetch(url):
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    return r.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, default=DEFAULT_SEASONS)
    ap.add_argument("--outdir", default=str(REPO_ROOT / "nflverse_raw"))
    ap.add_argument("--force", action="store_true", help="re-download seasons already present on disk")
    args = ap.parse_args()

    out = Path(args.outdir)
    out.mkdir(exist_ok=True)

    ok, skipped, failed = [], [], []
    for season in args.seasons:
        name = f"play_by_play_{season}.parquet"
        dest = out / name
        if dest.exists() and not args.force:
            print(f"[SKIP] {name} already present ({dest.stat().st_size / 1e6:.1f} MB) -- use --force to re-fetch")
            skipped.append(season)
            continue

        url = f"https://github.com/{NFLVERSE_REPO}/releases/download/pbp/{name}"
        print(f"[INFO] Downloading {name} ...")
        try:
            data = fetch(url)
        except Exception as e:
            print(f"[WARN] Failed to download {name}: {e}", file=sys.stderr)
            failed.append(season)
            continue
        dest.write_bytes(data)
        print(f"[OK] wrote {dest} ({len(data) / 1e6:.1f} MB)")
        ok.append(season)

    print(f"\n[DONE] fetched {len(ok)} season(s): {ok}")
    if skipped:
        print(f"[INFO] skipped {len(skipped)} already-present season(s): {skipped}")
    if failed:
        print(f"[WARN] {len(failed)} season(s) failed: {failed}")
        sys.exit(1)


if __name__ == "__main__":
    main()
