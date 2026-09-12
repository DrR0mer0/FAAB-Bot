#!/usr/bin/env python3
"""Apply _init_schema.sql to the FAAB history SQLite database."""
import argparse
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--schema", default=str(Path(__file__).resolve().parent / "_init_schema.sql"))
    args = ap.parse_args()

    schema_sql = Path(args.schema).read_text(encoding="utf-8")

    con = sqlite3.connect(args.db)
    try:
        con.executescript(schema_sql)

        # CREATE TABLE IF NOT EXISTS won't add a column to an already-existing
        # table, so reconcile columns for tables that pre-date a schema change.
        for table, col, coltype in [("player_week_features", "share_delta_vs_prior_season", "REAL")]:
            existing_cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if existing_cols and col not in existing_cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
                print(f"[MIGRATED] added {table}.{col}")

        con.commit()
    finally:
        con.close()

    print(f"[DONE] Applied {args.schema} to {args.db}")


if __name__ == "__main__":
    main()
