#!/usr/bin/env python3
"""Train the production O.D.D.S. model on 2010-2025 (TRAIN_SEASONS below),
using the scale_pos_weight validated in tune_and_finalize_xgboost.py.

This is the model score_week.py uses for live scoring going forward. The
original 2010-2022-trained / 2023-2024-tested model (odds_xgb_model.joblib)
is left untouched as the validation record -- it is not used for scoring.

TRAIN_SEASONS is explicit, not "whatever's in player_week_features": that
table now also holds partial in-progress seasons (2026 week 1-2, as of the
Group 1 promotion) once generate_player_week_features.py has been run for
them, which must NOT silently become training data for a model meant to
score that same season live.
"""
import argparse
import sqlite3
from pathlib import Path

import joblib
import pandas as pd
from xgboost import XGBClassifier

from features_lib import PRODUCTION_FEATURE_COLS
from model_metadata import write_metadata

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL_PATH = str(REPO_ROOT / "odds_xgb_model_production.joblib")
SCALE_POS_WEIGHT = 20.13  # validated in tune_and_finalize_xgboost.py
TRAIN_SEASONS = [s for s in range(2010, 2026) if s != 2019]  # 2010-2025, no 2019

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
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    ph = ",".join("?" * len(TRAIN_SEASONS))
    df = pd.read_sql_query(
        f"""SELECT f.season, f.week, f.player_id, {", ".join("f." + c for c in PRODUCTION_FEATURE_COLS)}, l.spike_flag
           FROM player_week_features f
           JOIN labels_player_week l
             ON f.season=l.season AND f.week=l.week AND f.player_id=l.player_id
           WHERE f.season IN ({ph})""",
        con,
        params=TRAIN_SEASONS,
    )
    con.close()

    seasons = sorted(df["season"].unique().tolist())
    print(f"Training rows: {len(df)}, seasons: {seasons}")
    print(f"Spike rate: {df['spike_flag'].mean():.4f}")

    X, y = df[PRODUCTION_FEATURE_COLS], df["spike_flag"]
    model = XGBClassifier(scale_pos_weight=SCALE_POS_WEIGHT, **MODEL_PARAMS)
    model.fit(X, y)

    print("\n=== Feature importances (gain) ===")
    for name, imp in sorted(zip(PRODUCTION_FEATURE_COLS, model.feature_importances_), key=lambda x: x[1], reverse=True):
        print(f"  {name}: {imp:.4f}")

    joblib.dump(
        {"model": model, "feature_cols": PRODUCTION_FEATURE_COLS, "scale_pos_weight": SCALE_POS_WEIGHT,
         "train_seasons": seasons, "model_params": MODEL_PARAMS},
        MODEL_PATH,
    )
    print(f"\n[SAVED] production model persisted to {MODEL_PATH}")

    meta_path, meta = write_metadata(
        MODEL_PATH, REPO_ROOT, PRODUCTION_FEATURE_COLS, MODEL_PARAMS, SCALE_POS_WEIGHT, seasons,
        extra={"role": "production",
               "description": "Live-scoring model used by score_week.py. 13 features -- promoted with Group 1 "
                               "(trailing_target_share, trailing_air_yards_share, trailing_adot) added to the "
                               "prior 10-feature set. The superseded 10-feature model is preserved as "
                               "odds_xgb_model_production_10feature.joblib for comparison."},
    )
    print(f"[SAVED] metadata sidecar written to {meta_path} (git_commit={meta['git_commit']})")


if __name__ == "__main__":
    main()
