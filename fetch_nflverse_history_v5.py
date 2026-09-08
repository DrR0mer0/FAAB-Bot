#!/usr/bin/env python3
"""Fetch stats_player_week_<season>.csv for a list of seasons, via the manifest.

Schedules (games.csv) are season-agnostic and fetched separately (see
fetch_quick_2022.py) -- this script only pulls per-season player-week files.
Soft-fails per season: a missing/broken season is logged and skipped rather
than aborting the whole run.
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

import requests


def fetch(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def maybe_gunzip_to(bytes_in, out_path: Path):
    try:
        data = gzip.decompress(bytes_in)
        out_path.write_bytes(data)
        return True
    except Exception:
        out_path.write_bytes(bytes_in)
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seasons", nargs="+", type=int, required=True)
    ap.add_argument("--manifest", default="manifest_nflverse.json")
    ap.add_argument("--outdir", default="nflverse_raw")
    args = ap.parse_args()

    out = Path(args.outdir)
    out.mkdir(exist_ok=True)
    man = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    by_name = {e["name"]: e["download_url"] for e in man.get("player_stats", [])}

    ok, failed = [], []
    for season in args.seasons:
        name = f"stats_player_week_{season}.csv"
        url = by_name.get(name)
        if not url:
            print(f"[WARN] {name} not in manifest; skipping season {season}", file=sys.stderr)
            failed.append(season)
            continue
        print(f"[INFO] Downloading {name} ...")
        try:
            data = fetch(url)
        except Exception as e:
            print(f"[WARN] Failed to download {name}: {e}", file=sys.stderr)
            failed.append(season)
            continue
        maybe_gunzip_to(data, out / name)
        ok.append(season)

    print(f"[DONE] fetched {len(ok)} seasons: {ok}")
    if failed:
        print(f"[WARN] {len(failed)} seasons failed/skipped: {failed}")


if __name__ == "__main__":
    main()
