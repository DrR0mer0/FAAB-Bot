#!/usr/bin/env python3
"""Descriptive check on labels_player_week's spike distribution across
seasons -- DIAGNOSTIC ONLY. No model training, no evaluation, no feature
work. Reads labels_player_week, which is derived from training data
already used elsewhere in this pipeline, so this spends no held-out slice.

Question: is 2024 unusual in a way that could explain why several
feature-group tests (Groups 2, 3, 7 -- see CLAUDE.md's rejected-candidate
record) showed WR gains alongside RB losses specifically on that slice?

For every loaded season, reports, broken out by position (QB/RB/WR/TE):
  1. Spike rate -- share of eligible player-weeks with spike_flag=1.
  2. Share of all spikes -- of every spike that season, what fraction
     came from each position.
  3. Eligible player-weeks -- the denominators, so a rate shift can be
     told apart from a pool-size shift.
  4. Spike magnitude -- median and 90th-percentile fantasy_points_half
     among spike rows only.

"Eligible" here is exactly labels_player_week's own definition (spike_flag
IS NOT NULL, i.e. >=3 prior games per generate_labels_and_breakouts.py) --
not recomputed via FeatureEngine, since this is a read of already-persisted
labels, not a feature computation.

2026 is partial (only the weeks scored so far) and is reported in every
table but EXCLUDED from the "where does 2024 sit" ranking against other
seasons, since comparing a partial season to full ones isn't meaningful.
2019 is excluded everywhere in this pipeline (a real schema gap in the
stats releases) and is skipped here too, consistent with every other
script.

Outlier flagging: for each (measure, position) series across the FULL
seasons only, uses Tukey's rule (outside Q1-1.5*IQR / Q3+1.5*IQR) for
"outlier", min/max for "at the extreme (not a statistical outlier)", and
otherwise reports 2024's rank out of N. Purely descriptive -- this script
does not interpret WHY a number is where it is.
"""
import bisect
import sqlite3
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ("QB", "RB", "WR", "TE")
ALL_SEASONS = [s for s in range(2010, 2027) if s != 2019]
PARTIAL_SEASONS = {2026}
FULL_SEASONS = [s for s in ALL_SEASONS if s not in PARTIAL_SEASONS]


def load_rows(con):
    rows = con.execute(
        """SELECT l.season, l.week, l.player_id, l.spike_flag, l.fantasy_points_half, p.pos
           FROM labels_player_week l
           JOIN player_week_stats p
             ON p.season = l.season AND p.week = l.week AND p.player_id = l.player_id
           WHERE l.spike_flag IS NOT NULL AND p.pos IN ('QB','RB','WR','TE')"""
    ).fetchall()
    return pd.DataFrame(rows, columns=["season", "week", "player_id", "spike_flag", "fantasy_points_half", "pos"])


def build_summary(df):
    records = []
    for season in ALL_SEASONS:
        sdf = df[df["season"] == season]
        total_spikes = int(sdf["spike_flag"].sum())
        for pos in POSITIONS:
            pdf = sdf[sdf["pos"] == pos]
            n_eligible = len(pdf)
            n_spikes = int(pdf["spike_flag"].sum())
            spike_pts = pdf.loc[pdf["spike_flag"] == 1, "fantasy_points_half"]
            records.append({
                "season": season,
                "pos": pos,
                "n_eligible": n_eligible,
                "n_spikes": n_spikes,
                "spike_rate": (n_spikes / n_eligible) if n_eligible else None,
                "share_of_spikes": (n_spikes / total_spikes) if total_spikes else None,
                "median_spike_pts": spike_pts.median() if len(spike_pts) else None,
                "p90_spike_pts": spike_pts.quantile(0.9) if len(spike_pts) else None,
            })
    return pd.DataFrame(records)


def print_table(summary, value_col, title, fmt):
    print(f"\n=== {title} ===")
    pivot = summary.pivot(index="season", columns="pos", values=value_col)[list(POSITIONS)]
    header = f"{'season':8}" + "".join(f"{p:>10}" for p in POSITIONS)
    print(header)
    for season in ALL_SEASONS:
        flag = "  (partial)" if season in PARTIAL_SEASONS else ""
        vals = "".join(f"{fmt(pivot.loc[season, p]):>10}" for p in POSITIONS)
        print(f"{season:<8}{vals}{flag}")


