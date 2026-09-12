#!/usr/bin/env python3
"""Cross-reference the full 2026-week-1 eligible candidate pool against
nflverse's current 2026 roster release, to catch offseason team changes that
predate any 2026 game data (score_week.py can only infer team from a
player's last 2025 game, which misses trades/signings/releases since).

For any candidate whose roster-reported team differs from the inferred one:
correct the displayed team AND set team_changed_this_offseason=1 -- never
silently overwrite the label.
"""
import argparse
import csv
import sqlite3
import sys
from pathlib import Path

import joblib
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "model"))
from features_lib import FeatureEngine
from score_week import get_eligible_candidates

# Only these statuses represent a confirmed, current team assignment. CUT/RET/
# EXE players aren't actually on the team the roster file last associated them
# with, so they're excluded from the mapping rather than asserting a bogus team.
CONFIRMED_STATUSES = {"ACT", "DEV", "RES"}


def load_roster_team_map(path):
    team_of = {}
    skipped_status = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pid = r.get("gsis_id")
            if not pid:
                continue
            status = r.get("status")
            if status not in CONFIRMED_STATUSES:
                skipped_status[status] = skipped_status.get(status, 0) + 1
                continue
            team_of[pid] = r["team"]
    return team_of, skipped_status


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--model", default=str(REPO_ROOT / "odds_xgb_model_production.joblib"))
    ap.add_argument("--roster", default=str(REPO_ROOT / "nflverse_raw" / "roster_2026.csv"))
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()

    roster_team_of, skipped_status = load_roster_team_map(args.roster)
    print(f"[INFO] roster_2026.csv: {len(roster_team_of)} players with a confirmed current team "
          f"(statuses {sorted(CONFIRMED_STATUSES)}); excluded by status: {skipped_status}")

    bundle = joblib.load(args.model)
    model, feature_cols = bundle["model"], bundle["feature_cols"]

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    engine = FeatureEngine(con)
    names = {row["player_id"]: row["full_name"] for row in con.execute("SELECT player_id, full_name FROM ref_players")}
    con.close()

    candidates = get_eligible_candidates(engine, args.season, args.week)
    print(f"[INFO] {len(candidates)} eligible players for {args.season} week {args.week}")

    X = np.array([[c[1][col] if c[1][col] is not None else np.nan for col in feature_cols] for c in candidates])
    proba = model.predict_proba(X)[:, 1]

    n_changed = 0
    n_no_roster_match = 0
    enriched = []
    for (pid, feat), score in zip(candidates, proba):
        inferred_team = feat["_team"]
        roster_team = roster_team_of.get(pid)
        if roster_team is None:
            n_no_roster_match += 1
            display_team, changed = inferred_team, False
        elif roster_team != inferred_team:
            n_changed += 1
            display_team, changed = roster_team, True
        else:
            display_team, changed = inferred_team, False
        enriched.append((pid, feat, score, display_team, changed))

    print(f"[INFO] of {len(candidates)} candidates: {n_changed} team_changed_this_offseason=1, "
          f"{n_no_roster_match} not found on any confirmed 2026 roster row (team left as inferred, not flagged)")

    ranked = sorted(enriched, key=lambda x: x[2], reverse=True)[: args.top]

    print(f"\nTop {len(ranked)} by predicted spike probability, {args.season} week {args.week} (roster-corrected):")
    header = f"{'Name':24} {'Pos':4} {'Team':5} {'Score':>7}  {'team_changed_this_offseason'}"
    print(header)
    print("-" * len(header))
    for pid, feat, score, display_team, changed in ranked:
        display = names.get(pid, pid)
        print(f"{display:24.24} {feat['_pos']:4} {display_team:5} {score:7.4f}  {int(changed)}")


if __name__ == "__main__":
    main()
