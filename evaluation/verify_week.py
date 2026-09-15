#!/usr/bin/env python3
"""Score a committed prediction file against what actually happened.

Given a completed (season, week), loads the committed prediction record
from predictions/<season>_week<NN>.json -- never regenerated, since
score_week.py run again later would see different roster/DB state -- and
scores it against labels_player_week AS THE PIPELINE COMPUTED IT
(fantasy_points_half, spike_flag). Hit/miss is always read directly from
spike_flag, never recomputed here, since a spike is defined relative to
each player's own trailing baseline and generate_labels_and_breakouts.py
is the single source of truth for that. The trailing baseline and spike
threshold shown in the report ARE recomputed here (they aren't persisted
anywhere), mirroring generate_labels_and_breakouts.py's own gap-aware
windowing logic exactly, for display purposes only.

Fails fast if the labels pipeline hasn't been run for this week yet --
verification against raw points would silently ignore each player's own
baseline, which is exactly the thing a "spike" is defined relative to.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# Windows' default console codepage (cp1252) can't encode the checkmark/cross
# status marks printed below; without this, the script dies partway through
# the per-player table -- before the log and markdown report are written.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
PREDICTIONS_DIR = REPO_ROOT / "predictions"

# Must match generate_labels_and_breakouts.py exactly -- these are the rules
# a "spike" was actually labeled under, reproduced here only to show the
# threshold each player needed to clear, not to redetermine hit/miss.
SPIKE_MULTIPLIER = 1.5
SPIKE_MIN_POINTS = 10.0
MIN_PRIOR_GAMES = 3


def gap_after_for(loaded_seasons, target_season):
    combined = sorted(set(loaded_seasons) | {target_season})
    return {combined[i - 1] for i in range(1, len(combined)) if combined[i] - combined[i - 1] > 1}


def crosses_gap(prev_season, cur_season, gap_after):
    return any(prev_season <= g < cur_season for g in gap_after)


def trailing_baseline(con, player_id, season, week, loaded_seasons):
    """Recomputes the trailing-3-game fantasy_points_half baseline and the
    spike threshold it implies, for display only. Uses the same
    structural season-gap reset as the labels pipeline (never bridges a
    genuinely missing season)."""
    rows = con.execute(
        """SELECT season, week, fantasy_points_half FROM labels_player_week
           WHERE player_id=? AND (season < ? OR (season = ? AND week < ?))
           ORDER BY season, week""",
        (player_id, season, season, week),
    ).fetchall()

    gap_after = gap_after_for(loaded_seasons, season)
    window = []
    for (s, w, pts) in rows:
        if window and crosses_gap(window[-1][0], s, gap_after):
            window = []
        window.append((s, w, pts))
    if window and crosses_gap(window[-1][0], season, gap_after):
        window = []

    if len(window) < MIN_PRIOR_GAMES:
        return None, None

    last3 = window[-MIN_PRIOR_GAMES:]
    baseline = sum(p for (_, _, p) in last3) / MIN_PRIOR_GAMES
    threshold = max(SPIKE_MULTIPLIER * baseline, SPIKE_MIN_POINTS)
    return baseline, threshold


def precision_at(rows_out, k):
    subset = [r for r in rows_out if r["rank"] <= k]
    hits = sum(1 for r in subset if r["hit"])
    n = len(subset)
    return hits, n, (hits / n if n else None)


def load_or_init_log(log_path):
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            return json.load(f)
    return {"weeks": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--predictions-dir", default=str(PREDICTIONS_DIR))
    ap.add_argument("--log", default=str(PREDICTIONS_DIR / "verification_log.json"))
    args = ap.parse_args()

    pred_path = Path(args.predictions_dir) / f"{args.season}_week{args.week:02d}.json"
    if not pred_path.exists():
        raise SystemExit(
            f"[ERROR] No committed prediction file at {pred_path}. "
            f"Nothing to verify -- run model/score_week.py --json-out for this week first, and commit it."
        )
    with open(pred_path, encoding="utf-8") as f:
        predictions = json.load(f)

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    label_count = con.execute(
        "SELECT COUNT(*) FROM labels_player_week WHERE season=? AND week=?",
        (args.season, args.week),
    ).fetchone()[0]
    if label_count == 0:
        raise SystemExit(
            f"[ERROR] No labels_player_week rows for {args.season} week {args.week} -- "
            f"the labels pipeline hasn't been run for this week yet. Verification must score "
            f"against labels as the pipeline computes them (each player's own trailing baseline), "
            f"not raw fantasy points recomputed here. Run, in order, for all loaded seasons "
            f"including {args.season}:\n"
            f"  python data/load_nflverse_into_history_SAFE_v3.py --seasons {args.season}\n"
            f"  python model/generate_labels_and_breakouts.py --seasons <all loaded seasons incl. {args.season}>\n"
            f"then rerun this script."
        )

    loaded_seasons = [r[0] for r in con.execute("SELECT DISTINCT season FROM labels_player_week ORDER BY season")]

    base_row = con.execute(
        """SELECT COUNT(*), SUM(CASE WHEN spike_flag=1 THEN 1 ELSE 0 END)
           FROM labels_player_week WHERE season=? AND week=? AND spike_flag IS NOT NULL""",
        (args.season, args.week),
    ).fetchone()
    n_eligible_week, n_spiked_week = base_row
    base_rate = (n_spiked_week / n_eligible_week) if n_eligible_week else None

    rows_out = []
    for entry in predictions["top"]:
        pid = entry["player_id"]
        label_row = con.execute(
            "SELECT fantasy_points_half, spike_flag FROM labels_player_week WHERE player_id=? AND season=? AND week=?",
            (pid, args.season, args.week),
        ).fetchone()
        baseline, threshold = trailing_baseline(con, pid, args.season, args.week, loaded_seasons)

        if label_row is None:
            status, actual_pts, hit = "DNP", None, False
        else:
            actual_pts, spike_flag = label_row
            if spike_flag is None:
                status, hit = "NO_LABEL", False
            elif spike_flag == 1:
                status, hit = "HIT", True
            else:
                status, hit = "MISS", False

        rows_out.append({
            "rank": entry["rank"], "player_id": pid, "name": entry["name"],
            "pos": entry["pos"], "team": entry["team"], "predicted_score": entry["score"],
            "actual_fantasy_points_half": actual_pts, "trailing_baseline": baseline,
            "spike_threshold": threshold, "status": status, "hit": hit,
        })

    hits10, n10, p10 = precision_at(rows_out, 10)
    hits25, n25, p25 = precision_at(rows_out, 25)
    n_dnp = sum(1 for r in rows_out if r["status"] == "DNP")
    n_no_label = sum(1 for r in rows_out if r["status"] == "NO_LABEL")

    con.close()

    # ---- console report ----
    print(f"[INFO] loaded {pred_path}")
    print(f"[INFO] model: {predictions.get('model', {}).get('filename')} "
          f"(git_commit={predictions.get('model', {}).get('git_commit')})")
    print(f"\n=== Verification: {args.season} week {args.week} ===")
    print(f"Week base rate: {n_spiked_week}/{n_eligible_week} eligible players spiked "
          f"({base_rate:.4f})" if base_rate is not None else "Week base rate: unavailable")
    print(f"Precision@10: {hits10}/{n10} = {p10:.4f}" + (f"  ({p10/base_rate:.1f}x base rate)" if base_rate else ""))
    print(f"Precision@25: {hits25}/{n25} = {p25:.4f}" + (f"  ({p25/base_rate:.1f}x base rate)" if base_rate else ""))
    print(f"Of the top 25: {sum(1 for r in rows_out if r['status']=='HIT')} actually spiked")
    print(f"DNP (didn't play): {n_dnp}; no label produced (ineligible despite playing): {n_no_label} "
          f"-- both counted as non-hits in precision@k above, not silently dropped from the denominator")

    print(f"\n{'Rk':3} {'Name':22} {'Pos':4} {'Team':5} {'Pred':>6} {'Actual':>7} {'Baseline':>9} {'Threshold':>10} {'Status'}")
    print("-" * 90)
    for r in rows_out:
        actual_s = f"{r['actual_fantasy_points_half']:.1f}" if r["actual_fantasy_points_half"] is not None else "  --"
        base_s = f"{r['trailing_baseline']:.2f}" if r["trailing_baseline"] is not None else "  N/A"
        thr_s = f"{r['spike_threshold']:.2f}" if r["spike_threshold"] is not None else "  N/A"
        mark = {"HIT": "✓", "MISS": "✗", "DNP": "DNP", "NO_LABEL": "N/A"}[r["status"]]
        print(f"{r['rank']:3d} {r['name']:22.22} {r['pos']:4} {r['team']:5} {r['predicted_score']:6.4f} "
              f"{actual_s:>7} {base_s:>9} {thr_s:>10} {mark}")

    # ---- cumulative log ----
    log_path = Path(args.log)
    log = load_or_init_log(log_path)
    log["weeks"] = [w for w in log["weeks"] if not (w["season"] == args.season and w["week"] == args.week)]
    log["weeks"].append({
        "season": args.season, "week": args.week,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "prediction_file": str(pred_path.relative_to(REPO_ROOT)),
        "model": predictions.get("model"),
        "precision_at_10": {"hits": hits10, "n": n10, "precision": p10},
        "precision_at_25": {"hits": hits25, "n": n25, "precision": p25},
        "base_rate": base_rate,
        "n_dnp": n_dnp, "n_no_label": n_no_label,
    })
    log["weeks"].sort(key=lambda w: (w["season"], w["week"]))
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)
    print(f"\n[SAVED] appended to {log_path}")

    cum_hits10 = sum(w["precision_at_10"]["hits"] for w in log["weeks"])
    cum_n10 = sum(w["precision_at_10"]["n"] for w in log["weeks"])
    cum_hits25 = sum(w["precision_at_25"]["hits"] for w in log["weeks"])
    cum_n25 = sum(w["precision_at_25"]["n"] for w in log["weeks"])
    cum_p10 = cum_hits10 / cum_n10 if cum_n10 else None
    cum_p25 = cum_hits25 / cum_n25 if cum_n25 else None
    print(f"\n=== Cumulative across {len(log['weeks'])} verified week(s) ===")
    print(f"Cumulative precision@10: {cum_hits10}/{cum_n10}" + (f" = {cum_p10:.4f}" if cum_p10 is not None else ""))
    print(f"Cumulative precision@25: {cum_hits25}/{cum_n25}" + (f" = {cum_p25:.4f}" if cum_p25 is not None else ""))
    print("(a single week is far too noisy to read on its own -- watch this cumulative figure over time)")

    # ---- markdown report ----
    md_path = Path(args.predictions_dir) / f"{args.season}_week{args.week:02d}_verified.md"
    lines = [
        f"# Verification: {args.season} week {args.week}",
        "",
        f"Model: `{predictions.get('model', {}).get('filename')}` "
        f"(commit `{predictions.get('model', {}).get('git_commit')}`)",
        "",
        f"- Week base rate: {n_spiked_week}/{n_eligible_week} eligible players spiked"
        + (f" ({base_rate:.4f})" if base_rate is not None else ""),
        f"- Precision@10: {hits10}/{n10} = {p10:.4f}" + (f" ({p10/base_rate:.1f}x base rate)" if base_rate else ""),
        f"- Precision@25: {hits25}/{n25} = {p25:.4f}" + (f" ({p25/base_rate:.1f}x base rate)" if base_rate else ""),
        f"- Of the top 25: {sum(1 for r in rows_out if r['status']=='HIT')} actually spiked",
        f"- DNP: {n_dnp}, no label produced: {n_no_label} -- both counted as non-hits above, not dropped from the denominator",
        f"- Cumulative across {len(log['weeks'])} verified week(s): "
        f"P@10 {cum_hits10}/{cum_n10}" + (f" = {cum_p10:.4f}" if cum_p10 is not None else "")
        + f", P@25 {cum_hits25}/{cum_n25}" + (f" = {cum_p25:.4f}" if cum_p25 is not None else ""),
        "",
        "| Rank | Name | Pos | Team | Predicted | Actual | Baseline | Threshold | Status |",
        "|---:|---|---|---|---:|---:|---:|---:|:---:|",
    ]
    for r in rows_out:
        actual_s = f"{r['actual_fantasy_points_half']:.1f}" if r["actual_fantasy_points_half"] is not None else "--"
        base_s = f"{r['trailing_baseline']:.2f}" if r["trailing_baseline"] is not None else "N/A"
        thr_s = f"{r['spike_threshold']:.2f}" if r["spike_threshold"] is not None else "N/A"
        mark = {"HIT": "✓", "MISS": "✗", "DNP": "DNP", "NO_LABEL": "N/A"}[r["status"]]
        lines.append(
            f"| {r['rank']} | {r['name']} | {r['pos']} | {r['team']} | {r['predicted_score']:.4f} | "
            f"{actual_s} | {base_s} | {thr_s} | {mark} |"
        )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[SAVED] markdown report written to {md_path}")


if __name__ == "__main__":
    main()
