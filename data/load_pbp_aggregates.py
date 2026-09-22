#!/usr/bin/env python3
"""Aggregate nflverse_raw/play_by_play_<season>.parquet into team_week_pbp_stats
and player_week_pbp_stats -- pre-aggregated metrics only; the raw play-by-play
is read from the cached parquet and never persisted to the DB.

Metric definitions (see data/_init_schema.sql for the full column-level
comment):
  - "dropback" = nflverse's own qb_dropback column (pass attempts + sacks +
    scrambles) -- the standard sack-rate denominator.
  - "rush attempt" = rush_attempt==1, which nflverse classifies a QB
    scramble under (play_type='run') -- a stuff rate computed this way
    includes scrambles, not just designed runs.
  - "stuff" = a rush_attempt row with yards_gained <= 0.
  - pass_rate = pass plays / (pass plays + run plays), both counted only
    from the offense's own snaps (play_type in ('pass','run')).
  - plays_inside_20 = offensive scrimmage plays (pass or run) with
    yardline_100 <= 20 -- a count, not a rate; offense side only.
  - redzone_carries / redzone_targets = player-level carries/targets on a
    play with yardline_100 <= 20; a target requires a non-null
    receiver_player_id (excludes sacks and no-target throwaways).

Regular season only (season_type == 'REG' in the source file) -- pbp
labels this directly, unlike the rest of this pipeline's playoff-exclusion
logic which has to cross-reference nfl_games.

TEAM_ALIASES (data/team_crosswalk.py) is applied defensively to posteam/
defteam even though empirical inspection of this release (checked 2010,
2018, and 2026) shows nflverse's pbp already uses the CURRENT franchise
code for every season, the same convention as player_week_stats -- e.g.
2010's Rams-era rows already say 'LA', not 'STL'. So this crosswalk is a
no-op here in practice (norm_team() returns any current code unchanged),
kept for defense-in-depth rather than because a mismatch was found; see
the load report this script prints for confirmation on real data.
"""
import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "data"))
from team_crosswalk import norm_team

DEFAULT_SEASONS = [s for s in range(2010, 2027) if s != 2019]

REQUIRED_COLS = [
    "season", "week", "season_type", "posteam", "defteam", "play_type", "play",
    "qb_dropback", "sack", "rush_attempt", "yards_gained", "yardline_100",
    "rusher_player_id", "receiver_player_id",
]


def load_one_season(con, folder: Path, season: int):
    path = folder / f"play_by_play_{season}.parquet"
    if not path.exists():
        print(f"[WARN] {path} not found; skipping season {season}")
        return None

    df = pd.read_parquet(path)

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        print(f"[WARN] season {season}: missing required column(s) {missing}; skipping season entirely "
              f"(this pbp release may have a different schema for this year)")
        return None

    df = df[df["season_type"] == "REG"].copy()
    if df.empty:
        print(f"[WARN] season {season}: 0 regular-season rows after filtering; skipping")
        return None

    df["posteam"] = df["posteam"].map(norm_team)
    df["defteam"] = df["defteam"].map(norm_team)

    # --- team-week: offense side (team = posteam) ---
    off = df.dropna(subset=["posteam"])
    off_teams_idx = off.groupby(["season", "week", "posteam"]).size().index

    def off_agg(mask, value_col=None):
        g = off[mask].groupby(["season", "week", "posteam"])
        s = g.size() if value_col is None else g[value_col].sum()
        return s.reindex(off_teams_idx, fill_value=0)

    dropbacks = off_agg(off["qb_dropback"] == 1)
    sacks_taken = off_agg(off["sack"] == 1)
    rush_mask = off["rush_attempt"] == 1
    rush_attempts = off_agg(rush_mask)
    rush_stuffs = off_agg(rush_mask & (off["yards_gained"] <= 0))
    pass_plays = off_agg(off["play_type"] == "pass")
    run_plays = off_agg(off["play_type"] == "run")
    scrimmage_mask = off["play_type"].isin(["pass", "run"])
    plays_inside_20 = off_agg(scrimmage_mask & (off["yardline_100"] <= 20))

    offense_df = pd.DataFrame({
        "dropbacks": dropbacks, "sacks_taken": sacks_taken,
        "rush_attempts": rush_attempts, "rush_stuffs": rush_stuffs,
        "pass_plays": pass_plays, "run_plays": run_plays,
        "plays_inside_20": plays_inside_20,
    })
    offense_df.index.names = ["season", "week", "team"]

    # --- team-week: defense side (team = defteam) ---
    dfn = df.dropna(subset=["defteam"])
    def_teams_idx = dfn.groupby(["season", "week", "defteam"]).size().index

    def def_agg(mask):
        g = dfn[mask].groupby(["season", "week", "defteam"])
        return g.size().reindex(def_teams_idx, fill_value=0)

    opp_dropbacks = def_agg(dfn["qb_dropback"] == 1)
    sacks_generated = def_agg(dfn["sack"] == 1)
    opp_rush_mask = dfn["rush_attempt"] == 1
    opp_rush_attempts = def_agg(opp_rush_mask)
    opp_rush_stuffs = def_agg(opp_rush_mask & (dfn["yards_gained"] <= 0))

    defense_df = pd.DataFrame({
        "opp_dropbacks": opp_dropbacks, "sacks_generated": sacks_generated,
        "opp_rush_attempts": opp_rush_attempts, "opp_rush_stuffs": opp_rush_stuffs,
    })
    defense_df.index.names = ["season", "week", "team"]

    team_week = offense_df.join(defense_df, how="outer").fillna(0)
    for c in team_week.columns:
        team_week[c] = team_week[c].astype(int)
    team_week["sack_rate_allowed"] = team_week["sacks_taken"] / team_week["dropbacks"].replace(0, pd.NA)
    team_week["sack_rate_generated"] = team_week["sacks_generated"] / team_week["opp_dropbacks"].replace(0, pd.NA)
    team_week["stuff_rate_allowed"] = team_week["rush_stuffs"] / team_week["rush_attempts"].replace(0, pd.NA)
    team_week["stuff_rate_generated"] = team_week["opp_rush_stuffs"] / team_week["opp_rush_attempts"].replace(0, pd.NA)
    denom = team_week["pass_plays"] + team_week["run_plays"]
    team_week["pass_rate"] = team_week["pass_plays"] / denom.replace(0, pd.NA)
    team_week = team_week.reset_index()

    # --- player-week: red-zone carries / targets ---
    rz = df[df["yardline_100"] <= 20]
    rz_rush = rz[rz["rush_attempt"] == 1].dropna(subset=["rusher_player_id"])
    redzone_carries = rz_rush.groupby(["season", "week", "rusher_player_id"]).size()
    redzone_carries.index.names = ["season", "week", "player_id"]

    rz_pass = rz[rz["play_type"] == "pass"].dropna(subset=["receiver_player_id"])
    redzone_targets = rz_pass.groupby(["season", "week", "receiver_player_id"]).size()
    redzone_targets.index.names = ["season", "week", "player_id"]

    player_week = pd.DataFrame({
        "redzone_carries": redzone_carries, "redzone_targets": redzone_targets,
    }).fillna(0).astype(int).reset_index()

    return team_week, player_week


