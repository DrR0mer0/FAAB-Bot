#!/usr/bin/env python3
"""Test Group 6 (recency-weighted decay): NOT a new feature group -- this
modifies HOW 4 EXISTING production features are computed (trailing_touches_avg,
trailing_team_touch_share, trailing_target_share, trailing_air_yards_share),
under the SAME names, via FeatureEngine.compute_recency_weighted_usage()
(features_lib.py). Trains baseline (production compute_features(), flat mean
over the last 3 games) vs two modified-baseline variants that replace those 4
columns with an exponentially recency-weighted version, keeping the other 9
production columns (and PRODUCTION_FEATURE_COLS' names) identical:

  A: same 3-game window, exponentially weighted toward the most recent game
  B: widened to a 6-game window (fewer if not yet available), same decay

Decay constant: FeatureEngine.RECENCY_DECAY = 0.5 per game further back (a
1-game half-life), same for both variants -- see features_lib.py's comment
above compute_recency_weighted_usage for the reasoning. Only the window size
differs between A and B; the decay rate is fixed so that's the one thing
being varied between them.

Eligibility is fixed at >=3 prior games for ALL THREE models (baseline, A,
B) -- compute_recency_weighted_usage() enforces the same floor regardless of
window_size, so widening the window in variant B never admits a player the
baseline candidate pool wouldn't already include; the three models are
trained and evaluated on the exact same row population, only the 4 columns'
values differ.

Train 2010-2023, evaluate on 2024 (the designated development slice per
CLAUDE.md's evaluation-slice rule) -- per-position precision@10 (RB/WR/TE
primary, QB secondary), pooled for continuity, via evaluation/metrics.py.
2025 is closed; 2023 is reserved for a future second-opinion decision and
deliberately not used here.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import average_precision_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "model"))
from features_lib import FeatureEngine, PRODUCTION_FEATURE_COLS
from metrics import POSITIONS, mean_precision_at_k, per_position_report

MODIFIED_COLS = [
    "trailing_touches_avg",
    "trailing_team_touch_share",
    "trailing_target_share",
    "trailing_air_yards_share",
]
UNCHANGED_COLS = [c for c in PRODUCTION_FEATURE_COLS if c not in MODIFIED_COLS]

WINDOW_A = 3
WINDOW_B = 6

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


def cols_for(variant):
    """variant None -> baseline production column names. variant "A"/"B" ->
    the same PRODUCTION_FEATURE_COLS order, but the 4 MODIFIED_COLS point at
    that variant's prefixed columns in the working DataFrame instead."""
    if variant is None:
        return list(PRODUCTION_FEATURE_COLS)
    prefix = f"{variant}__"
    return [f"{prefix}{c}" if c in MODIFIED_COLS else c for c in PRODUCTION_FEATURE_COLS]


