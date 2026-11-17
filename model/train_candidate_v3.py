#!/usr/bin/env python3
"""v3 candidate: fix the starter_absent_proxy leak (as-of or removed) and train on the league's 5-pt passing TD.

Three stages, run in order, each ONCE (decision rules below are fixed before the first run -- see
docs/starter_absent_asof_plan.md; change them in that file and here BEFORE running dev, never after):

  dev    train 2010-2023 (no 2019), evaluate on 2024 -> picks the arm (as-of vs removed)
  test   train 2010-2024, evaluate on 2025            -> the out-of-time record for the picked arm (2025 has been
                                                         read before, by the 10-feature baseline and share delta: not
                                                         virgin, reported as such)
  final  train 2010-2025 on the picked arm            -> odds_xgb_model_candidate_v3.joblib (+ .meta.json), the
                                                         third tracked model; never production by this script

Arms (every arm trains on labels_player_week_league, profile league_v1, so only the feature differs):
  live     production's 13 features, trained as production was (leaky starter_absent_proxy in training) and scored
           with starter_absent_proxy = NULL, which is how production actually runs live -- the honest baseline
  removed  production's features minus starter_absent_proxy (12)
  asof     production's features with starter_absent_proxy -> starter_absent_asof (13)

Population: every eligible, labeled QB/RB/WR/TE player-week of the evaluation season (the live pool's positions).

Pre-registered dev rule: pick `asof` only if its pooled mean precision@10 on 2024 is strictly higher than
`removed`'s AND it is not lower at RB or at WR (precision@10, top-10 per position per week, summed). Otherwise pick
`removed`. TE and QB are reported, not decisive (TE absences are the least knowable pre-game -- 66-75% vs 82-87% at
RB/WR, 2022-2024 check -- and QB scores are unreliable, CLAUDE.md). Both arms fix the leak; the rule only decides
whether the as-of feature earns its column.

Usage (on a COPY of the DB -- see the plan):
  python model/train_candidate_v3.py --db <copy> --stage dev
  python model/train_candidate_v3.py --db <copy> --stage test --arm asof|removed
  python model/train_candidate_v3.py --db <copy> --stage final --arm asof|removed
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score
from xgboost import XGBClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "evaluation"))
from availability import WEEKLY_ROSTER_FIRST_SEASON, Availability  # noqa: E402
from features_lib import CANDIDATE_V3_FEATURE_COLS, CANDIDATE_V3_REMOVED_COLS, PRODUCTION_FEATURE_COLS, norm_team  # noqa: E402
from metrics import mean_precision_at_k, precision_at_k  # noqa: E402
from model_metadata import write_metadata  # noqa: E402

LABEL_TABLE = "labels_player_week_league"
LABEL_PROFILE = "league_v1"
SCALE_POS_WEIGHT = 20.13  # unchanged from production (tune_and_finalize_xgboost.py); not re-tuned for v3
MODEL_PARAMS = dict(objective="binary:logistic", eval_metric="aucpr", n_estimators=200, max_depth=4,
                    learning_rate=0.1, importance_type="gain", random_state=42)
ROSTERABLE = ("QB", "RB", "WR", "TE")
STAGES = {
    "dev": ([s for s in range(2010, 2024) if s != 2019], 2024),
    "test": ([s for s in range(2010, 2025) if s != 2019], 2025),
    "final": ([s for s in range(2010, 2026) if s != 2019], None),
}
ARMS = {
    "live": PRODUCTION_FEATURE_COLS,
    "removed": CANDIDATE_V3_REMOVED_COLS,
    "asof": CANDIDATE_V3_FEATURE_COLS,
}
# Two data guards, both over RB/WR/TE rows and both only for seasons >= availability.WEEKLY_ROSTER_FIRST_SEASON (2016;
# before it the feature is NULL by design -- season-level roster files, see availability.py):
MAX_ASOF_NULL_RATE = 0.20       # a season above this almost certainly has a missing nflverse file
# Every training row is a player-week in which the player PLAYED (it has a stat row). If the availability source calls
# more than this share of them absent, it is reading something other than pre-game availability -- the check that
# found the 2010-2015 problem (21-26% there, 0.0-0.2% in 2016-2025; plan section 3, correction of 2026-11-17).
MAX_PLAYED_ABSENT_RATE = 0.01
GUARD_POSITIONS = ("RB", "WR", "TE")
MODEL_PATH = REPO_ROOT / "odds_xgb_model_candidate_v3.joblib"


def load(con, seasons):
    cols = sorted(set(PRODUCTION_FEATURE_COLS) | set(CANDIDATE_V3_FEATURE_COLS))
    ph = ",".join("?" * len(seasons))
    df = pd.read_sql_query(
        f"""SELECT f.season, f.week, f.player_id, s.pos, s.team, {", ".join("f." + c for c in cols)}, l.spike_flag
            FROM player_week_features f
            JOIN {LABEL_TABLE} l ON f.season=l.season AND f.week=l.week AND f.player_id=l.player_id
            JOIN player_week_stats s ON f.season=s.season AND f.week=s.week AND f.player_id=s.player_id
            WHERE f.season IN ({ph}) AND l.spike_flag IS NOT NULL""",
        con, params=seasons)
    return df


def data_guards(df, availability):
    """Per season, over RB/WR/TE rows: the starter_absent_asof NULL rate, and the share of player-weeks the availability
    source calls absent although the player played (every row here has a stat row). {season: {...}}; printed, and
    stored with the stage's record."""
    g = df[df["pos"].isin(GUARD_POSITIONS)]
    out = {}
    for season, d in g.groupby("season"):
        flags = [availability.absent(int(r.season), int(r.week), r.player_id, norm_team(r.team))
                 for r in d[["season", "week", "player_id", "team"]].itertuples(index=False)]
        n = len(flags)
        out[int(season)] = {"n": n, "asof_null_rate": float(d["starter_absent_asof"].isna().mean()),
                            "played_absent_rate": (sum(1 for f in flags if f is True) / n) if n else 0.0,
                            "availability_unknown_rate": (sum(1 for f in flags if f is None) / n) if n else 0.0,
                            "guarded": int(season) >= WEEKLY_ROSTER_FIRST_SEASON}
    print(f"\nData guards (RB/WR/TE; enforced from {WEEKLY_ROSTER_FIRST_SEASON}: NULL <= {MAX_ASOF_NULL_RATE:.0%}, "
          f"played-but-absent <= {MAX_PLAYED_ABSENT_RATE:.0%}):")
    print(f"  {'season':6} {'rows':>7} {'asof NULL':>10} {'played but absent':>18} {'availability unknown':>21}")
    for season, r in out.items():
        print(f"  {season:<6} {r['n']:7} {r['asof_null_rate']:10.1%} {r['played_absent_rate']:18.2%} "
              f"{r['availability_unknown_rate']:21.1%}" + ("" if r["guarded"] else "   (before the cutoff: NULL by design)"))
    return out


