#!/usr/bin/env python3
"""Load nflverse players.csv into ref_players (player_id, full_name, pos, first_season, last_season)."""
import argparse
import csv
import sqlite3
from pathlib import Path


def to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="faab_history_core_v0_1.db")
    ap.add_argument("--csv", default="nflverse_raw/players.csv")
    args = ap.parse_args()

    path = Path(args.csv)
    rows = []
    skipped = 0
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            player_id = r.get("gsis_id")
            full_name = r.get("display_name")
            pos = r.get("position")
            if not player_id or not full_name or not pos:
                skipped += 1
                continue
            rows.append((
                player_id, full_name, pos,
                to_int(r.get("rookie_season")), to_int(r.get("last_season")),
            ))

    con = sqlite3.connect(args.db)
    con.executemany(
        "INSERT OR REPLACE INTO ref_players (player_id, full_name, pos, first_season, last_season) VALUES (?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()

    print(f"[DONE] loaded {len(rows)} rows into ref_players ({skipped} skipped for missing required fields)")


if __name__ == "__main__":
    main()
