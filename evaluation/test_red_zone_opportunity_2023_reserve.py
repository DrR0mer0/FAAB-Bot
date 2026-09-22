#!/usr/bin/env python3
"""Group 8 (red-zone opportunity) confirmatory test on the 2023 RESERVE
slice -- the second-opinion slice CLAUDE.md held back for exactly one future
decision (see "Evaluation slices (standing designation)" in the Methodology
rules). This spends it. 2023 is not fully virgin -- it was the validation
slice for the share_delta_vs_prior_season test earlier in this project
(won there, then lost on the 2025 held-out test) -- but it has never been
used for a Group 1-8 style feature-group test until now.

PRE-REGISTERED DECISION RULE (fixed before this script was run; do not
adjust after seeing the result):

    Train baseline (13 production features) vs baseline+Group 8 on
    2010-2022, evaluate on 2023.

    Group 8 PASSES if:
      (a) sum(RB delta, WR delta, TE delta) > 0, AND
      (b) no single one of RB/WR/TE loses more than 0.02 (i.e. no delta
          among those three is < -0.02)

    QB is secondary and does not affect the decision. Pooled precision@10
    is reported for continuity only and does not affect the decision.

    Any other outcome (sum <= 0, or any of RB/WR/TE breaches -0.02) is a
    REJECTION. If it fails, Group 8 is recorded as rejected here and the
    features stay out of PERSISTED_FEATURE_COLS/PRODUCTION_FEATURE_COLS
    (where they already are, from the original 2024-slice test).

check_decision_rule() below applies this mechanically to whatever numbers
come out of the run -- the pass/fail is computed, not asserted, and this
file is not edited after seeing the result.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import average_precision_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "model"))
from features_lib import FeatureEngine, PRODUCTION_FEATURE_COLS, RED_ZONE_OPPORTUNITY_FEATURES
from metrics import POSITIONS, mean_precision_at_k, per_position_report

BASE_FEATURE_COLS = PRODUCTION_FEATURE_COLS
CANDIDATE_FEATURES = RED_ZONE_OPPORTUNITY_FEATURES
ALL_COLS = BASE_FEATURE_COLS + CANDIDATE_FEATURES

TRAIN_SEASONS = [s for s in range(2010, 2023) if s != 2019]  # 2010-2022, no 2019
TEST_SEASON = 2023
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

# The 2024-slice result, for the "does RB differ in character" comparison
# this test is asked to report -- copied from the already-committed record
# (evaluation/test_red_zone_opportunity_features.py's commit message and
# CLAUDE.md), not recomputed here.
RESULT_2024 = {"RB": -0.0222, "WR": +0.0056, "TE": +0.0222}

BREACH_THRESHOLD = -0.02


def check_decision_rule(deltas):
    """deltas: {"RB": float, "WR": float, "TE": float}. Returns (passed,
    total, breaches) applying the pre-registered rule above, mechanically."""
    total = sum(deltas.values())
    breaches = {pos: d for pos, d in deltas.items() if d < BREACH_THRESHOLD}
    passed = (total > 0) and (len(breaches) == 0)
    return passed, total, breaches


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

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"] == TEST_SEASON].copy()
    print(f"\nTrain rows: {len(train)} (2010-2022, excl. 2019)")
    print(f"Test rows:  {len(test)} (2023 -- the reserve slice, being spent now)")
    print(f"Train spike rate: {train['spike_flag'].mean():.4f}")
    print(f"Test spike rate:  {test['spike_flag'].mean():.4f}")

    print("\n*** This is the 2023 RESERVE slice -- CLAUDE.md's one second-opinion decision. Being spent now. ***")
    print("*** Decision rule was pre-registered above BEFORE this script ran and is not adjustable after the fact. ***")

    results = {}
    per_pos_results = {}
    models = {}
    for label, cols in [("baseline (13 features)", BASE_FEATURE_COLS),
                         ("candidate (13 + red-zone)", ALL_COLS)]:
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

    print(f"\n{'model':30} {'P@10 (2023)':>12} {'P@25 (2023)':>12} {'PR-AUC (2023)':>14}")
    print("-" * 72)
    for label, (p10, p25, prauc) in results.items():
        print(f"{label:30} {p10:12.4f} {p25:12.4f} {prauc:14.4f}")

    base_p10 = results["baseline (13 features)"][0]
    cand_p10 = results["candidate (13 + red-zone)"][0]
    pooled_delta = cand_p10 - base_p10
    print(f"\nPooled P@10 delta (candidate - baseline): {pooled_delta:+.4f} -- reported for continuity ONLY, "
          f"does not affect the decision.")

    print(f"\n=== Per-position precision@10 (2023) -- RB/WR/TE primary (decision), QB secondary (not decisive) ===")
    print(f"{'pos':4} {'baseline':>18} {'candidate':>18} {'delta':>9}   base rate")
    print("-" * 68)
    deltas = {}
    for pos in ("RB", "WR", "TE", "QB"):
        b = per_pos_results["baseline (13 features)"][pos]
        c = per_pos_results["candidate (13 + red-zone)"][pos]
        delta = c["precision"] - b["precision"]
        if pos != "QB":
            deltas[pos] = delta
        b_s = f"{b['hits']}/{b['n']}={b['precision']:.4f}" if b["precision"] is not None else "n/a"
        c_s = f"{c['hits']}/{c['n']}={c['precision']:.4f}" if c["precision"] is not None else "n/a"
        d_s = f"{delta:+.4f}" if (b["precision"] is not None and c["precision"] is not None) else "n/a"
        br_s = f"{c['base_rate']:.4f}" if c["base_rate"] is not None else "n/a"
        tag = "  <-- decision" if pos != "QB" else "  (secondary, not decisive)"
        print(f"{pos:4} {b_s:>18} {c_s:>18} {d_s:>9}   {br_s}{tag}")

    print(f"\n=== Pre-registered decision-rule arithmetic (checkable, not asserted) ===")
    passed, total, breaches = check_decision_rule(deltas)
    print(f"  RB delta: {deltas['RB']:+.4f}")
    print(f"  WR delta: {deltas['WR']:+.4f}")
    print(f"  TE delta: {deltas['TE']:+.4f}")
    print(f"  Sum:      {total:+.4f}  ({'> 0, condition (a) MET' if total > 0 else '<= 0, condition (a) FAILED'})")
    if breaches:
        print(f"  Breach of -0.02 threshold (condition b): {', '.join(f'{p}={d:+.4f}' for p, d in breaches.items())} -- condition (b) FAILED")
    else:
        print(f"  No position lost more than 0.02 -- condition (b) MET")
    print(f"\n  RESULT: Group 8 {'PASSES' if passed else 'FAILS'} the pre-registered rule on the 2023 reserve slice.")
    print(f"  {'Adopting' if passed else 'Rejecting'} per the rule fixed before this run. Not adjusted after seeing the result.")

    print(f"\n=== RB character check: does 2023 differ from the original 2024-slice run? ===")
    print(f"  2024 RB delta (already-committed record, evaluation/test_red_zone_opportunity_features.py): {RESULT_2024['RB']:+.4f}")
    print(f"  2023 RB delta (this run):                                                                   {deltas['RB']:+.4f}")
    print(f"  2024 has the LOWEST RB spike rate of 15 full seasons on record (evaluation/label_distribution_by_season.py);")
    print(f"  2023's RB spike rate is unremarkable by that same check (near the historical range).")
    if (deltas['RB'] < 0) == (RESULT_2024['RB'] < 0):
        print(f"  Same sign as 2024 ({'both negative' if deltas['RB'] < 0 else 'both positive'}) -- RB result does NOT "
              f"look specific to 2024's unusual label distribution; it recurs on a normal-RB-spike-rate season too.")
    else:
        print(f"  Opposite sign from 2024 -- RB result DOES look specific to 2024's unusual label distribution; "
              f"it does not recur on a normal-RB-spike-rate season.")

    print("\n=== Candidate model feature importances (gain) ===")
    cand_model = models["candidate (13 + red-zone)"]
    for name, imp in sorted(zip(ALL_COLS, cand_model.feature_importances_), key=lambda x: x[1], reverse=True):
        marker = "  <-- Group 8" if name in CANDIDATE_FEATURES else ""
        print(f"  {name}: {imp:.4f}{marker}")

    print(f"\nThe 2023 reserve slice is now SPENT (per CLAUDE.md's standing rule: one future decision, once).")
    print("No model saved, no metadata written, and PERSISTED_FEATURE_COLS/PRODUCTION_FEATURE_COLS are NOT")
    print("touched by this script regardless of outcome -- a pass here is evidence for a human decision on")
    print("whether to pursue promotion (which would still need prospective confirmation on live 2026 weeks per")
    print("the standing evaluation-slice methodology), not an automatic promotion.")


if __name__ == "__main__":
    main()
