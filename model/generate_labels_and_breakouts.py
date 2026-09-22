#!/usr/bin/env python3
"""Populate labels_player_week from player_week_stats across one or more seasons.

Scoring is entirely data-driven: multipliers live in scoring_profiles as a
JSON rule set, so adding a new profile later requires no code changes here.

Trailing baselines carry over season boundaries (a player's "prior 3 games"
can span two seasons), EXCEPT across a gap between loaded seasons -- if the
season immediately before the current one wasn't loaded (e.g. 2019 is
missing from a 2010-2018 + 2020-2024 run), history resets at that boundary,
same as if the player were a rookie again.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "data"))
from team_crosswalk import TEAM_ALIASES, norm_team  # noqa: E402 -- shared crosswalk; see data/team_crosswalk.py

# spike_flag thresholds
SPIKE_MULTIPLIER = 1.5
SPIKE_MIN_POINTS = 10.0
MIN_PRIOR_GAMES = 3

HALF12_PROFILE_ID = "half12"
HALF12_RULES = {
    "pass_yd": 0.04,
    "pass_td": 4,
    "pass_int": -2,
    "rush_yd": 0.1,
    "rush_td": 6,
    "rec_yd": 0.1,
    "reception": 0.5,
    "rec_td": 6,
    "fumble_lost": -2,
}

# Structural mapping from a scoring-rule key to its player_week_stats column.
# Not a scoring parameter itself -- the multipliers all come from the profile.
RULE_TO_COLUMN = {
    "pass_yd": "pass_yds",
    "pass_td": "pass_td",
    "pass_int": "pass_int",
    "rush_yd": "rush_yds",
    "rush_td": "rush_td",
    "rec_yd": "rec_yds",
    "reception": "rec_rec",
    "rec_td": "rec_td",
    "fumble_lost": "fumbles",
}


def ensure_scoring_profile(con, profile_id, rules):
    con.execute(
        "INSERT OR REPLACE INTO scoring_profiles (profile_id, scoring_json) VALUES (?, ?)",
        (profile_id, json.dumps(rules)),
    )
    con.commit()


def load_rules(con, profile_id):
    row = con.execute(
        "SELECT scoring_json FROM scoring_profiles WHERE profile_id = ?", (profile_id,)
    ).fetchone()
    if not row:
        raise SystemExit(f"[ERROR] scoring profile '{profile_id}' not found")
    return json.loads(row[0])


def compute_points(rules, row):
    points = 0.0
    for key, mult in rules.items():
        col = RULE_TO_COLUMN.get(key)
        if col is None:
            continue
        val = row[col]
        if val is not None:
            points += mult * val
    return points


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--seasons", nargs="+", type=int, default=[2022])
    ap.add_argument("--profile", default=HALF12_PROFILE_ID)
    args = ap.parse_args()
    seasons = sorted(args.seasons)

    # A "gap" is a hole in the loaded-season sequence itself (e.g. 2019 missing
    # between 2018 and 2020) -- NOT a player's personal absence from a loaded
    # season (e.g. an injury year). gap_after holds the season just before each
    # such hole; a player's history resets only when their previous game and
    # current game fall on opposite sides of one of these structural gaps.
    gap_after = {seasons[i - 1] for i in range(1, len(seasons)) if seasons[i] - seasons[i - 1] > 1}

    def crosses_gap(prev_season_val, curr_season_val):
        return any(prev_season_val <= g < curr_season_val for g in gap_after)

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    ensure_scoring_profile(con, HALF12_PROFILE_ID, HALF12_RULES)
    rules = load_rules(con, args.profile)

    # Regular-season-only: (season, week, team) triples that were playoff
    # games get excluded entirely -- not counted as prior games, no labels written.
    placeholders = ",".join("?" * len(seasons))
    playoff_team_weeks = set()
    for season, week, home, away in con.execute(
        f"SELECT season, week, home_team, away_team FROM nfl_games WHERE season IN ({placeholders}) AND is_playoffs = 1",
        seasons,
    ):
        playoff_team_weeks.add((season, week, norm_team(home)))
        playoff_team_weeks.add((season, week, norm_team(away)))

    all_rows = con.execute(
        f"""SELECT season, week, player_id, team, pos, pass_yds, pass_td, pass_int,
                  rush_yds, rush_td, rec_yds, rec_rec, rec_td, fumbles
           FROM player_week_stats WHERE season IN ({placeholders}) ORDER BY player_id, season, week""",
        seasons,
    ).fetchall()
    rows = [r for r in all_rows if (r["season"], r["week"], r["team"]) not in playoff_team_weeks]

    # Clean slate for these seasons: previous runs may have written playoff-week
    # labels, or labels under a different cross-season history, that no longer belong.
    con.execute(f"DELETE FROM labels_player_week WHERE season IN ({placeholders})", seasons)

    # points[(season,week,player_id)] = fantasy_points_half
    points = {}
    pos_of = {}
    for r in rows:
        key = (r["season"], r["week"], r["player_id"])
        points[key] = compute_points(rules, r)
        pos_of[key] = r["pos"]

    # Rolling trailing-baseline pass, in chronological (season, week) order per
    # player, over games actually played -- byes leave no row so they're
    # naturally skipped. History resets whenever the gap between this game's
    # season and the player's previous game's season is more than one year,
    # i.e. the season right before this one wasn't loaded.
    history = {}       # player_id -> list of prior fantasy_points_half, in order played
    prev_season = {}   # player_id -> season of their last processed game
    spike_flag = {}    # key -> 0/1, absent if ineligible (stays NULL)
    baseline = {}       # key -> trailing baseline, only set when eligible
    n_eligible = 0
    n_ineligible = 0
    per_season_stats = {s: {"eligible": 0, "ineligible": 0, "flagged": 0} for s in seasons}

    # Shadow pass (report-only, not written to DB): what eligibility would be
    # WITHOUT the season-gap reset, to measure the reset rule's actual impact.
    shadow_history = {}
    shadow_prev_season = {}
    newly_ineligible = []  # keys that are eligible in the shadow pass but not the real pass

    for r in rows:
        key = (r["season"], r["week"], r["player_id"])
        pid = r["player_id"]
        season = r["season"]
        pts = points[key]

        prior = history.setdefault(pid, [])
        if pid in prev_season and crosses_gap(prev_season[pid], season):
            prior.clear()
        prev_season[pid] = season

        shadow_prior = shadow_history.setdefault(pid, [])
        shadow_was_eligible = len(shadow_prior) >= MIN_PRIOR_GAMES

        if len(prior) >= MIN_PRIOR_GAMES:
            n_eligible += 1
            per_season_stats[season]["eligible"] += 1
            base = sum(prior[-MIN_PRIOR_GAMES:]) / MIN_PRIOR_GAMES
            baseline[key] = base
            flag = 1 if (pts >= SPIKE_MULTIPLIER * base and pts >= SPIKE_MIN_POINTS) else 0
            spike_flag[key] = flag
            if flag == 1:
                per_season_stats[season]["flagged"] += 1
        else:
            n_ineligible += 1
            per_season_stats[season]["ineligible"] += 1
            if shadow_was_eligible:
                newly_ineligible.append(key)

        prior.append(pts)
        shadow_prior.append(pts)

    # positional_rank_week: standard competition ranking (ties share a rank)
    # within (season, week, pos).
    by_week_pos = {}
    for key, pos in pos_of.items():
        season, week, player_id = key
        by_week_pos.setdefault((season, week, pos), []).append(key)

    rank_of = {}
    for group_key, keys in by_week_pos.items():
        keys.sort(key=lambda k: points[k], reverse=True)
        rank = 0
        prev_pts = None
        for i, k in enumerate(keys, start=1):
            if points[k] != prev_pts:
                rank = i
                prev_pts = points[k]
            rank_of[k] = rank

    label_rows = []
    for r in rows:
        key = (r["season"], r["week"], r["player_id"])
        label_rows.append((
            key[0], key[1], key[2],
            points[key],
            rank_of[key],
            None,  # beat_proj_flag: out of scope until real historical projections exist
            spike_flag.get(key),
        ))

    con.executemany(
        """INSERT OR REPLACE INTO labels_player_week
           (season, week, player_id, fantasy_points_half, positional_rank_week, beat_proj_flag, spike_flag)
           VALUES (?,?,?,?,?,?,?)""",
        label_rows,
    )
    con.commit()

    names = {row["player_id"]: row["full_name"] for row in con.execute("SELECT player_id, full_name FROM ref_players")}

    print(f"[DONE] seasons {seasons}: {len(label_rows)} labels_player_week rows written using profile '{args.profile}'")

    print("\nplayer_week_stats and nfl_games rows per season:")
    for s in seasons:
        pws = con.execute("SELECT COUNT(*) FROM player_week_stats WHERE season=?", (s,)).fetchone()[0]
        games = con.execute("SELECT COUNT(*) FROM nfl_games WHERE season=?", (s,)).fetchone()[0]
        print(f"  {s}: player_week_stats={pws}, nfl_games={games}")

    print(f"\nTotal labels_player_week rows: {len(label_rows)}")
    print(f"Overall eligible (>= {MIN_PRIOR_GAMES} prior games): {n_eligible}")
    print(f"Overall ineligible (spike_flag NULL): {n_ineligible}")
    n_flagged = sum(1 for v in spike_flag.values() if v == 1)
    print(f"Overall spike-flagged: {n_flagged}")

    print("\nPer-season eligible / ineligible / flagged:")
    for s in seasons:
        st = per_season_stats[s]
        print(f"  {s}: eligible={st['eligible']}, ineligible={st['ineligible']}, flagged={st['flagged']}")

    print(f"\nPlayer-weeks newly made ineligible by the season-gap reset rule: {len(newly_ineligible)}")
    if newly_ineligible:
        newly_ineligible.sort()
        for key in newly_ineligible[:20]:
            season, week, player_id = key
            display = names.get(player_id, player_id)
            print(f"  {display} ({pos_of[key]}) season={season} week={week}")
        if len(newly_ineligible) > 20:
            print(f"  ... and {len(newly_ineligible) - 20} more")

    flagged_keys = [k for k, v in spike_flag.items() if v == 1]
    flagged_keys.sort(key=lambda k: points[k], reverse=True)

    print(f"\nTop {min(20, len(flagged_keys))} spike-flagged player-weeks across all seasons:")
    header = f"{'Name':24} {'Pos':4} {'Season':6} {'Wk':3} {'Points':>7} {'Baseline':>9}"
    print(header)
    print("-" * len(header))
    for key in flagged_keys[:20]:
        season, week, player_id = key
        display = names.get(player_id, player_id)
        print(f"{display:24.24} {pos_of[key]:4} {season:6d} {week:3d} {points[key]:7.2f} {baseline[key]:9.2f}")

    con.close()


if __name__ == "__main__":
    main()
