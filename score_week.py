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
NEW_COMPETITOR_REASON = "new same-position addition(s) this offseason — trailing touch share predates the competition"

# Fantasy-relevant offensive positions only; IDP/O-line/K/P aren't rosterable
# in this league.
ROSTERABLE_POSITIONS = {"QB", "RB", "WR", "TE"}

# "Meaningful threat" bar for a new same-position arrival: either they
# produced at a real starter/committee-back level in 2025, or they carry
# first-few-round draft capital in the 2026 class (a Day 1-2 rookie is often
# a bigger threat to an incumbent's role than a marginal veteran). A rookie
# or futures signing with neither trips nothing.
PROD_THRESHOLDS = {"RB": 50, "WR": 30, "TE": 30, "QB": 100}  # touches/targets/targets/attempts
DRAFT_CAPITAL_PICK_CUTOFF = 96  # ~3 rounds at 32 picks/round


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


def load_roster_records(path, season):
    """Confirmed-status roster rows, keyed by gsis_id, plus a
    (team, position) -> [gsis_id, ...] grouping used to find same-position
    additions. Also returns draft_pick_of: gsis_id -> overall pick number,
    for rookies of this draft class only (rookie_year == season)."""
    team_of = {}
    team_pos_roster = {}
    draft_pick_of = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pid = r.get("gsis_id")
            if not pid or r.get("status") not in CONFIRMED_ROSTER_STATUSES:
                continue
            team_of[pid] = r["team"]
            team_pos_roster.setdefault((r["team"], r["position"]), []).append(pid)
            if r.get("rookie_year") == str(season) and r.get("draft_number"):
                draft_pick_of[pid] = int(r["draft_number"])
    return team_of, team_pos_roster, draft_pick_of


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
    roster_team_of, team_pos_roster, draft_pick_of = {}, {}, {}
    if os.path.exists(roster_path):
        roster_team_of, team_pos_roster, draft_pick_of = load_roster_records(roster_path, args.season)
        print(f"[INFO] cross-referencing against {roster_path}: {len(roster_team_of)} players with a confirmed current team, "
              f"{len(draft_pick_of)} rookies with known {args.season}-draft pick numbers")
    else:
        print(f"[INFO] no roster file at {roster_path} -- skipping offseason team-change / new-competitor cross-checks")

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
    all_eligible_count = len(candidates)
    candidates = [(pid, feat) for pid, feat in candidates if feat["_pos"] in ROSTERABLE_POSITIONS]
    print(f"[INFO] {all_eligible_count} eligible players for {args.season} week {args.week}; "
          f"{len(candidates)} after restricting to {sorted(ROSTERABLE_POSITIONS)}")

    if not candidates:
        print("[DONE] no eligible players -- nothing to score.")
        return

    # A player is "on team T's 2025 roster" if they have a real 2025 game for
    # T -- used to detect a same-position teammate who is genuinely new to
    # the team this offseason (free agent signing, trade, or rookie), not
    # just someone who happened to change teams themselves.
    prior_season = args.season - 1
    players_on_team_2025 = {}
    rb_touches_2025, wr_te_targets_2025 = {}, {}
    for pid, entries in engine.player_history.items():
        for (s, _w, tm, pos, touches) in entries:
            if s != prior_season:
                continue
            players_on_team_2025.setdefault(tm, set()).add(pid)
            if pos == "RB":
                rb_touches_2025[pid] = rb_touches_2025.get(pid, 0) + touches

    # rec_tgt (targets) isn't part of the "touches" tuple in player_history,
    # so pull it straight from player_week_stats for WR/TE.
    for pid, tgt in con.execute(
        "SELECT player_id, SUM(rec_tgt) FROM player_week_stats WHERE season=? AND pos IN ('WR','TE') GROUP BY player_id",
        (prior_season,),
    ):
        wr_te_targets_2025[pid] = tgt or 0

    # pass attempts aren't in player_week_stats at all (never needed by the
    # half12 scoring profile) -- read them from the raw fetched prior-season
    # CSV instead of extending the core schema for one threshold check.
    qb_attempts_2025 = {}
    raw_2025_path = f"nflverse_raw/stats_player_week_{prior_season}.csv"
    if os.path.exists(raw_2025_path):
        with open(raw_2025_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("attempts"):
                    pid2 = r["player_id"]
                    qb_attempts_2025[pid2] = qb_attempts_2025.get(pid2, 0) + int(r["attempts"])

    def meaningful_threat(pid2, pos2):
        """Returns (is_threat, via_production, via_draft)."""
        prod_bar = PROD_THRESHOLDS.get(pos2)
        if pos2 == "RB":
            prod_val = rb_touches_2025.get(pid2, 0)
        elif pos2 in ("WR", "TE"):
            prod_val = wr_te_targets_2025.get(pid2, 0)
        elif pos2 == "QB":
            prod_val = qb_attempts_2025.get(pid2, 0)
        else:
            prod_val = 0
        via_production = prod_bar is not None and prod_val >= prod_bar
        pick = draft_pick_of.get(pid2)
        via_draft = pick is not None and pick <= DRAFT_CAPITAL_PICK_CUTOFF
        return (via_production or via_draft), via_production, via_draft

    stable, team_changed_list, new_competitor_list = [], [], []
    n_via_production_only = n_via_draft_only = n_via_both = 0
    for pid, feat in candidates:
        inferred_team = feat["_team"]
        roster_team = roster_team_of.get(pid)

        if roster_team is not None and roster_team != inferred_team:
            corrected = engine.compute_schedule_dependent_features(args.season, args.week, roster_team, feat["_pos"], pid)
            team_changed_list.append((pid, feat, inferred_team, roster_team, corrected))
            continue

        team = roster_team or inferred_team
        pos = feat["_pos"]
        new_arrivals = [
            p2 for p2 in team_pos_roster.get((team, pos), [])
            if p2 != pid and p2 not in players_on_team_2025.get(team, set())
        ]

        meaningful, any_prod, any_draft = [], False, False
        for p2 in new_arrivals:
            is_threat, via_prod, via_draft = meaningful_threat(p2, pos)
            if is_threat:
                meaningful.append(p2)
                any_prod = any_prod or via_prod
                any_draft = any_draft or via_draft

        if meaningful:
            new_competitor_list.append((pid, feat, team, meaningful))
            if any_prod and any_draft:
                n_via_both += 1
            elif any_prod:
                n_via_production_only += 1
            else:
                n_via_draft_only += 1
        else:
            stable.append((pid, feat))

    print(f"[INFO] {len(stable)} stable candidates scored; "
          f"{len(team_changed_list)} team-changed (score suppressed); "
          f"{len(new_competitor_list)} with a meaningful new same-position competitor this offseason (score suppressed)")
    print(f"[INFO]   of those {len(new_competitor_list)}: {n_via_production_only} via prior-production threshold only, "
          f"{n_via_draft_only} via draft-capital only, {n_via_both} via both")

    X = np.array([[c[1][col] if c[1][col] is not None else np.nan for col in feature_cols] for c in stable])
    proba = model.predict_proba(X)[:, 1]
    ranked = sorted(zip(stable, proba), key=lambda x: x[1], reverse=True)[: args.top]

    print(f"\nTop {len(ranked)} by predicted spike probability, {args.season} week {args.week} (stable candidates only, both flags clear):")
    header = f"{'Name':24} {'Pos':4} {'Team':5} {'Score':>7}  team_changed  new_competitor"
    print(header)
    print("-" * len(header))
    for (pid, feat), score in ranked:
        display = names.get(pid, pid)
        print(f"{display:24.24} {feat['_pos']:4} {feat['_team']:5} {score:7.4f}  {0:^12}  {0:^14}")

    if team_changed_list:
        print(f"\nWatch list -- {len(team_changed_list)} players with a confirmed offseason team change, score suppressed ({TEAM_CHANGE_REASON}):")
        header2 = f"{'Name':24} {'Pos':4} {'Old':4} {'New':4} {'Opp':4} {'Home':5} {'ShortWk':7} {'StarterAbsent':13} {'Matchup':>8}"
        print(header2)
        print("-" * len(header2))
        for pid, feat, old_team, new_team, corrected in sorted(team_changed_list, key=lambda w: (w[3], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            opp = corrected["_opp"] or "?"
            home = corrected["is_home"]
            sw = corrected["is_short_week"]
            sap = corrected["starter_absent_proxy"]
            matchup = corrected["opponent_position_matchup"]
            matchup_s = f"{matchup:.2f}" if matchup is not None else "None"
            print(f"{display:24.24} {feat['_pos']:4} {old_team:4} {new_team:4} {opp:4} {str(home):5} {str(sw):7} {str(sap):13} {matchup_s:>8}")

    if new_competitor_list:
        print(f"\nWatch list -- {len(new_competitor_list)} players with a meaningful new same-position competitor this offseason, score suppressed ({NEW_COMPETITOR_REASON}):")
        header3 = f"{'Name':24} {'Pos':4} {'Team':5} {'New competitor(s) [why]'}"
        print(header3)
        print("-" * len(header3))
        for pid, feat, team, competitors in sorted(new_competitor_list, key=lambda w: (w[2], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            comp_strs = []
            for c in competitors:
                _, via_prod, via_draft = meaningful_threat(c, feat["_pos"])
                why = "prod+draft" if (via_prod and via_draft) else ("draft" if via_draft else "prod")
                comp_strs.append(f"{names.get(c, c)} [{why}]")
            print(f"{display:24.24} {feat['_pos']:4} {team:5} {', '.join(comp_strs)}")

    con.close()


if __name__ == "__main__":
    main()
