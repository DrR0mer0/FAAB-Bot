#!/usr/bin/env python3
"""Populate player_week_features for every (season, week, player_id) in
player_week_stats that is eligible (>=3 prior non-playoff games, respecting
season-gap boundaries) -- eligibility is determined independently via
features_lib.FeatureEngine, not by checking labels_player_week row existence.
"""
import argparse
import sqlite3
from pathlib import Path

from availability import WEEKLY_ROSTER_FIRST_SEASON, Availability
from features_lib import PERSISTED_FEATURE_COLS, FeatureEngine, norm_team

REPO_ROOT = Path(__file__).resolve().parent.parent


def availability_summary(availability, seasons):
    """What starter_absent_asof can use per season, in words: which half of the source (weekly roster / injury report)
    is missing where, and which seasons are NULL by design (availability.WEEKLY_ROSTER_FIRST_SEASON)."""
    missing = set(availability.missing)
    full, roster_only, none_ = [], [], []
    for s in seasons:
        if f"roster_weekly_{s}.csv" in missing:
            none_.append(s)
        elif f"injuries_{s}.csv" in missing:
            roster_only.append(s)
        else:
            full.append(s)
    early = [s for s in seasons if s < WEEKLY_ROSTER_FIRST_SEASON]
    lines = [f"weekly roster + injury report for {[s for s in full if s >= WEEKLY_ROSTER_FIRST_SEASON]}"]
    if roster_only:
        lines.append(f"weekly roster only, NO injury report (Out/Doubtful not seen; reserve list, released, other team "
                     f"still are) for {roster_only}: missing {[f'injuries_{s}.csv' for s in roster_only]}")
    if none_:
        lines.append(f"NO weekly roster (feature NULL for the whole season) for {none_}: missing "
                     f"{[f'roster_weekly_{s}.csv' for s in none_]}")
    if early:
        lines.append(f"NULL by design for {early} (before {WEEKLY_ROSTER_FIRST_SEASON}: season-level roster files)")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--availability-raw-dir", default=None,
                    help="compute starter_absent_asof from nflverse injuries_<season>.csv + roster_weekly_<season>.csv "
                         "in this folder (normally nflverse_raw); without it the column is written NULL")
    ap.add_argument("--download-missing", action="store_true",
                    help="with --availability-raw-dir: first download any missing season file (starter_back."
                         "ensure_season_files, same nflverse release URLs)")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    # Additive schema change: starter_absent_asof (v3 candidate). Old models select their own columns by name.
    existing_cols = {r[1] for r in con.execute("PRAGMA table_info(player_week_features)")}
    for c in PERSISTED_FEATURE_COLS:
        if c not in existing_cols:
            con.execute(f"ALTER TABLE player_week_features ADD COLUMN {c} INTEGER")
            print(f"[INFO] added column player_week_features.{c}")
    con.commit()

    availability = None
    if args.availability_raw_dir:
        seasons_all = sorted(r[0] for r in con.execute("SELECT DISTINCT season FROM player_week_stats"))
        if args.download_missing:
            # Completed seasons only: the in-progress season's injury report is the live run's business (tracker's
            # raw/injuries copies), and a partial file here would be read as a "prior season" next year.
            import starter_back
            got = starter_back.ensure_season_files(args.availability_raw_dir, [s for s in seasons_all if s < max(seasons_all)])
            print(f"[INFO] downloaded {len(got)} missing nflverse file(s): {[p.name for p in got]}")
        availability = Availability.from_nflverse(args.availability_raw_dir, seasons_all, norm_team=norm_team)
        for line in availability_summary(availability, seasons_all):
            print(f"[INFO] starter_absent_asof: {line}")
    engine = FeatureEngine(con, availability=availability)

    seasons = engine.stats_seasons
    print(f"[INFO] positions with no touch signal, found in data: {sorted(engine.no_touch_signal_pos)}")

    to_insert = []
    n_checked = 0
    for pid, entries in engine.player_history.items():
        for (season, week, _team, _pos, _touches) in entries:
            n_checked += 1
            feat = engine.compute_features(season, week, pid)
            if feat is None:
                continue
            to_insert.append((season, week, pid, *[feat[c] for c in PERSISTED_FEATURE_COLS]))

    ph = ",".join("?" * len(seasons))
    con.execute(f"DELETE FROM player_week_features WHERE season IN ({ph})", seasons)
    cols_sql = ", ".join(PERSISTED_FEATURE_COLS)
    placeholders = ",".join("?" * (3 + len(PERSISTED_FEATURE_COLS)))
    con.executemany(
        f"""INSERT OR REPLACE INTO player_week_features
           (season, week, player_id, {cols_sql})
           VALUES ({placeholders})""",
        to_insert,
    )
    con.commit()

    print(f"[DONE] {n_checked} non-playoff player-weeks checked, {len(to_insert)} eligible rows written for seasons {seasons}")

    # ---- validation report ----
    total = len(to_insert)
    print(f"\nNULL rates per feature (n={total}):")
    for c in PERSISTED_FEATURE_COLS:
        idx = 3 + PERSISTED_FEATURE_COLS.index(c)
        n_null = sum(1 for row in to_insert if row[idx] is None)
        pct = 100.0 * n_null / total if total else 0.0
        print(f"  {c}: {n_null} NULL ({pct:.2f}%)")

    henry = con.execute("SELECT player_id FROM ref_players WHERE full_name = 'Derrick Henry'").fetchone()
    print("\nDerrick Henry 2018 week 14 feature row:")
    if henry:
        pid = henry["player_id"]
        frow = con.execute(
            "SELECT * FROM player_week_features WHERE season=2018 AND week=14 AND player_id=?", (pid,)
        ).fetchone()
        if frow:
            for k in frow.keys():
                print(f"  {k}: {frow[k]}")
        else:
            print("  No feature row found (unexpected).")
    else:
        print("  Derrick Henry not found in ref_players.")

    print("\n3 example starter_absent_proxy=1 rows (skill positions preferred):")
    examples = con.execute(
        """SELECT f.season, f.week, f.player_id FROM player_week_features f
           JOIN player_week_stats s ON f.season=s.season AND f.week=s.week AND f.player_id=s.player_id
           WHERE f.starter_absent_proxy=1
           ORDER BY CASE WHEN s.pos IN ('RB','WR','TE','QB') THEN 0 ELSE 1 END
           LIMIT 3"""
    ).fetchall()
    names = {row["player_id"]: row["full_name"] for row in con.execute("SELECT player_id, full_name FROM ref_players")}
    for ex in examples:
        season, week, pid = ex["season"], ex["week"], ex["player_id"]
        frow = con.execute(
            "SELECT * FROM player_week_features WHERE season=? AND week=? AND player_id=?", (season, week, pid)
        ).fetchone()
        prow = con.execute(
            "SELECT team, pos FROM player_week_stats WHERE season=? AND week=? AND player_id=?", (season, week, pid)
        ).fetchone()
        print(f"  {names.get(pid, pid)} ({prow['pos']}, {prow['team']}) season={season} week={week}")
        for k in frow.keys():
            if k not in ("season", "week", "player_id"):
                print(f"    {k}: {frow[k]}")

    con.close()


if __name__ == "__main__":
    main()