def check_guards(guards):
    """Stop (SystemExit, nothing written) if any season from the cutoff on breaks either guard."""
    null_bad = [s for s, r in guards.items() if r["guarded"] and r["asof_null_rate"] > MAX_ASOF_NULL_RATE]
    if null_bad:
        raise SystemExit(f"[ERROR] starter_absent_asof is NULL for more than {MAX_ASOF_NULL_RATE:.0%} of RB/WR/TE rows in "
                         f"{null_bad} -- regenerate player_week_features with --availability-raw-dir "
                         f"(and --download-missing) first")
    absent_bad = {s: r["played_absent_rate"] for s, r in guards.items()
                  if r["guarded"] and r["played_absent_rate"] > MAX_PLAYED_ABSENT_RATE}
    if absent_bad:
        raise SystemExit(f"[ERROR] the availability source calls more than {MAX_PLAYED_ABSENT_RATE:.0%} of RB/WR/TE "
                         f"player-weeks absent although the player played: "
                         + ", ".join(f"{s} {v:.2%}" for s, v in absent_bad.items())
                         + " -- it is not reading pre-game availability there; stop and investigate")


def fit(train, cols):
    m = XGBClassifier(scale_pos_weight=SCALE_POS_WEIGHT, **MODEL_PARAMS)
    m.fit(train[cols], train["spike_flag"])
    return m


def evaluate(name, model, cols, test, null_cols=()):
    X = test[cols].copy()
    for c in null_cols:
        X[c] = np.nan
    t = test[test["pos"].isin(ROSTERABLE)].copy()
    t["proba"] = model.predict_proba(X.loc[t.index])[:, 1]
    p10 = mean_precision_at_k(t, 10)
    p25 = mean_precision_at_k(t, 25)
    per_pos = precision_at_k(t, 10, group_col="pos")
    top25 = t.sort_values(["season", "week", "proba"], ascending=[True, True, False]).groupby(["season", "week"]).head(25)
    res = {
        "arm": name, "features": cols, "null_at_score_time": list(null_cols),
        "pooled_mean_p10": p10, "pooled_mean_p25": p25,
        "p10_by_pos": {k: v[2] for k, v in per_pos.items()},
        "p10_hits_by_pos": {k: [v[0], v[1]] for k, v in per_pos.items()},
        "pr_auc": float(average_precision_score(t["spike_flag"], t["proba"])),
        "calibration": {"top25_mean_score": float(top25["proba"].mean()), "top25_realized": float(top25["spike_flag"].mean()),
                        "pool_mean_score": float(t["proba"].mean()), "pool_realized": float(t["spike_flag"].mean())},
        "importances": {c: float(v) for c, v in sorted(zip(cols, model.feature_importances_), key=lambda x: -x[1])},
    }
    pp = "  ".join(f"{k} {v:.3f}" for k, v in res["p10_by_pos"].items() if v is not None)
    print(f"\n[{name}] pooled P@10 {p10:.4f}  P@25 {p25:.4f}  PR-AUC {res['pr_auc']:.4f}\n  P@10 by pos: {pp}\n"
          f"  calibration: top-25 mean score {res['calibration']['top25_mean_score']:.3f} vs realized "
          f"{res['calibration']['top25_realized']:.3f}; pool {res['calibration']['pool_mean_score']:.3f} vs "
          f"{res['calibration']['pool_realized']:.3f}")
    return res


