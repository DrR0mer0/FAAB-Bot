#!/usr/bin/env python3
"""Baseline XGBoost classifier for spike_flag, using player_week_features.

Chronological (out-of-time) split: train on TRAIN_SEASONS, test on
TEST_SEASONS. No shuffling, no hyperparameter tuning, no imputation --
XGBoost handles NaN/NULL natively.
"""
import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent

TRAIN_SEASONS = list(range(2010, 2023))  # 2010-2022 inclusive
TEST_SEASONS = [2023, 2024]

FEATURE_COLS = [
    "trailing_touches_avg",
    "trailing_touches_trend",
    "trailing_team_touch_share",
    "opponent_position_matchup",
    "experience_seasons",
    "games_played_this_season",
    "is_short_week",
    "is_home",
    "is_bye_return",
    "starter_absent_proxy",
]


def precision_at_k(df, k):
    """Per-(season,week) precision@k, using predicted probability 'proba'."""
    results = []
    for (season, week), grp in df.groupby(["season", "week"]):
        top = grp.sort_values("proba", ascending=False).head(k)
        denom = len(top)
        hits = int(top["spike_flag"].sum())
        results.append((season, week, hits, denom, hits / denom if denom else float("nan")))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    df = pd.read_sql_query(
        f"""SELECT f.season, f.week, f.player_id, {", ".join("f." + c for c in FEATURE_COLS)}, l.spike_flag
           FROM player_week_features f
           JOIN labels_player_week l
             ON f.season=l.season AND f.week=l.week AND f.player_id=l.player_id""",
        con,
    )
    con.close()

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"].isin(TEST_SEASONS)].copy()
    print(f"Train rows: {len(train)} (seasons {TRAIN_SEASONS[0]}-{TRAIN_SEASONS[-1]})")
    print(f"Test rows: {len(test)} (seasons {TEST_SEASONS})")
    print(f"Train spike rate: {train['spike_flag'].mean():.4f}")
    print(f"Test spike rate: {test['spike_flag'].mean():.4f}")

    X_train, y_train = train[FEATURE_COLS], train["spike_flag"]
    X_test, y_test = test[FEATURE_COLS], test["spike_flag"]

    model = XGBClassifier(
        objective="binary:logistic",
        eval_metric="aucpr",
        n_estimators=200,
        max_depth=4,
        learning_rate=0.1,
        importance_type="gain",
        random_state=42,
    )
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    pred = (proba >= 0.5).astype(int)

    print("\n=== Test-set metrics (threshold 0.5) ===")
    print(f"Precision: {precision_score(y_test, pred, zero_division=0):.4f}")
    print(f"Recall:    {recall_score(y_test, pred, zero_division=0):.4f}")
    print(f"F1:        {f1_score(y_test, pred, zero_division=0):.4f}")
    print(f"PR-AUC:    {average_precision_score(y_test, proba):.4f}")
    print(f"Predicted positives: {pred.sum()} / {len(pred)}  (actual positives: {y_test.sum()})")

    test_scored = test.copy()
    test_scored["proba"] = proba

    for k in (10, 25):
        print(f"\n=== Precision@{k} per week (test set) ===")
        rows = precision_at_k(test_scored, k)
        for season, week, hits, denom, p in rows:
            print(f"  {season} wk {week:2d}: {hits:2d}/{denom:2d} = {p:.3f}")
        precs = [p for (_, _, _, _, p) in rows if not np.isnan(p)]
        print(f"  --> mean precision@{k} across {len(precs)} weeks: {sum(precs) / len(precs):.4f}")

    print("\n=== Feature importances (gain) ===")
    importances = sorted(zip(FEATURE_COLS, model.feature_importances_), key=lambda x: x[1], reverse=True)
    for name, imp in importances:
        print(f"  {name}: {imp:.4f}")


if __name__ == "__main__":
    main()
