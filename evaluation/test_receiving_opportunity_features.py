#!/usr/bin/env python3
"""Test Group 1 (receiving-opportunity) features: trailing_target_share,
trailing_air_yards_share, trailing_adot. Train baseline (current production
feature set) vs candidate (baseline + these 3) on 2010-2023, compare mean
precision@10 on 2024.

NOTE: 2024 is being reused as a validation slice here. It already appeared
as training data in evaluation/eval_share_delta_on_2025.py's final 2010-2024
train / 2025 test pair, so it is not a virgin test year -- 2025 is closed
(that already-decided test), and 2026 has only one played week, so 2024 is
the least-bad option available for this group's hypothesis test. If Group 1
looks promising here, it still needs a genuine held-out year before
adoption, not just a repeat of 2024.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import average_precision_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "model"))
from features_lib import PRODUCTION_FEATURE_COLS, FeatureEngine, RECEIVING_OPPORTUNITY_FEATURES

# Current production feature set (features_lib.PRODUCTION_FEATURE_COLS --
# what train_production_model.py actually trains on). Deliberately NOT
# features_lib.PERSISTED_FEATURE_COLS, which also carries the rejected
# share_delta_vs_prior_season (see features_lib.py for why the two differ).
BASE_FEATURE_COLS = PRODUCTION_FEATURE_COLS
CANDIDATE_FEATURES = RECEIVING_OPPORTUNITY_FEATURES
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
    engine = FeatureEngine(con)

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
            rows.append((season, week, pid, label, *[feat[c] for c in ALL_COLS]))

    df = pd.DataFrame(rows, columns=["season", "week", "player_id", "spike_flag", *ALL_COLS])
    print(f"[INFO] {n_checked} non-playoff player-weeks checked across seasons {seasons[0]}-{seasons[-1]} "
          f"(excl. 2019), {n_no_label} with no label row, {len(df)} eligible+labeled rows")

    print(f"\nNULL rate per candidate feature over {len(df)} rows (2010-2024):")
    for c in CANDIDATE_FEATURES:
        n_null = df[c].isna().sum()
        print(f"  {c}: {n_null} ({100 * n_null / len(df):.2f}%)")

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"] == TEST_SEASON].copy()
    print(f"\nTrain rows: {len(train)} (2010-2023, excl. 2019)")
    print(f"Test rows:  {len(test)} (2024)")
    print(f"Train spike rate: {train['spike_flag'].mean():.4f}")
    print(f"Test spike rate:  {test['spike_flag'].mean():.4f}")

    print("\n*** NOTE: 2024 is a REUSED validation slice, not a virgin test year. ***")
    print("It already served as training data in the final share_delta-vs-2025 evaluation.")
    print("2025 is closed (that already-decided held-out test); 2026 has only one played week.")
    print("A positive result here is a green light to test further, not a green light to adopt.")

    results = {}
    models = {}
    for label, cols in [("baseline (10 features)", BASE_FEATURE_COLS),
                         ("candidate (10 + receiving-opportunity)", ALL_COLS)]:
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
        models[label] = model

    print(f"\n{'model':42} {'P@10 (2024)':>12} {'P@25 (2024)':>12} {'PR-AUC (2024)':>14}")
    print("-" * 84)
    for label, (p10, p25, prauc) in results.items():
        print(f"{label:42} {p10:12.4f} {p25:12.4f} {prauc:14.4f}")

    base_p10 = results["baseline (10 features)"][0]
    cand_p10 = results["candidate (10 + receiving-opportunity)"][0]
    delta = cand_p10 - base_p10
    print(f"\nDelta P@10 (candidate - baseline): {delta:+.4f}")
    if delta > 0.005:
        print("VERDICT: candidate beats baseline on 2024 precision@10.")
    else:
        print("VERDICT: candidate does NOT meaningfully beat baseline on 2024 precision@10.")

    print("\n=== Candidate model feature importances (gain) ===")
    cand_model = models["candidate (10 + receiving-opportunity)"]
    for name, imp in sorted(zip(ALL_COLS, cand_model.feature_importances_), key=lambda x: x[1], reverse=True):
        marker = "  <-- Group 1" if name in CANDIDATE_FEATURES else ""
        print(f"  {name}: {imp:.4f}{marker}")

    print("\nNo model saved, no metadata written -- this is a hypothesis test only. "
          "Group 1 is NOT adopted into production regardless of this outcome.")


if __name__ == "__main__":
    main()