def fmt_pct(v):
    return "n/a" if pd.isna(v) else f"{100 * v:.2f}%"


def fmt_int(v):
    return "n/a" if pd.isna(v) else f"{int(v)}"


def fmt_pts(v):
    return "n/a" if pd.isna(v) else f"{v:.1f}"


def classify_2024(series_full, value_2024):
    """series_full: the measure's values across FULL_SEASONS only (a dict
    season->value). Returns a one-line description of where 2024 sits."""
    vals = sorted(v for v in series_full.values() if v is not None)
    if value_2024 is None or not vals:
        return "n/a (no data)"
    n = len(vals)
    rank = bisect.bisect_left(vals, value_2024) + 1
    q1 = pd.Series(vals).quantile(0.25)
    q3 = pd.Series(vals).quantile(0.75)
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    if value_2024 < lo or value_2024 > hi:
        tag = "OUTLIER (outside 1.5x IQR)"
    elif value_2024 == vals[0] or value_2024 == vals[-1]:
        tag = "at the extreme (min/max, not a statistical outlier)"
    elif rank <= n * 0.25 or rank >= n * 0.75 + 1:
        tag = "toward one end, not extreme"
    else:
        tag = "near the middle"
    return f"rank {rank}/{n} among full seasons -- {tag}"


def print_2024_callouts(summary):
    print("\n=== Where does 2024 sit, per measure/position (vs. full seasons only, 2026 excluded)? ===")
    for value_col, label in [
        ("spike_rate", "Spike rate"),
        ("share_of_spikes", "Share of all spikes"),
        ("n_eligible", "Eligible player-weeks (pool size)"),
        ("median_spike_pts", "Median spike fantasy_points_half"),
        ("p90_spike_pts", "P90 spike fantasy_points_half"),
    ]:
        print(f"\n{label}:")
        for pos in POSITIONS:
            pdf = summary[summary["pos"] == pos]
            full = {row["season"]: row[value_col] for _, row in pdf.iterrows() if row["season"] in FULL_SEASONS}
            val_2024 = pdf.loc[pdf["season"] == 2024, value_col].iloc[0]
            print(f"  {pos}: {classify_2024(full, val_2024)}  (2024 value: "
                  f"{fmt_pct(val_2024) if value_col in ('spike_rate', 'share_of_spikes') else (fmt_int(val_2024) if value_col == 'n_eligible' else fmt_pts(val_2024))})")


def print_2023_for_reference(summary):
    print("\n=== 2023 (reserve second-opinion slice) for comparison ===")
    for value_col, label, fmt in [
        ("spike_rate", "Spike rate", fmt_pct),
        ("share_of_spikes", "Share of all spikes", fmt_pct),
        ("n_eligible", "Eligible player-weeks", fmt_int),
        ("median_spike_pts", "Median spike pts", fmt_pts),
        ("p90_spike_pts", "P90 spike pts", fmt_pts),
    ]:
        row = summary[(summary["season"] == 2023)]
        vals = "  ".join(f"{pos}={fmt(row[row['pos']==pos][value_col].iloc[0])}" for pos in POSITIONS)
        print(f"  {label}: {vals}")


def main():
    con = sqlite3.connect(str(REPO_ROOT / "faab_history_core_v0_1.db"))
    df = load_rows(con)
    con.close()

    print(f"[INFO] {len(df)} eligible (spike_flag NOT NULL) QB/RB/WR/TE player-weeks across "
          f"{df['season'].nunique()} seasons ({ALL_SEASONS[0]}-{ALL_SEASONS[-1]}, excl. 2019; "
          f"{sorted(PARTIAL_SEASONS)} partial)")

    summary = build_summary(df)

    print_table(summary, "spike_rate", "1. Spike rate by position (share of eligible player-weeks with spike_flag=1)", fmt_pct)
    print_table(summary, "share_of_spikes", "2. Share of all spikes by position (of every spike that season)", fmt_pct)
    print_table(summary, "n_eligible", "3. Eligible player-weeks by position (denominators)", fmt_int)
    print_table(summary, "median_spike_pts", "4a. Spike magnitude -- median fantasy_points_half among spike rows", fmt_pts)
    print_table(summary, "p90_spike_pts", "4b. Spike magnitude -- 90th percentile fantasy_points_half among spike rows", fmt_pts)

    print_2024_callouts(summary)
    print_2023_for_reference(summary)


if __name__ == "__main__":
    main()
