#!/usr/bin/env python3
"""Test Group 2 (QB volume + team pass-catcher mix) features:
trailing_pass_attempts, trailing_pass_air_yards, trailing_team_wr_target_rate.
Train baseline (current production feature set) vs candidate (baseline + these
3) on 2010-2023, compare mean precision@10 on 2024 -- pooled, QB-only within
each model's own top 25, AND per-position precision@10 (RB/WR/TE primary, QB
secondary) via the shared evaluation/metrics.py helper used identically by
verify_week.py. The per-position breakdown is the important one here: this
group's first pass won on pooled precision@10 largely by reallocating picks
toward QBs, a ~4x-higher-base-rate position -- that inflates a pooled metric
without necessarily improving discrimination, which is exactly what
RB/WR/TE-specific numbers are for catching.

NOTE: 2024 is being reused as a validation slice here, exactly as in
evaluation/test_receiving_opportunity_features.py -- it already appeared as
training data in evaluation/eval_share_delta_on_2025.py's final 2010-2024
train / 2025 test pair, so it is not a virgin test year. 2025 is closed (that
already-decided held-out test); 2026 has only two played weeks so far. If
Group 2 looks promising here, it still needs a genuine held-out year before
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
from features_lib import FeatureEngine, PRODUCTION_FEATURE_COLS, QB_VOLUME_FEATURES
from metrics import POSITIONS, mean_precision_at_k, per_position_report

BASE_FEATURE_COLS = PRODUCTION_FEATURE_COLS
CANDIDATE_FEATURES = QB_VOLUME_FEATURES
ALL_COLS = BASE_FEATURE_COLS + CANDIDATE_FEATURES

TRAIN_SEASONS = [s for s in range(2010, 2024) if s != 2019]  # 2010-2023, no 2019
TEST_SEASON = 2024
SCALE_POS_WEIGHT = 20.13  # matches production / prior hypothesis tests
TOP_K_FOR_QB_BREAKDOWN = 25

MODEL_PARAMS = dict(
    objective="binary:logistic",
    eval_metric="aucpr",
    n_estimators=200,
    max_depth=4,
    learning_rate=0.1,
    importance_type="gain",
    random_state=42,
)


def qb_precision_within_topk(df, k):
    """Among each week's own top-k (by this model's predicted probability),
    restrict to QB rows and report hit rate there -- how often a QB the
    model ranked highly actually spiked, not spike rate among QBs overall."""
    hits, n = 0, 0
    for (season, week), grp in df.groupby(["season", "week"]):
        top = grp.sort_values("proba", ascending=False).head(k)
        qb_rows = top[top["pos"] == "QB"]
        hits += int(qb_rows["spike_flag"].sum())
        n += len(qb_rows)
    return hits, n, (hits / n if n else None)


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
            rows.append((season, week, pid, feat["_pos"], label, *[feat[c] for c in ALL_COLS]))

    df = pd.DataFrame(rows, columns=["season", "week", "player_id", "pos", "spike_flag", *ALL_COLS])
    print(f"[INFO] {n_checked} non-playoff player-weeks checked across seasons {seasons[0]}-{seasons[-1]} "
          f"(excl. 2019), {n_no_label} with no label row, {len(df)} eligible+labeled rows")

    print(f"\nNULL rate per candidate feature over {len(df)} rows (2010-2024):")
    for c in CANDIDATE_FEATURES:
        n_null = df[c].isna().sum()
        print(f"  {c}: {n_null} ({100 * n_null / len(df):.2f}%)")
    n_qb_rows = (df["pos"] == "QB").sum()
    print(f"QB rows in this population: {n_qb_rows} ({100 * n_qb_rows / len(df):.2f}%)")

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"] == TEST_SEASON].copy()
    print(f"\nTrain rows: {len(train)} (2010-2023, excl. 2019)")
    print(f"Test rows:  {len(test)} (2024)")
    print(f"Train spike rate: {train['spike_flag'].mean():.4f}")
    print(f"Test spike rate:  {test['spike_flag'].mean():.4f}")
    print(f"Test QB spike rate: {test[test['pos']=='QB']['spike_flag'].mean():.4f}")

    print("\n*** NOTE: 2024 is a REUSED validation slice, not a virgin test year. ***")
    print("It already served as training data in the final share_delta-vs-2025 evaluation.")
    print("2025 is closed (that already-decided held-out test); 2026 has only two played weeks.")
    print("A positive result here is a green light to test further, not a green light to adopt.")

    results = {}
    qb_results = {}
    per_pos_results = {}
    models = {}
    for label, cols in [("baseline (10 features)", BASE_FEATURE_COLS),
                         ("candidate (10 + QB-volume)", ALL_COLS)]:
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
        qb_results[label] = qb_precision_within_topk(test_scored, TOP_K_FOR_QB_BREAKDOWN)
        per_pos_results[label] = per_position_report(test_scored, test_scored, k=10, positions=POSITIONS)
        models[label] = model

    print(f"\n{'model':30} {'P@10 (2024)':>12} {'P@25 (2024)':>12} {'PR-AUC (2024)':>14}")
    print("-" * 72)
    for label, (p10, p25, prauc) in results.items():
        print(f"{label:30} {p10:12.4f} {p25:12.4f} {prauc:14.4f}")

    base_p10 = results["baseline (10 features)"][0]
    cand_p10 = results["candidate (10 + QB-volume)"][0]
    delta = cand_p10 - base_p10
    print(f"\nDelta P@10 pooled (candidate - baseline): {delta:+.4f}")
    if delta > 0.005:
        print("Pooled VERDICT: candidate beats baseline on 2024 precision@10.")
    else:
        print("Pooled VERDICT: candidate does NOT meaningfully beat baseline on 2024 precision@10.")

    print(f"\n=== QB-only precision within each model's own top {TOP_K_FOR_QB_BREAKDOWN} (2024) ===")
    print("(how often a QB the model ranked into its own top 25 actually spiked)")
    for label, (hits, n, p) in qb_results.items():
        p_str = f"{p:.4f}" if p is not None else "n/a (0 QB rows in top 25)"
        print(f"  {label:30} {hits}/{n} = {p_str}")

    print(f"\n=== Per-position precision@10 (2024) -- RB/WR/TE primary, QB secondary ===")
    print("(this is the corrected read: does the group win where the league's decisions actually happen?)")
    print(f"{'pos':4} {'baseline':>18} {'candidate':>18} {'delta':>9}   base rate")
    print("-" * 68)
    for pos in ("RB", "WR", "TE", "QB"):
        b = per_pos_results["baseline (10 features)"][pos]
        c = per_pos_results["candidate (10 + QB-volume)"][pos]
        b_s = f"{b['hits']}/{b['n']}={b['precision']:.4f}" if b["precision"] is not None else "n/a"
        c_s = f"{c['hits']}/{c['n']}={c['precision']:.4f}" if c["precision"] is not None else "n/a"
        d_s = f"{c['precision']-b['precision']:+.4f}" if (b["precision"] is not None and c["precision"] is not None) else "n/a"
        br_s = f"{c['base_rate']:.4f}" if c["base_rate"] is not None else "n/a"
        tag = "  <-- primary" if pos != "QB" else "  (secondary)"
        print(f"{pos:4} {b_s:>18} {c_s:>18} {d_s:>9}   {br_s}{tag}")

    print("\n=== Candidate model feature importances (gain) ===")
    cand_model = models["candidate (10 + QB-volume)"]
    for name, imp in sorted(zip(ALL_COLS, cand_model.feature_importances_), key=lambda x: x[1], reverse=True):
        marker = "  <-- Group 2" if name in CANDIDATE_FEATURES else ""
        print(f"  {name}: {imp:.4f}{marker}")

    print("\nNo model saved, no metadata written -- this is a hypothesis test only. "
          "Group 2 is NOT adopted into production regardless of this outcome.")


if __name__ == "__main__":
    main()
