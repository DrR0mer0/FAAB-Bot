#!/usr/bin/env python3
"""Load fetched nflverse CSVs (nflverse_raw/) into the FAAB history SQLite DB.

Scope: player_week_stats from stats_player_week_<season>.csv; nfl_games and
team_week_stats' betting lines (spread/total/implied_total) from games.csv
(one file covering every season). Injuries, depth charts, projections, and
non-betting team_week_stats columns (pace, DVOA, weather) are not touched
here.
"""
import argparse
import csv
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


PLAYER_STAT_COLUMNS = [
    "season", "week", "player_id", "team", "opp", "pos",
    "pass_yds", "pass_td", "pass_int",
    "rush_att", "rush_yds", "rush_td",
    "rec_tgt", "rec_rec", "rec_yds", "rec_td",
    "fumbles",
    "fg_made", "fg_att", "xp_made", "xp_att",
    "def_sacks", "def_int", "def_td", "def_pa", "def_ya",
    "snaps_off", "snaps_def", "snaps_st",
    "attempts", "completions",
    "passing_air_yards", "receiving_air_yards",
    "target_share", "air_yards_share",
    "racr", "pacr", "wopr",
    "passing_epa", "rushing_epa", "receiving_epa",
    "passing_cpoe",
]


def load_player_week_stats(con, folder: Path, season: int):
    path = folder / f"stats_player_week_{season}.csv"
    if not path.exists():
        print(f"[WARN] {path} not found; skipping player_week_stats for {season}")
        return 0

    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # sacks_suffered/fumbles lost across sack, rush and receiving plays
            # is the closest single "fumbles" figure this file supports.
            fumbles_lost = (
                (to_float(r.get("sack_fumbles_lost")) or 0)
                + (to_float(r.get("rushing_fumbles_lost")) or 0)
                + (to_float(r.get("receiving_fumbles_lost")) or 0)
            )
            rows.append((
                to_int(r["season"]), to_int(r["week"]), r["player_id"],
                r.get("team"), r.get("opponent_team"), r.get("position"),
                to_float(r.get("passing_yards")), to_float(r.get("passing_tds")), to_float(r.get("passing_interceptions")),
                to_float(r.get("carries")), to_float(r.get("rushing_yards")), to_float(r.get("rushing_tds")),
                to_float(r.get("targets")), to_float(r.get("receptions")), to_float(r.get("receiving_yards")), to_float(r.get("receiving_tds")),
                fumbles_lost,
                to_float(r.get("fg_made")), to_float(r.get("fg_att")), to_float(r.get("pat_made")), to_float(r.get("pat_att")),
                to_float(r.get("def_sacks")), to_float(r.get("def_interceptions")), to_float(r.get("def_tds")), None, None,
                None, None, None,
                to_float(r.get("attempts")), to_float(r.get("completions")),
                to_float(r.get("passing_air_yards")), to_float(r.get("receiving_air_yards")),
                to_float(r.get("target_share")), to_float(r.get("air_yards_share")),
                to_float(r.get("racr")), to_float(r.get("pacr")), to_float(r.get("wopr")),
                to_float(r.get("passing_epa")), to_float(r.get("rushing_epa")), to_float(r.get("receiving_epa")),
                to_float(r.get("passing_cpoe")),
            ))

    placeholders = ",".join(["?"] * len(PLAYER_STAT_COLUMNS))
    cols = ",".join(PLAYER_STAT_COLUMNS)
    con.executemany(
        f"INSERT OR REPLACE INTO player_week_stats ({cols}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


LONDON_STADIUMS = {"tottenham stadium", "wembley stadium"}


def load_nfl_games(con, folder: Path, season: int):
    path = folder / "games.csv"
    if not path.exists():
        print(f"[WARN] {path} not found; skipping nfl_games for {season}")
        return 0

    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if to_int(r["season"]) != season:
                continue
            gameday = r.get("gameday") or ""
            gametime = r.get("gametime") or "00:00"
            # nflverse doesn't publish a timezone offset; this is local
            # kickoff time in ISO shape, not a true UTC instant.
            kickoff = f"{gameday}T{gametime}:00"
            rows.append((
                to_int(r["season"]), to_int(r["week"]), r["game_id"], kickoff,
                r.get("home_team"), r.get("away_team"),
                to_int(r.get("home_score")), to_int(r.get("away_score")),
                1 if r.get("weekday") == "Thursday" else 0,
                1 if (r.get("stadium") or "").strip().lower() in LONDON_STADIUMS else 0,
                0 if r.get("game_type") == "REG" else 1,
            ))

    con.executemany(
        """INSERT OR REPLACE INTO nfl_games
           (season, week, game_id, kickoff_utc, home_team, away_team,
            home_score, away_score, is_thursday, is_london, is_playoffs)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


def load_team_week_stats(con, folder: Path, season: int):
    """Populate team_week_stats' spread/total/implied_total from games.csv.

    spread_line is the home team's closing spread: positive means the home
    team was favored. Per-team spread is signed from that team's own
    perspective (negative = favored), matching common sportsbook convention.
    """
    path = folder / "games.csv"
    if not path.exists():
        print(f"[WARN] {path} not found; skipping team_week_stats for {season}")
        return 0

    rows = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if to_int(r["season"]) != season:
                continue
            season_i, week_i = to_int(r["season"]), to_int(r["week"])
            spread_line = to_float(r.get("spread_line"))
            total_line = to_float(r.get("total_line"))
            home_spread = -spread_line if spread_line is not None else None
            away_spread = spread_line
            home_implied = (
                (total_line + spread_line) / 2
                if total_line is not None and spread_line is not None
                else None
            )
            away_implied = (
                (total_line - spread_line) / 2
                if total_line is not None and spread_line is not None
                else None
            )
            rows.append((season_i, week_i, r.get("home_team"), home_spread, total_line, home_implied))
            rows.append((season_i, week_i, r.get("away_team"), away_spread, total_line, away_implied))

    con.executemany(
        """INSERT INTO team_week_stats (season, week, team, spread, total, implied_total)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(season, week, team) DO UPDATE SET
             spread=excluded.spread, total=excluded.total, implied_total=excluded.implied_total""",
        rows,
    )
    return len(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--folder", default=str(REPO_ROOT / "nflverse_raw"))
    ap.add_argument("--seasons", nargs="+", type=int, default=[2022])
    args = ap.parse_args()

    folder = Path(args.folder)
    con = sqlite3.connect(args.db)
    try:
        for season in args.seasons:
            n_stats = load_player_week_stats(con, folder, season)
            n_games = load_nfl_games(con, folder, season)
            n_team_week = load_team_week_stats(con, folder, season)
            con.commit()
            print(f"[DONE] season {season}: {n_stats} player_week_stats rows, {n_games} nfl_games rows, {n_team_week} team_week_stats rows")
    finally:
        con.close()


if __name__ == "__main__":
    main()
