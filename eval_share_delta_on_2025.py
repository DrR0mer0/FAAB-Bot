#!/usr/bin/env python3
"""Final evaluation of the winning feature set (9 original + share_delta_vs_prior_season):
train on 2010-2024, evaluate exactly once on 2025 (the only untouched year).
"""
import sqlite3

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
from xgboost import XGBClassifier

from features_lib import FEATURE_COLS

TRAIN_SEASONS = [s for s in range(2010, 2025) if s != 2019]  # 2010-2024, no 2019
TEST_SEASONS = [2025]
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


def precision_at_k(df, k):
    results = []
    for (season, week), grp in df.groupby(["season", "week"]):
        top = grp.sort_values("proba", ascending=False).head(k)
        denom = len(top)
        hits = int(top["spike_flag"].sum())
        results.append((season, week, hits, denom, hits / denom if denom else float("nan")))
    return results


def mean_precision_at_k(df, k):
    rows = precision_at_k(df, k)
    precs = [p for (_, _, _, _, p) in rows if not np.isnan(p)]
    return sum(precs) / len(precs), rows


def main():
    seasons = TRAIN_SEASONS + TEST_SEASONS
    con = sqlite3.connect("faab_history_core_v0_1.db")
    ph = ",".join("?" * len(seasons))
    df = pd.read_sql_query(
        f"""SELECT f.season, f.week, f.player_id, {", ".join("f." + c for c in FEATURE_COLS)}, l.spike_flag
           FROM player_week_features f
           JOIN labels_player_week l
             ON f.season=l.season AND f.week=l.week AND f.player_id=l.player_id
           WHERE f.season IN ({ph})""",
        con,
        params=seasons,
    )
    con.close()

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"].isin(TEST_SEASONS)].copy()
    print(f"Train rows: {len(train)} (seasons {TRAIN_SEASONS[0]}-{TRAIN_SEASONS[-1]}, excl. 2019)")
    print(f"Test rows: {len(test)} (season {TEST_SEASONS[0]})")
    print(f"Train spike rate: {train['spike_flag'].mean():.4f}")
    print(f"Test spike rate: {test['spike_flag'].mean():.4f}")
    print(f"Features used ({len(FEATURE_COLS)}): {FEATURE_COLS}")

    X_train, y_train = train[FEATURE_COLS], train["spike_flag"]
    X_test, y_test = test[FEATURE_COLS], test["spike_flag"]

    model = XGBClassifier(scale_pos_weight=SCALE_POS_WEIGHT, **MODEL_PARAMS)
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
        print(f"\n=== Precision@{k} per week (2025) ===")
        mean_p, rows = mean_precision_at_k(test_scored, k)
        for season, week, hits, denom, p in rows:
            print(f"  {season} wk {week:2d}: {hits:2d}/{denom:2d} = {p:.3f}")
        print(f"  --> mean precision@{k} across {len(rows)} weeks: {mean_p:.4f}")

    print("\n=== Feature importances (gain) ===")
    for name, imp in sorted(zip(FEATURE_COLS, model.feature_importances_), key=lambda x: x[1], reverse=True):
        print(f"  {name}: {imp:.4f}")

    import joblib
    joblib.dump(
        {"model": model, "feature_cols": FEATURE_COLS, "scale_pos_weight": SCALE_POS_WEIGHT, "train_seasons": TRAIN_SEASONS},
        "odds_xgb_model_2010_2024_with_share_delta.joblib",
    )
    print("\n[SAVED] odds_xgb_model_2010_2024_with_share_delta.joblib")


if __name__ == "__main__":
    main()
