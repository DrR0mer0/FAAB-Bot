#!/usr/bin/env python3
"""Train the production O.D.D.S. model on ALL available seasons (through
2025), using the scale_pos_weight validated in tune_and_finalize_xgboost.py.

This is the model score_week.py uses for live scoring going forward. The
original 2010-2022-trained / 2023-2024-tested model (odds_xgb_model.joblib)
is left untouched as the validation record -- it is not used for scoring.
"""
import argparse
import sqlite3

import joblib
import pandas as pd
from xgboost import XGBClassifier

MODEL_PATH = "odds_xgb_model_production.joblib"
SCALE_POS_WEIGHT = 20.13  # validated in tune_and_finalize_xgboost.py

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="faab_history_core_v0_1.db")
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

    seasons = sorted(df["season"].unique().tolist())
    print(f"Training rows: {len(df)}, seasons: {seasons}")
    print(f"Spike rate: {df['spike_flag'].mean():.4f}")

    X, y = df[FEATURE_COLS], df["spike_flag"]
    model = XGBClassifier(scale_pos_weight=SCALE_POS_WEIGHT, **MODEL_PARAMS)
    model.fit(X, y)

    print("\n=== Feature importances (gain) ===")
    for name, imp in sorted(zip(FEATURE_COLS, model.feature_importances_), key=lambda x: x[1], reverse=True):
        print(f"  {name}: {imp:.4f}")

    joblib.dump(
        {"model": model, "feature_cols": FEATURE_COLS, "scale_pos_weight": SCALE_POS_WEIGHT,
         "train_seasons": seasons, "model_params": MODEL_PARAMS},
        MODEL_PATH,
    )
    print(f"\n[SAVED] production model persisted to {MODEL_PATH}")


if __name__ == "__main__":
    main()
