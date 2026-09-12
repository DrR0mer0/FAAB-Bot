#!/usr/bin/env python3
"""Test whether share_delta_vs_prior_season improves the model: train
baseline (9 features) vs candidate (9 + new feature) on 2010-2022, compare
mean precision@10 on 2023 only. 2024 and 2025 are left untouched.
"""
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "model"))
from features_lib import FeatureEngine

# NOTE: this experiment already won and share_delta_vs_prior_season was
# folded into features_lib.FEATURE_COLS. Kept as a record of the comparison;
# BASE_FEATURE_COLS below is the pre-adoption 9-feature set it was tested
# against, not features_lib.FEATURE_COLS (which now includes the winner).
CANDIDATE_FEATURE = "share_delta_vs_prior_season"
BASE_FEATURE_COLS = [
    "trailing_touches_avg", "trailing_touches_trend", "trailing_team_touch_share",
    "opponent_position_matchup", "experience_seasons", "games_played_this_season",
    "is_short_week", "is_home", "is_bye_return", "starter_absent_proxy",
]

TRAIN_SEASONS = list(range(2010, 2023))  # 2010-2022
TEST_SEASON = 2023
SCALE_POS_WEIGHT = 20.13

MODEL_PARAMS = dict(
    objective="binary:logistic",
    eval_metric="aucpr",
    n_estimators=200,
    max_depth=4,
    learning_rate=0.1,
    importance_type="gain",
    random_state=42,
)


def mean_precision_at_k(df, k):
    precs = []
    for (season, week), grp in df.groupby(["season", "week"]):
        top = grp.sort_values("proba", ascending=False).head(k)
        precs.append(top["spike_flag"].sum() / len(top))
    return sum(precs) / len(precs)


def main():
    seasons = TRAIN_SEASONS + [TEST_SEASON]
    con = sqlite3.connect(str(REPO_ROOT / "faab_history_core_v0_1.db"))
    con.row_factory = sqlite3.Row
    ph = ",".join("?" * len(seasons))
    df = pd.read_sql_query(
        f"""SELECT f.season, f.week, f.player_id, {", ".join("f." + c for c in BASE_FEATURE_COLS)}, l.spike_flag
           FROM player_week_features f
           JOIN labels_player_week l
             ON f.season=l.season AND f.week=l.week AND f.player_id=l.player_id
           WHERE f.season IN ({ph})""",
        con,
        params=seasons,
    )

    engine = FeatureEngine(con)
    con.close()

    # Compute the candidate feature on top of the already-persisted 9, reusing
    # trailing_team_touch_share instead of recomputing everything from scratch.
    prior_share_cache = {}

    def prior_share(pid, season):
        key = (pid, season)
        if key not in prior_share_cache:
            prior_share_cache[key] = engine._prior_season_avg_share(pid, season)
        return prior_share_cache[key]

    deltas = []
    for row in df.itertuples(index=False):
        p_share = prior_share(row.player_id, row.season)
        touch_share = row.trailing_team_touch_share
        if touch_share is None or p_share is None or (isinstance(touch_share, float) and np.isnan(touch_share)):
            deltas.append(np.nan)
        else:
            deltas.append(touch_share - p_share)
    df[CANDIDATE_FEATURE] = deltas

    n_null = df[CANDIDATE_FEATURE].isna().sum()
    print(f"share_delta_vs_prior_season NULL rate over {len(df)} rows (2010-2023): {n_null} ({100*n_null/len(df):.2f}%)")

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"] == TEST_SEASON].copy()
    print(f"Train rows: {len(train)} (2010-2022), Test rows: {len(test)} (2023)")

    results = {}
    for label, cols in [("baseline (9 features)", BASE_FEATURE_COLS), ("candidate (9 + share_delta)", BASE_FEATURE_COLS + [CANDIDATE_FEATURE])]:
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

    print(f"\n{'model':30} {'P@10 (2023)':>12} {'P@25 (2023)':>12} {'PR-AUC (2023)':>14}")
    print("-" * 70)
    for label, (p10, p25, prauc) in results.items():
        print(f"{label:30} {p10:12.4f} {p25:12.4f} {prauc:14.4f}")

    base_p10 = results["baseline (9 features)"][0]
    cand_p10 = results["candidate (9 + share_delta)"][0]
    delta = cand_p10 - base_p10
    print(f"\nDelta P@10 (candidate - baseline): {delta:+.4f}")
    if delta > 0.005:
        print("VERDICT: candidate beats baseline on 2023 precision@10.")
    else:
        print("VERDICT: candidate does NOT meaningfully beat baseline on 2023 precision@10.")


if __name__ == "__main__":
    main()
