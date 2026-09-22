#!/usr/bin/env python3
"""Test Group 4 (Vegas) features: implied_team_total, game_total, team_spread
-- sourced from team_week_stats (spread/total/implied_total, populated
2010-2025, mostly unpopulated for not-yet-posted 2026 weeks). Train baseline
(current 13-feature production set) vs candidate (baseline + these 3) on
2010-2023, compare precision@10 on 2024 -- pooled AND per-position (RB/WR/TE
primary, QB secondary) via the shared evaluation/metrics.py helper, same
convention as every prior feature-group test in this repo.

Unlike every trailing feature already in features_lib.py, these describe the
UPCOMING game, not a trailing window over prior games -- a Vegas line is set
and known days before kickoff, so using the CURRENT week's own line for the
CURRENT week's prediction is not leakage the way using the current week's own
STATS would be.

Also reports the 2026-specific NULL rate for all three features separately
from the 2010-2024 rate used for training/eval: lines may not be posted yet
for future weeks at scoring time, which would make the group unusable live
for those weeks even if it tests well on 2024.

NOTE: 2024 is the designated development slice for feature-group testing (see
CLAUDE.md "Methodology rules (standing)") -- it has already been reused by
Group 1, Group 2, and now Group 3, so it is not virgin. A win here is a green
light to keep developing this group, not a green light to promote it:
promotion requires prospective confirmation on live 2026 weeks in addition to
this result. 2025 is closed. 2023 is held in reserve for a future
second-opinion decision and is deliberately NOT used here.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import average_precision_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "model"))
from features_lib import FeatureEngine, PRODUCTION_FEATURE_COLS, VEGAS_FEATURES
from metrics import POSITIONS, mean_precision_at_k, per_position_report

BASE_FEATURE_COLS = PRODUCTION_FEATURE_COLS
CANDIDATE_FEATURES = VEGAS_FEATURES
ALL_COLS = BASE_FEATURE_COLS + CANDIDATE_FEATURES

TRAIN_SEASONS = [s for s in range(2010, 2024) if s != 2019]  # 2010-2023, no 2019
TEST_SEASON = 2024
SCALE_POS_WEIGHT = 20.13  # matches production / prior hypothesis tests

MODEL_PARAMS = dict(
    objective="binary:logistic",
    eval_metric="aucpr",
    n_estimators=200,
    max_depth=4,
    learning_rate=0.1,
    importance_type="gain",
    random_state=42,
)


def report_2026_null_rate(engine):
    """Group 4-specific check: NULL rate of the Vegas features for 2026,
    independent of the 2010-2024 train/eval population above. Iterates
    team_week_stats directly (not engine.player_history, which only has
    ACTUALLY PLAYED games -- most of 2026 hasn't been played yet, and the
    whole point of this check is future, not-yet-played weeks)."""
    print("\n=== 2026 NULL rate for Vegas features (team-week level, not player-week) ===")
    print("(a not-yet-posted line means the feature is unusable for that week's LIVE scoring,")
    print(" independent of whether the group tests well on the 2024 slice below)")
    by_week = {}
    for (season, week, _team), (spread, total, implied_total) in engine.team_week_vegas.items():
        if season != 2026:
            continue
        n, n_null = by_week.get(week, (0, 0))
        by_week[week] = (n + 1, n_null + (1 if implied_total is None else 0))
    total_n = sum(n for n, _ in by_week.values())
    total_null = sum(nn for _, nn in by_week.values())
    print(f"{'week':4} {'team-weeks':>10} {'NULL':>6} {'NULL rate':>10}")
    for week in sorted(by_week):
        n, n_null = by_week[week]
        print(f"{week:4} {n:10} {n_null:6} {100 * n_null / n:9.1f}%")
    print(f"{'ALL':4} {total_n:10} {total_null:6} {100 * total_null / total_n:9.1f}%")


def main():
    seasons = TRAIN_SEASONS + [TEST_SEASON]
    con = sqlite3.connect(str(REPO_ROOT / "faab_history_core_v0_1.db"))
    con.row_factory = sqlite3.Row
    engine = FeatureEngine(con)

    report_2026_null_rate(engine)

    ph = ",".join("?" * len(seasons))
    labels = {
        (s, w, pid): flag
        for s, w, pid, flag in con.execute(
            f"SELECT season, week, player_id, spike_flag FROM labels_player_week WHERE season IN ({ph})", seasons
        )
    }
    con.close()

    # Recompute every feature fresh via FeatureEngine (not read from the
    # persisted player_week_features table) -- the 3 candidate features
    # aren't persisted anywhere yet, and recomputing everything from one
    # source guarantees baseline and candidate rows line up exactly.
    rows = []
    n_checked, n_no_label = 0, 0
    for pid, entries in engine.player_history.items():
        for (season, week, _team, _pos, _touches) in entries:
            if season not in seasons:
                continue
            n_checked += 1
            label = labels.get((season, week, pid))
            if label is None:
                n_no_label += 1
                continue
            feat = engine.compute_features(season, week, pid)
            if feat is None:
                continue
            rows.append((season, week, pid, feat["_pos"], label, *[feat[c] for c in ALL_COLS]))

    df = pd.DataFrame(rows, columns=["season", "week", "player_id", "pos", "spike_flag", *ALL_COLS])
    print(f"\n[INFO] {n_checked} non-playoff player-weeks checked across seasons {seasons[0]}-{seasons[-1]} "
          f"(excl. 2019), {n_no_label} with no label row, {len(df)} eligible+labeled rows")

    print(f"\nNULL rate per candidate feature over {len(df)} rows (2010-2024, train+test population):")
    for c in CANDIDATE_FEATURES:
        n_null = df[c].isna().sum()
        print(f"  {c}: {n_null} ({100 * n_null / len(df):.2f}%)")

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"] == TEST_SEASON].copy()
    print(f"\nTrain rows: {len(train)} (2010-2023, excl. 2019)")
    print(f"Test rows:  {len(test)} (2024)")
    print(f"Train spike rate: {train['spike_flag'].mean():.4f}")
    print(f"Test spike rate:  {test['spike_flag'].mean():.4f}")

    print("\n*** NOTE: 2024 is the designated (reused) development slice -- see CLAUDE.md. ***")
    print("A positive result here is a green light to keep developing this group, not to promote it.")
    print("Promotion requires prospective confirmation on live 2026 weeks in addition to this result,")
    print("AND requires the 2026 NULL-rate report above to be low enough for the group to be usable live.")

    results = {}
    per_pos_results = {}
    models = {}
    for label, cols in [("baseline (13 features)", BASE_FEATURE_COLS),
                         ("candidate (13 + Vegas)", ALL_COLS)]:
        X_train, y_train = train[cols], train["spike_flag"]
        X_test, y_test = test[cols], test["spike_flag"]

        model = XGBClassifier(scale_pos_weight=SCALE_POS_WEIGHT, **MODEL_PARAMS)
        model.fit(X_train, y_train)
        proba = model.predict_proba(X_test)[:, 1]

        test_scored = test.copy()
        test_scored["proba"] = proba
        p10 = mean_precision_at_k(test_scored, 10)
        p25 = mean_precision_at_k(test_scored, 25)
        prauc = average_precision_score(y_test, proba)
        results[label] = (p10, p25, prauc)
        per_pos_results[label] = per_position_report(test_scored, test_scored, k=10, positions=POSITIONS)
        models[label] = model

    print(f"\n{'model':42} {'P@10 (2024)':>12} {'P@25 (2024)':>12} {'PR-AUC (2024)':>14}")
    print("-" * 84)
    for label, (p10, p25, prauc) in results.items():
        print(f"{label:42} {p10:12.4f} {p25:12.4f} {prauc:14.4f}")

    base_p10 = results["baseline (13 features)"][0]
    cand_p10 = results["candidate (13 + Vegas)"][0]
    delta = cand_p10 - base_p10
    print(f"\nDelta P@10 pooled (candidate - baseline): {delta:+.4f}")
    if delta > 0.005:
        print("Pooled VERDICT: candidate beats baseline on 2024 precision@10.")
    else:
        print("Pooled VERDICT: candidate does NOT meaningfully beat baseline on 2024 precision@10.")

    print(f"\n=== Per-position precision@10 (2024) -- RB/WR/TE primary, QB secondary ===")
    print(f"{'pos':4} {'baseline':>18} {'candidate':>18} {'delta':>9}   base rate")
    print("-" * 68)
    for pos in ("RB", "WR", "TE", "QB"):
        b = per_pos_results["baseline (13 features)"][pos]
        c = per_pos_results["candidate (13 + Vegas)"][pos]
        b_s = f"{b['hits']}/{b['n']}={b['precision']:.4f}" if b["precision"] is not None else "n/a"
        c_s = f"{c['hits']}/{c['n']}={c['precision']:.4f}" if c["precision"] is not None else "n/a"
        d_s = f"{c['precision']-b['precision']:+.4f}" if (b["precision"] is not None and c["precision"] is not None) else "n/a"
        br_s = f"{c['base_rate']:.4f}" if c["base_rate"] is not None else "n/a"
        tag = "  <-- primary" if pos != "QB" else "  (secondary)"
        print(f"{pos:4} {b_s:>18} {c_s:>18} {d_s:>9}   {br_s}{tag}")

    print("\n=== Candidate model feature importances (gain) ===")
    cand_model = models["candidate (13 + Vegas)"]
    for name, imp in sorted(zip(ALL_COLS, cand_model.feature_importances_), key=lambda x: x[1], reverse=True):
        marker = "  <-- Group 4" if name in CANDIDATE_FEATURES else ""
        print(f"  {name}: {imp:.4f}{marker}")

    print("\nNo model saved, no metadata written -- this is a hypothesis test only. "
          "Group 4 is NOT adopted into production regardless of this outcome.")


if __name__ == "__main__":
    main()
