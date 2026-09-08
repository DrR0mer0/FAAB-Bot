#!/usr/bin/env python3
"""Score every eligible player for a target (season, week) using the saved
O.D.D.S. model, and print the top N by predicted spike probability.

Works for a not-yet-played week: eligibility and features are computed via
features_lib.FeatureEngine using only data strictly before the target week,
the same function used to build the historical training table.

If a current-season roster file is available (nflverse_raw/roster_<season>.csv),
candidates are cross-referenced against it. A confirmed offseason team change
(vs. the team inferred from the player's last game) gets its schedule-
dependent features (opponent matchup, home/away, short week, presumed-starter
check) recomputed for the corrected team -- but the model score is suppressed
entirely, since usage features (touches, touch share) still reflect the old
team and there's no real data yet for the new one. Those players are reported
separately as a watch list, not folded into the ranking.
"""
import argparse
import csv
import os
import sqlite3

import joblib
import numpy as np

from features_lib import FEATURE_COLS, FeatureEngine

# Only these statuses represent a confirmed, current team assignment. CUT/RET/
# EXE players aren't actually on the team a roster file last associated them
# with, so they're excluded rather than asserting a bogus team.
CONFIRMED_ROSTER_STATUSES = {"ACT", "DEV", "RES"}
TEAM_CHANGE_REASON = "team changed this offseason — insufficient data on new team"


def get_eligible_candidates(engine, season, week):
    """The season-gap reset in features_lib only catches *structural* holes in
    the loaded-season sequence (e.g. 2019, or 2025 when scoring into 2026);
    it says nothing about an individual player who simply stopped playing.
    2024->2025 has no such hole, so a long-retired veteran (Matt Ryan, Julio
    Jones, ...) whose last real game was years ago would otherwise still
    pass eligibility on stale data. Require a recent-enough appearance to be
    a plausible current-roster candidate."""
    recency_cutoff = season - 1
    candidates = []
    for pid, entries in engine.player_history.items():
        if entries[-1][0] < recency_cutoff:
            continue
        feat = engine.compute_features(season, week, pid)
        if feat is None:
            continue
        candidates.append((pid, feat))
    return candidates


def load_roster_team_map(path):
    team_of = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pid = r.get("gsis_id")
            if pid and r.get("status") in CONFIRMED_ROSTER_STATUSES:
                team_of[pid] = r["team"]
    return team_of


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="faab_history_core_v0_1.db")
    ap.add_argument("--model", default="odds_xgb_model_production.joblib")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--roster", default=None, help="defaults to nflverse_raw/roster_<season>.csv if present")
    args = ap.parse_args()

    roster_path = args.roster or f"nflverse_raw/roster_{args.season}.csv"
    roster_team_of = {}
    if os.path.exists(roster_path):
        roster_team_of = load_roster_team_map(roster_path)
        print(f"[INFO] cross-referencing against {roster_path}: {len(roster_team_of)} players with a confirmed current team")
    else:
        print(f"[INFO] no roster file at {roster_path} -- skipping offseason team-change cross-check")

    bundle = joblib.load(args.model)
    model = bundle["model"]
    feature_cols = bundle["feature_cols"]
    # The saved model may use any subset of what compute_features() returns
    # (e.g. the production model can be the 9-feature baseline while
    # features_lib computes 10) -- just check it's a real subset, not an
    # exact match against whatever features_lib currently computes.
    unknown = set(feature_cols) - set(FEATURE_COLS)
    assert not unknown, f"saved model uses unknown feature columns: {unknown}"
    print(f"[INFO] model uses {len(feature_cols)} feature(s): {feature_cols}")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    engine = FeatureEngine(con)

    names = {row["player_id"]: row["full_name"] for row in con.execute("SELECT player_id, full_name FROM ref_players")}

    already_played = bool(engine.played_this_week.get((args.season, args.week)))
    print(f"[INFO] {args.season} week {args.week}: "
          f"{'ALREADY HAS stat data (historical/backtest mode)' if already_played else 'no stat data yet (genuine future-week mode)'}")

    candidates = get_eligible_candidates(engine, args.season, args.week)
    print(f"[INFO] {len(candidates)} eligible players for {args.season} week {args.week}")

    if not candidates:
        print("[DONE] no eligible players -- nothing to score.")
        return

    stable, watch_list = [], []
    for pid, feat in candidates:
        inferred_team = feat["_team"]
        roster_team = roster_team_of.get(pid)
        if roster_team is not None and roster_team != inferred_team:
            corrected = engine.compute_schedule_dependent_features(args.season, args.week, roster_team, feat["_pos"], pid)
            watch_list.append((pid, feat, inferred_team, roster_team, corrected))
        else:
            stable.append((pid, feat))

    print(f"[INFO] {len(stable)} stable-team candidates scored; {len(watch_list)} team-changed candidates moved to watch list (no score)")

    X = np.array([[c[1][col] if c[1][col] is not None else np.nan for col in feature_cols] for c in stable])
    proba = model.predict_proba(X)[:, 1]
    ranked = sorted(zip(stable, proba), key=lambda x: x[1], reverse=True)[: args.top]

    print(f"\nTop {len(ranked)} by predicted spike probability, {args.season} week {args.week} (stable-team candidates only):")
    header = f"{'Name':24} {'Pos':4} {'Team':5} {'Score':>7}"
    print(header)
    print("-" * len(header))
    for (pid, feat), score in ranked:
        display = names.get(pid, pid)
        print(f"{display:24.24} {feat['_pos']:4} {feat['_team']:5} {score:7.4f}")

    if watch_list:
        print(f"\nWatch list -- {len(watch_list)} players with a confirmed offseason team change, score suppressed ({TEAM_CHANGE_REASON}):")
        header2 = f"{'Name':24} {'Pos':4} {'Old':4} {'New':4} {'Opp':4} {'Home':5} {'ShortWk':7} {'StarterAbsent':13} {'Matchup':>8}"
        print(header2)
        print("-" * len(header2))
        for pid, feat, old_team, new_team, corrected in sorted(watch_list, key=lambda w: (w[3], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            opp = corrected["_opp"] or "?"
            home = corrected["is_home"]
            sw = corrected["is_short_week"]
            sap = corrected["starter_absent_proxy"]
            matchup = corrected["opponent_position_matchup"]
            matchup_s = f"{matchup:.2f}" if matchup is not None else "None"
            print(f"{display:24.24} {feat['_pos']:4} {old_team:4} {new_team:4} {opp:4} {str(home):5} {str(sw):7} {str(sap):13} {matchup_s:>8}")

    con.close()


if __name__ == "__main__":
    main()