def build_X(df_split, variant):
    X = df_split[cols_for(variant)].copy()
    X.columns = PRODUCTION_FEATURE_COLS  # canonical names for training/reporting either way
    return X


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

    print(f"[INFO] FeatureEngine.RECENCY_DECAY = {FeatureEngine.RECENCY_DECAY} "
          f"(weight = decay**games_ago, same for variant A window={WINDOW_A} and variant B window={WINDOW_B})")

    # Recompute every feature fresh via FeatureEngine -- baseline via
    # compute_features(), both recency-weighted variants via
    # compute_recency_weighted_usage(), all three off the SAME eligibility
    # gate so all three models train/evaluate on identical row populations.
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
            pos = feat["_pos"]
            wa = engine.compute_recency_weighted_usage(season, week, pid, pos, WINDOW_A)
            wb = engine.compute_recency_weighted_usage(season, week, pid, pos, WINDOW_B)
            row = {"season": season, "week": week, "player_id": pid, "pos": pos, "spike_flag": label}
            for c in PRODUCTION_FEATURE_COLS:
                row[c] = feat[c]
            for c in MODIFIED_COLS:
                row[f"A__{c}"] = wa[c]
                row[f"B__{c}"] = wb[c]
            rows.append(row)

    df = pd.DataFrame(rows)
    print(f"[INFO] {n_checked} non-playoff player-weeks checked across seasons {seasons[0]}-{seasons[-1]} "
          f"(excl. 2019), {n_no_label} with no label row, {len(df)} eligible+labeled rows "
          f"(identical population for baseline, variant A, variant B)")

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"] == TEST_SEASON].copy()
    print(f"\nTrain rows: {len(train)} (2010-2023, excl. 2019)")
    print(f"Test rows:  {len(test)} (2024)")
    print(f"Train spike rate: {train['spike_flag'].mean():.4f}")
    print(f"Test spike rate:  {test['spike_flag'].mean():.4f}")

    print("\n*** NOTE: 2024 is the designated (reused) development slice -- see CLAUDE.md. ***")
    print("A positive result here is a green light to keep developing this variant, not to adopt it.")
    print("Promotion requires prospective confirmation on live 2026 weeks in addition to this result.")

    results = {}
    per_pos_results = {}
    models = {}
    variants = [("baseline (flat 3-game mean)", None),
                (f"variant A (weighted, window={WINDOW_A})", "A"),
                (f"variant B (weighted, window={WINDOW_B})", "B")]
    for label, variant in variants:
        X_train, y_train = build_X(train, variant), train["spike_flag"]
        X_test, y_test = build_X(test, variant), test["spike_flag"]

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

    print(f"\n{'model':38} {'P@10 (2024)':>12} {'P@25 (2024)':>12} {'PR-AUC (2024)':>14}")
    print("-" * 80)
    for label, (p10, p25, prauc) in results.items():
        print(f"{label:38} {p10:12.4f} {p25:12.4f} {prauc:14.4f}")

    base_label = "baseline (flat 3-game mean)"
    base_p10 = results[base_label][0]
    for label, variant in variants[1:]:
        delta = results[label][0] - base_p10
        print(f"\nDelta P@10 pooled ({label} - baseline): {delta:+.4f}")
        if delta > 0.005:
            print(f"Pooled VERDICT: {label} beats baseline on 2024 precision@10.")
        else:
            print(f"Pooled VERDICT: {label} does NOT meaningfully beat baseline on 2024 precision@10.")

    for label, variant in variants[1:]:
        print(f"\n=== Per-position precision@10 (2024) -- baseline vs {label} -- RB/WR/TE primary, QB secondary ===")
        print(f"{'pos':4} {'baseline':>18} {'variant':>18} {'delta':>9}   base rate")
        print("-" * 68)
        for pos in ("RB", "WR", "TE", "QB"):
            b = per_pos_results[base_label][pos]
            c = per_pos_results[label][pos]
            b_s = f"{b['hits']}/{b['n']}={b['precision']:.4f}" if b["precision"] is not None else "n/a"
            c_s = f"{c['hits']}/{c['n']}={c['precision']:.4f}" if c["precision"] is not None else "n/a"
            d_s = f"{c['precision']-b['precision']:+.4f}" if (b["precision"] is not None and c["precision"] is not None) else "n/a"
            br_s = f"{c['base_rate']:.4f}" if c["base_rate"] is not None else "n/a"
            tag = "  <-- primary" if pos != "QB" else "  (secondary)"
            print(f"{pos:4} {b_s:>18} {c_s:>18} {d_s:>9}   {br_s}{tag}")

    for label, variant in variants:
        print(f"\n=== {label} feature importances (gain) ===")
        m = models[label]
        for name, imp in sorted(zip(PRODUCTION_FEATURE_COLS, m.feature_importances_), key=lambda x: x[1], reverse=True):
            marker = "  <-- recency-weighted" if (variant is not None and name in MODIFIED_COLS) else ""
            print(f"  {name}: {imp:.4f}{marker}")

    print("\nNo model saved, no metadata written -- this is a hypothesis test only. "
          "Group 6 (neither variant) is NOT adopted into production regardless of this outcome.")


if __name__ == "__main__":
    main()