TEAM_WEEK_COLS = [
    "dropbacks", "sacks_taken", "sack_rate_allowed",
    "opp_dropbacks", "sacks_generated", "sack_rate_generated",
    "rush_attempts", "rush_stuffs", "stuff_rate_allowed",
    "opp_rush_attempts", "opp_rush_stuffs", "stuff_rate_generated",
    "pass_plays", "run_plays", "pass_rate", "plays_inside_20",
]


def write_team_week(con, df):
    cols = ["season", "week", "team"] + TEAM_WEEK_COLS
    rows = [tuple(None if pd.isna(v) else v for v in row) for row in df[cols].itertuples(index=False, name=None)]
    placeholders = ",".join(["?"] * len(cols))
    con.executemany(
        f"INSERT OR REPLACE INTO team_week_pbp_stats ({','.join(cols)}) VALUES ({placeholders})", rows
    )
    return len(rows)


def write_player_week(con, df):
    cols = ["season", "week", "player_id", "redzone_carries", "redzone_targets"]
    rows = list(df[cols].itertuples(index=False, name=None))
    placeholders = ",".join(["?"] * len(cols))
    con.executemany(
        f"INSERT OR REPLACE INTO player_week_pbp_stats ({','.join(cols)}) VALUES ({placeholders})", rows
    )
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--folder", default=str(REPO_ROOT / "nflverse_raw"))
    ap.add_argument("--seasons", nargs="+", type=int, default=DEFAULT_SEASONS)
    args = ap.parse_args()

    folder = Path(args.folder)
    con = sqlite3.connect(args.db)

    rate_cols = ["sack_rate_allowed", "sack_rate_generated", "stuff_rate_allowed",
                 "stuff_rate_generated", "pass_rate"]
    print(f"{'season':6} {'team_wk':>8} {'player_wk':>10}  " + "  ".join(f"{c} NULL%" for c in rate_cols))
    try:
        for season in args.seasons:
            result = load_one_season(con, folder, season)
            if result is None:
                continue
            team_week, player_week = result
            n_tw = write_team_week(con, team_week)
            n_pw = write_player_week(con, player_week)
            con.commit()

            null_pcts = [
                f"{100 * team_week[c].isna().mean():6.2f}" for c in rate_cols
            ]
            print(f"{season:6} {n_tw:8} {n_pw:10}  " + "  ".join(null_pcts))
    finally:
        con.close()


if __name__ == "__main__":
    main()
