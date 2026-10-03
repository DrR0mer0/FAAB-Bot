#!/usr/bin/env python3
"""Fetch nflverse snap counts and load them into tr_snap_counts.

Source: nflverse-data release tag `snap_counts`, asset snap_counts_<season>.csv
(2012+). That file keys players by PFR id (`pfr_player_id`); the rest of this
pipeline keys on gsis_id, so rows are crosswalked through nflverse_raw/
players.csv (pfr_id -> gsis_id). Rows with no crosswalk are skipped and
counted, never guessed at. Only regular-season rows are loaded. Loading is
idempotent (INSERT OR REPLACE) -- snap counts are a loaded data table, not
part of the append-only ledger. Like every other nflverse file this is
re-fetched when the week's numbers settle (the release lags the games by
about a day), which is why each tracker run records the latest snap week it
actually had.
"""
import csv
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
RELEASE_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
OFFENSE_POSITIONS = None  # keep every position; the heuristic only ever looks up pool players


def fetch_season(season, raw_dir, timeout=120):
    url = RELEASE_URL.format(season=season)
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    dest = Path(raw_dir) / f"snap_counts_{season}.csv"
    dest.write_bytes(r.content)
    return dest


def pfr_to_gsis(players_csv):
    m = {}
    with open(players_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("pfr_id") and r.get("gsis_id"):
                m[r["pfr_id"]] = r["gsis_id"]
    return m


def _f(v):
    try:
        return float(v) if v not in (None, "", "NA") else None
    except ValueError:
        return None


def load_season(con, season, raw_dir, crosswalk):
    path = Path(raw_dir) / f"snap_counts_{season}.csv"
    if not path.exists():
        return {"season": season, "loaded": 0, "unmapped": 0, "missing_file": True}
    rows, unmapped = [], 0
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("game_type") not in (None, "", "REG"):
                continue
            gsis = crosswalk.get(r.get("pfr_player_id"))
            if gsis is None:
                unmapped += 1
                continue
            try:
                week = int(r["week"])
            except (KeyError, ValueError):
                continue
            rows.append((season, week, gsis, r.get("pfr_player_id"), r.get("player"), r.get("position"),
                         r.get("team"), _f(r.get("offense_snaps")), _f(r.get("offense_pct"))))
    con.executemany(
        "INSERT OR REPLACE INTO tr_snap_counts (season, week, player_id, pfr_player_id, name, position, team, "
        "offense_snaps, offense_pct) VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    return {"season": season, "loaded": len(rows), "unmapped": unmapped, "missing_file": False}


def fetch_and_load(con, seasons, raw_dir=None, players_csv=None, do_fetch=True):
    raw_dir = Path(raw_dir or REPO_ROOT / "nflverse_raw")
    players_csv = Path(players_csv or raw_dir / "players.csv")
    crosswalk = pfr_to_gsis(players_csv)
    results = []
    for s in seasons:
        if do_fetch:
            try:
                fetch_season(s, raw_dir)
            except Exception as e:  # noqa: BLE001 -- report and keep going
                print(f"[WARN] snap_counts {s}: fetch failed ({e}); using any file already on disk", file=sys.stderr)
        res = load_season(con, s, raw_dir, crosswalk)
        print(f"[INFO] snap_counts {s}: loaded {res['loaded']} rows, {res['unmapped']} without a gsis crosswalk"
              + (" (no file)" if res["missing_file"] else ""))
        results.append(res)
    return results
