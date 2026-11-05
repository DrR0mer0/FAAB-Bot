"""Read-only information check for the display-only "starter back" flag (model/starter_back.py): the 2022-2025 spike
rate of flagged vs unflagged QB/RB/WR/TE player-weeks, at the committed LEAD_MIN and PRE_WINDOW_GAMES.

Universe: label rows (labels_player_week, REG weeks 1-18, spike_flag set) with a full 3-game trailing window, all of
it for the team he played for that week. Snap shares come from tr_snap_counts in the DB; nothing under nflverse_raw/
is read. Availability proxy (no past weekly rosters on disk): the teammate PLAYED that week -- an oracle version of
condition (c). The spike label is measured against the player's own trailing baseline, which the teammate's absence
inflates, so part of the gap is likely a label artifact (see the CLAUDE.md paragraph). Display only; nothing here
feeds a score, rank or pick.

    python evaluation/starter_back_spike_check.py [--db PATH]

Result on 2026-11-04 (LEAD_MIN 0.30, PRE_WINDOW_GAMES 4): flagged 6.5% (138 of 2,132) vs unflagged 14.4%, lower at
every position.
"""
import argparse
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "model"))
import starter_back as sb  # noqa: E402
from features_lib import FeatureEngine  # noqa: E402

POSITIONS = ("QB", "RB", "WR", "TE")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{Path(args.db).resolve().as_posix()}?mode=ro", uri=True)   # read-only
    con.row_factory = sqlite3.Row
    snaps = sb.SnapIndex.from_db(con, min_season=2021)
    engine = FeatureEngine(con)
    rows = con.execute("""SELECT l.season, l.week, l.player_id, l.spike_flag, s.pos, s.team FROM labels_player_week l
                          JOIN player_week_stats s USING (season, week, player_id) WHERE l.season BETWEEN 2022 AND 2025
                          AND l.week <= 18 AND l.spike_flag IS NOT NULL AND s.pos IN ('QB','RB','WR','TE')""").fetchall()

    cell = defaultdict(lambda: [0, 0])          # (pos, flagged/unflagged, subgroup) -> [n, spikes]
    for r in rows:
        s, w, pid, pos, team = r["season"], r["week"], r["player_id"], r["pos"], r["team"]
        window = engine.trailing_window(pid, s, w)
        if len(window) < 3 or any(t != team for _s, _w, t, *_ in window):
            continue

        def played(x, s=s, w=w, team=team):
            return snaps.player.get(x, {}).get((s, w), (None,))[0] == team

        flags = sb.starter_back(pid, pos, team, window, (s, w), snaps, played)
        key = "flagged" if flags else "unflagged"
        subs = ["all"]
        if flags:
            subs.append("back already" if any(f["returned"] != "not back yet" for f in flags) else "back this week")
        for sub in subs:
            for k in ((pos, key, sub), ("ALL", key, sub)):
                cell[k][0] += 1
                cell[k][1] += r["spike_flag"]

    print(f"LEAD_MIN {sb.LEAD_MIN}, PRE_WINDOW_GAMES {sb.PRE_WINDOW_GAMES}")
    print("pos  flagged n  spike rate | unflagged n  spike rate | difference (95% CI)  | flagged: back already / back this week")
    for pos in POSITIONS + ("ALL",):
        n1, k1 = cell[(pos, "flagged", "all")]
        n0, k0 = cell[(pos, "unflagged", "all")]
        if not n1 or not n0:
            print(f"{pos:4} no rows")
            continue
        p1, p0 = k1 / n1, k0 / n0
        se = math.sqrt(p1 * (1 - p1) / n1 + p0 * (1 - p0) / n0)
        a = cell[(pos, "flagged", "back already")]
        b = cell[(pos, "flagged", "back this week")]
        rate = lambda c: c[1] / c[0] if c[0] else float("nan")  # noqa: E731
        print(f"{pos:4} {n1:9} {p1:11.1%} | {n0:11} {p0:11.1%} | {p1 - p0:+6.1%} ({p1 - p0 - 1.96 * se:+.1%} to "
              f"{p1 - p0 + 1.96 * se:+.1%}) | {a[1]}/{a[0]} = {rate(a):.1%}  /  {b[1]}/{b[0]} = {rate(b):.1%}")


if __name__ == "__main__":
    main()
