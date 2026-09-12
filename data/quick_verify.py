
import argparse, sqlite3, os, sys
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
ap = argparse.ArgumentParser()
ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
args = ap.parse_args()

exists = os.path.exists(args.db)
size = os.path.getsize(args.db) if exists else 0
print(f"DB exists: {exists}, size bytes: {size}")
if not exists or size < 10000:
    print("WARNING: DB is missing or very small; likely no data loaded.")
con = sqlite3.connect(args.db)
cur = con.cursor()
def count(name):
    try:
        return cur.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    except Exception as e:
        return f"ERR: {e}"
for t in ("player_week_stats","nfl_games","ref_players"):
    print(f"{t}: {count(t)}")

def by_season(table):
    try:
        return cur.execute(f"SELECT season, COUNT(*) FROM {table} GROUP BY season ORDER BY season").fetchall()
    except Exception as e:
        return f"ERR: {e}"

print("\nplayer_week_stats by season:", by_season("player_week_stats"))
print("nfl_games by season:", by_season("nfl_games"))

try:
    weeks = cur.execute("SELECT MIN(week), MAX(week) FROM nfl_games").fetchone()
    print("nfl_games week range:", weeks)
    playoffs = cur.execute("SELECT COUNT(*) FROM nfl_games WHERE is_playoffs=1").fetchone()[0]
    london = cur.execute("SELECT COUNT(*) FROM nfl_games WHERE is_london=1").fetchone()[0]
    print(f"nfl_games playoffs: {playoffs}, london: {london}")
except Exception as e:
    print(f"ERR checking nfl_games detail: {e}")

con.close()
