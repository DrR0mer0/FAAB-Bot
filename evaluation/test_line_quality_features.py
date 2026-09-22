#!/usr/bin/env python3
"""Test Group 7 (line quality) features: trailing_team_sack_rate_allowed,
trailing_team_stuff_rate_allowed, trailing_opp_sack_rate_generated,
trailing_opp_stuff_rate_generated -- sourced from team_week_pbp_stats
(data/load_pbp_aggregates.py). Train baseline (current 13-feature production
set) vs candidate (baseline + these 4) on 2010-2023, compare precision@10 on
2024 -- pooled AND per-position (RB/WR/TE primary, QB secondary) via the
shared evaluation/metrics.py helper, same convention as every prior
feature-group test in this repo.

All four are TEAM-level, not player-level: every player on the same team (or
facing the same upcoming opponent) that week gets the SAME value for the
relevant column -- unlike every other trailing feature tested so far, which
is computed per player. report_shared_value_check() below measures exactly
how much duplication this creates (how many distinct rows share a value
within a single team-week) and the candidate model's own feature importances
say directly whether a tree-based model finds a many-rows-share-one-value
feature useful to split on at all.

NOTE: 2024 is the designated development slice for feature-group testing
(see CLAUDE.md "Methodology rules (standing)") -- already reused by Groups
1-6, not virgin. A win here is a green light to keep developing this group,
not a green light to promote it: promotion requires prospective confirmation
on live 2026 weeks in addition to this result. 2025 is closed. 2023 is held
in reserve for a future second-opinion decision and deliberately NOT used
here.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from sklearn.metrics import average_precision_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "model"))
from features_lib import FeatureEngine, LINE_QUALITY_FEATURES, PRODUCTION_FEATURE_COLS
from metrics import POSITIONS, mean_precision_at_k, per_position_report

BASE_FEATURE_COLS = PRODUCTION_FEATURE_COLS
CANDIDATE_FEATURES = LINE_QUALITY_FEATURES
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

TEAM_LEVEL_COLS = {
    "trailing_team_sack_rate_allowed": "team",
    "trailing_team_stuff_rate_allowed": "team",
    "trailing_opp_sack_rate_generated": "opp",
    "trailing_opp_stuff_rate_generated": "opp",
}


def report_shared_value_check(df):
    """How much duplication the team-level design actually creates: for each
    candidate column, the average number of DISTINCT rows (players) sharing
    the exact same value within a single (season, week, team-or-opp) group.
    A value near the group's typical roster size confirms the feature really
    is one number copied across every teammate/opponent-facer that week, as
    designed -- not a bug, but the thing report_shared_value_check exists to
    make visible before looking at whether the model uses it."""
    print("\n=== Team-level sharing check ===")
    print("(avg number of rows sharing the identical value within one team-week group, non-NULL only)")
    for c, group_key in TEAM_LEVEL_COLS.items():
        sub = df[df[c].notna()]
        group_cols = ["season", "week", group_key]
        sizes = sub.groupby(group_cols)[c].apply(lambda s: s.value_counts().max())
        print(f"  {c} (grouped by season/week/{group_key}): avg {sizes.mean():.2f} rows/value, "
              f"n_groups={len(sizes)}")


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
    # persisted player_week_features table) -- the 4 candidate features
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
            rows.append((season, week, pid, feat["_pos"], feat["_team"], feat["_opp"], label,
                         *[feat[c] for c in ALL_COLS]))

    df = pd.DataFrame(rows, columns=["season", "week", "player_id", "pos", "team", "opp", "spike_flag", *ALL_COLS])
    print(f"[INFO] {n_checked} non-playoff player-weeks checked across seasons {seasons[0]}-{seasons[-1]} "
          f"(excl. 2019), {n_no_label} with no label row, {len(df)} eligible+labeled rows")

    print(f"\nNULL rate per candidate feature over {len(df)} rows (2010-2024):")
    for c in CANDIDATE_FEATURES:
        n_null = df[c].isna().sum()
        print(f"  {c}: {n_null} ({100 * n_null / len(df):.2f}%)")

    report_shared_value_check(df)

    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"] == TEST_SEASON].copy()
    print(f"\nTrain rows: {len(train)} (2010-2023, excl. 2019)")
    print(f"Test rows:  {len(test)} (2024)")
    print(f"Train spike rate: {train['spike_flag'].mean():.4f}")
    print(f"Test spike rate:  {test['spike_flag'].mean():.4f}")

    print("\n*** NOTE: 2024 is the designated (reused) development slice -- see CLAUDE.md. ***")
    print("A positive result here is a green light to keep developing this group, not to promote it.")
    print("Promotion requires prospective confirmation on live 2026 weeks in addition to this result.")

    results = {}
    per_pos_results = {}
    models = {}
    for label, cols in [("baseline (13 features)", BASE_FEATURE_COLS),
                         ("candidate (13 + line-quality)", ALL_COLS)]:
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

    print(f"\n{'model':30} {'P@10 (2024)':>12} {'P@25 (2024)':>12} {'PR-AUC (2024)':>14}")
    print("-" * 72)
    for label, (p10, p25, prauc) in results.items():
        print(f"{label:30} {p10:12.4f} {p25:12.4f} {prauc:14.4f}")

    base_p10 = results["baseline (13 features)"][0]
    cand_p10 = results["candidate (13 + line-quality)"][0]
    delta = cand_p10 - base_p10
    print(f"\nDelta P@10 pooled (candidate - baseline): {delta:+.4f}")
    if delta > 0.005:
        print("Pooled VERDICT: candidate beats baseline on 2024 precision@10.")
    else:
        print("Pooled VERDICT: candidate does NOT meaningfully beat baseline on 2024 precision@10.")

    print(f"\n=== Per-position precision@10 (2024) -- RB/WR/TE primary, QB secondary ===")
    print(f"{'pos':4} {'baseline':>18} {'candidate':>18} {'delta':>9}   base rate")
    print("-" * 68)
    for pos in ("RB", "WR", "TE", "QB"):
        b = per_pos_results["baseline (13 features)"][pos]
        c = per_pos_results["candidate (13 + line-quality)"][pos]
        b_s = f"{b['hits']}/{b['n']}={b['precision']:.4f}" if b["precision"] is not None else "n/a"
        c_s = f"{c['hits']}/{c['n']}={c['precision']:.4f}" if c["precision"] is not None else "n/a"
        d_s = f"{c['precision']-b['precision']:+.4f}" if (b["precision"] is not None and c["precision"] is not None) else "n/a"
        br_s = f"{c['base_rate']:.4f}" if c["base_rate"] is not None else "n/a"
        tag = "  <-- primary" if pos != "QB" else "  (secondary)"
        print(f"{pos:4} {b_s:>18} {c_s:>18} {d_s:>9}   {br_s}{tag}")

    print("\n=== Candidate model feature importances (gain) ===")
    cand_model = models["candidate (13 + line-quality)"]
    imps = sorted(zip(ALL_COLS, cand_model.feature_importances_), key=lambda x: x[1], reverse=True)
    for name, imp in imps:
        marker = "  <-- Group 7 (team-level)" if name in CANDIDATE_FEATURES else ""
        print(f"  {name}: {imp:.4f}{marker}")
    group7_rank = [i for i, (name, _) in enumerate(imps, start=1) if name in CANDIDATE_FEATURES]
    group7_total_gain = sum(imp for name, imp in imps if name in CANDIDATE_FEATURES)
    print(f"\nGroup 7 combined importance: {group7_total_gain:.4f} of 1.0 total gain across all {len(ALL_COLS)} features; "
          f"individual ranks (1=most important): {group7_rank}")
    if group7_total_gain < 0.01:
        print("Read: negligible combined importance -- the model is essentially ignoring these team-level features, "
              "consistent with a shared-value column giving a tree few useful within-week splits.")
    else:
        print("Read: non-trivial combined importance -- the model IS finding some use for a team-level feature, "
              "worth a closer look at which split points it's actually using before drawing conclusions.")

    print("\nNo model saved, no metadata written -- this is a hypothesis test only. "
          "Group 7 is NOT adopted into production regardless of this outcome.")


if __name__ == "__main__":
    main()
