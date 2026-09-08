#!/usr/bin/env python3
"""Populate player_week_features for every (season, week, player_id) in
player_week_stats that is eligible (>=3 prior non-playoff games, respecting
season-gap boundaries) -- eligibility is determined independently via
features_lib.FeatureEngine, not by checking labels_player_week row existence.
"""
import argparse
import sqlite3

from features_lib import FEATURE_COLS, FeatureEngine


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="faab_history_core_v0_1.db")
    args = ap.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    engine = FeatureEngine(con)

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
            to_insert.append((season, week, pid, *[feat[c] for c in FEATURE_COLS]))

    ph = ",".join("?" * len(seasons))
    con.execute(f"DELETE FROM player_week_features WHERE season IN ({ph})", seasons)
    cols_sql = ", ".join(FEATURE_COLS)
    placeholders = ",".join("?" * (3 + len(FEATURE_COLS)))
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
    for c in FEATURE_COLS:
        idx = 3 + FEATURE_COLS.index(c)
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
