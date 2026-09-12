#!/usr/bin/env python3
"""Tune scale_pos_weight on a proper validation split, then do a single,
final out-of-time evaluation on 2023-2024.

Validation phase: train on 2010-2021, validate on 2022 (chronological, no
shuffling), select scale_pos_weight by mean precision@10 on 2022.

Final phase: retrain fresh on the full 2010-2022 training period with the
winning scale_pos_weight, and evaluate exactly once on 2023-2024 -- that test
set is touched only here, once.
"""
import argparse
import sqlite3
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = str(REPO_ROOT / "odds_xgb_model.joblib")

TRAIN_SUB_SEASONS = list(range(2010, 2022))   # 2010-2021
VAL_SEASONS = [2022]
FULL_TRAIN_SEASONS = list(range(2010, 2023))  # 2010-2022
TEST_SEASONS = [2023, 2024]

CANDIDATE_SPW = [1, 5, 10, None, 30]  # None -> filled in with the actual imbalance ratio

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

    train_sub = df[df["season"].isin(TRAIN_SUB_SEASONS)].copy()
    val = df[df["season"].isin(VAL_SEASONS)].copy()
    full_train = df[df["season"].isin(FULL_TRAIN_SEASONS)].copy()
    test = df[df["season"].isin(TEST_SEASONS)].copy()

    actual_ratio = (train_sub["spike_flag"] == 0).sum() / (train_sub["spike_flag"] == 1).sum()
    candidates = [actual_ratio if c is None else c for c in CANDIDATE_SPW]

    print(f"Train-sub rows: {len(train_sub)} (seasons 2010-2021), spike rate: {train_sub['spike_flag'].mean():.4f}")
    print(f"Validation rows: {len(val)} (season 2022), spike rate: {val['spike_flag'].mean():.4f}")
    print(f"Actual class imbalance ratio (neg/pos) in train-sub: {actual_ratio:.2f}")

    X_train_sub, y_train_sub = train_sub[FEATURE_COLS], train_sub["spike_flag"]
    X_val, y_val = val[FEATURE_COLS], val["spike_flag"]

    print("\n=== scale_pos_weight validation sweep (train=2010-2021, val=2022) ===")
    results = []
    for spw in candidates:
        model = XGBClassifier(scale_pos_weight=spw, **MODEL_PARAMS)
        model.fit(X_train_sub, y_train_sub)
        proba = model.predict_proba(X_val)[:, 1]

        val_scored = val.copy()
        val_scored["proba"] = proba

        p10, _ = mean_precision_at_k(val_scored, 10)
        p25, _ = mean_precision_at_k(val_scored, 25)
        prauc = average_precision_score(y_val, proba)
        results.append((spw, p10, p25, prauc))

    print(f"{'scale_pos_weight':>18} {'val P@10':>10} {'val P@25':>10} {'val PR-AUC':>11}")
    print("-" * 52)
    for spw, p10, p25, prauc in results:
        print(f"{spw:18.2f} {p10:10.4f} {p25:10.4f} {prauc:11.4f}")

    winner_spw, winner_p10, _, _ = max(results, key=lambda r: r[1])
    print(f"\nWinner (highest val precision@10): scale_pos_weight={winner_spw:.2f} (val P@10={winner_p10:.4f})")

    # ---- Final phase: retrain on full 2010-2022, evaluate once on 2023-2024 ----
    print("\n" + "=" * 60)
    print(f"FINAL MODEL: retrained on 2010-2022 with scale_pos_weight={winner_spw:.2f}")
    print(f"Evaluated once, held out, on 2023-2024")
    print("=" * 60)

    X_full_train, y_full_train = full_train[FEATURE_COLS], full_train["spike_flag"]
    X_test, y_test = test[FEATURE_COLS], test["spike_flag"]

    final_model = XGBClassifier(scale_pos_weight=winner_spw, **MODEL_PARAMS)
    final_model.fit(X_full_train, y_full_train)

    proba = final_model.predict_proba(X_test)[:, 1]
    pred = (proba >= 0.5).astype(int)

    print(f"\nTrain rows: {len(full_train)} (seasons 2010-2022)")
    print(f"Test rows: {len(test)} (seasons 2023-2024)")
    print(f"Train spike rate: {y_full_train.mean():.4f}")
    print(f"Test spike rate: {y_test.mean():.4f}")

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
        mean_p, rows = mean_precision_at_k(test_scored, k)
        for season, week, hits, denom, p in rows:
            print(f"  {season} wk {week:2d}: {hits:2d}/{denom:2d} = {p:.3f}")
        print(f"  --> mean precision@{k} across {len(rows)} weeks: {mean_p:.4f}")

    print("\n=== Feature importances (gain) ===")
    importances = sorted(zip(FEATURE_COLS, final_model.feature_importances_), key=lambda x: x[1], reverse=True)
    for name, imp in importances:
        print(f"  {name}: {imp:.4f}")

    joblib.dump(
        {"model": final_model, "feature_cols": FEATURE_COLS, "scale_pos_weight": winner_spw,
         "train_seasons": FULL_TRAIN_SEASONS, "model_params": MODEL_PARAMS},
        MODEL_PATH,
    )
    print(f"\n[SAVED] model persisted to {MODEL_PATH}")


if __name__ == "__main__":
    main()