def dev_decision(removed, asof):
    beats_pooled = asof["pooled_mean_p10"] > removed["pooled_mean_p10"]
    holds = {p: (asof["p10_by_pos"].get(p) or 0) >= (removed["p10_by_pos"].get(p) or 0) for p in ("RB", "WR")}
    pick = "asof" if beats_pooled and all(holds.values()) else "removed"
    print(f"\n[DECISION] asof beats removed on pooled P@10: {beats_pooled}; not lower at RB: {holds['RB']}, WR: "
          f"{holds['WR']} -> pick '{pick}' (pre-registered rule)")
    return pick


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="a COPY of faab_history_core_v0_1.db, prepared per the plan")
    ap.add_argument("--stage", choices=STAGES, required=True)
    ap.add_argument("--arm", choices=("asof", "removed"), help="required for test/final: the arm dev picked")
    ap.add_argument("--out", default=str(REPO_ROOT / "evaluation" / "results"), help="where the stage's JSON goes")
    ap.add_argument("--availability-raw-dir", default=str(REPO_ROOT / "nflverse_raw"),
                    help="the nflverse injuries/roster_weekly files the features were built from (for the data guards)")
    args = ap.parse_args()
    if args.stage != "dev" and not args.arm:
        ap.error("--arm is required for --stage test/final (use the arm dev picked)")

    train_seasons, test_season = STAGES[args.stage]
    record = Path(args.out) / f"candidate_v3_{args.stage}.json"
    if args.stage != "final" and record.exists():
        raise SystemExit(f"[ERROR] {record} already exists -- each stage runs once; it is the record")
    con = sqlite3.connect(args.db)
    df = load(con, train_seasons + ([test_season] if test_season else []))
    con.close()
    seasons = sorted(int(x) for x in df["season"].unique())
    availability = Availability.from_nflverse(args.availability_raw_dir, seasons, norm_team=norm_team)
    guards = data_guards(df, availability)
    check_guards(guards)
    train = df[df["season"].isin(train_seasons)]
    print(f"[INFO] stage {args.stage}: train {train_seasons[0]}-{train_seasons[-1]} ({len(train)} rows, spike rate "
          f"{train['spike_flag'].mean():.4f}), labels {LABEL_TABLE} ({LABEL_PROFILE})")

    if args.stage == "final":
        cols = ARMS[args.arm]
        model = fit(train, cols)
        joblib.dump({"model": model, "feature_cols": cols, "scale_pos_weight": SCALE_POS_WEIGHT,
                     "train_seasons": train_seasons, "model_params": MODEL_PARAMS, "label_profile": LABEL_PROFILE},
                    str(MODEL_PATH))
        meta_path, meta = write_metadata(
            str(MODEL_PATH), REPO_ROOT, cols, MODEL_PARAMS, SCALE_POS_WEIGHT, train_seasons,
            extra={"role": "candidate", "label_profile": LABEL_PROFILE, "label_table": LABEL_TABLE, "arm": args.arm,
                   "data_guards": {str(k): v for k, v in guards.items()},
                   "description": "v3 candidate: starter_absent_proxy leak fixed (" + args.arm + "), labels on the "
                                  "league's 5-pt passing TD. Third tracked model (start week: docs/starter_absent_asof_plan.md, section 4); not production."})
        print(f"[SAVED] {MODEL_PATH} and {meta_path} (git_commit={meta['git_commit']})")
        print("  importances: " + ", ".join(f"{c} {v:.3f}" for c, v in sorted(zip(cols, model.feature_importances_), key=lambda x: -x[1])))
        return

    test = df[df["season"] == test_season]
    print(f"[INFO] evaluate on {test_season}: {len(test[test['pos'].isin(ROSTERABLE)])} QB/RB/WR/TE rows, spike rate "
          f"{test[test['pos'].isin(ROSTERABLE)]['spike_flag'].mean():.4f}")
    arms = ("live", "removed", "asof") if args.stage == "dev" else ("live", args.arm)
    results = {}
    for a in arms:
        model = fit(train, ARMS[a])
        results[a] = evaluate(a, model, ARMS[a], test, null_cols=("starter_absent_proxy",) if a == "live" else ())
    out = {"stage": args.stage, "train_seasons": train_seasons, "test_season": test_season, "label_profile": LABEL_PROFILE,
           "run_at": datetime.now(timezone.utc).isoformat(), "data_guards": {str(k): v for k, v in guards.items()},
           "results": results}
    if args.stage == "dev":
        out["pick"] = dev_decision(results["removed"], results["asof"])
    Path(args.out).mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n[SAVED] {record}")


if __name__ == "__main__":
    main()
