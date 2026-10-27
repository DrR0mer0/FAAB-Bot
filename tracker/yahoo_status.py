#!/usr/bin/env python3
"""Yahoo's current player status as a check on a long-term weekly-roster status (pool rule v4, from 2026 week 8).

Why: nflverse's weekly roster kept Josh Jacobs on the exempt list (EXE) in week 7, three days after his
reinstatement, so score_week.py's availability gate left him out of the week 7 pool. Yahoo had tracked the change
(CEL on 10-15, no status on 10-22); ESPN said DAY_TO_DAY from 10-05 on, including the weeks he really was exempt, so
ESPN must not decide (user decision 2026-10-22).

THE RULE -- one definition for model/score_week.py's availability gate and the tracker's record of it, the same rule
as startsit/build_week.py's (commit b69adaf; a test keeps the two in step):
  A long-term weekly-roster status in OVERRIDABLE (EXE / RES / PUP / SUS) suppresses a player unless Yahoo's
  current status for him says he may play (MAY_PLAY: no status, Q, GTD, DTD). Then he is treated as available,
  still subject to the Out/Doubtful rule (tracker/injuries.py), and the conflict is recorded with the run.
  A player Yahoo says nothing about (not in either Yahoo source, or an unmatched name) keeps the long-term status;
  so does an unknown Yahoo code (it is reported, never guessed). RSN and CUT / RET are not overridable.
  Yahoo D (doubtful) never overrules (user decision 2026-10-27): the pool rule is yes/no and leaves Doubtful players
  out, so D is an OUT code here -- the one difference from startsit, which gives D a play chance above 0.
  ESPN is a cross-check only: the tracker records what it said, nothing reads it here.

SOURCES (collect) -- Yahoo, read through the API only:
  rostered   lg_rosters.status from the newest successful league roster snapshot (league/league_data.py; the
             06:35 scheduled task) taken at or before the run and at most ROSTER_MAX_AGE_HOURS before it.
             '' = no status; NULL = a snapshot from before statuses were captured (2026-10-22), i.e. unknown.
  available  the league-availability fetch `log` makes for the run (tracker/league_availability.py), now made
             BEFORE O.D.D.S. runs: every free agent / waiver player's player_status (None there = no status).
             Live, so it wins over the roster snapshot for a player in both (added or dropped since 06:35).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

VERSION = "yahoo-status-v1"
OVERRIDABLE = frozenset({"EXE", "RES", "PUP", "SUS"})
# Yahoo player-status codes. MAY_PLAY are the ones startsit gives a play chance above 0 EXCEPT D; OUT are the ones it
# gives 0, plus D (doubtful: the pool rule is yes/no and excludes Doubtful, so D never overrules a long-term status).
MAY_PLAY = frozenset({"", "Q", "GTD", "DTD"})
OUT = frozenset({"D", "O", "IR", "IR-R", "IR-NR", "PUP-R", "PUP-P", "PUP", "NFI-R", "NFI-A", "NFI", "SUSP", "CEL", "NA",
                 "COVID-19"})
ROSTER_MAX_AGE_HOURS = 36   # a day and a half: the 06:35 snapshot before a 07:00 run, or the one before it if that failed


def known(code):
    return code in MAY_PLAY or code in OUT


def overrides(long_term, yahoo):
    """Does Yahoo's current status (raw code; None = Yahoo says nothing about him) contradict this long-term
    weekly-roster status? Only for an OVERRIDABLE status and a known code that says he may play."""
    return long_term in OVERRIDABLE and yahoo in MAY_PLAY


def _table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def roster_statuses(con, as_of, max_age_hours=ROSTER_MAX_AGE_HOURS):
    """({gsis: raw status}, source record) from the newest successful roster snapshot taken at or before as_of,
    if it is recent enough. Rows with no gsis id or a NULL status (not captured) say nothing."""
    rec = {"snapshot_id": None, "taken_at": None, "used": False, "reason": None, "n": 0}
    if not (_table_exists(con, "lg_snapshots") and _table_exists(con, "lg_rosters")):
        rec["reason"] = "no league roster tables in this database"
        return {}, rec
    row = con.execute("SELECT snapshot_id, taken_at FROM lg_snapshots WHERE resource='rosters' AND ok=1 AND taken_at<=? "
                      "ORDER BY taken_at DESC, snapshot_id DESC LIMIT 1", (as_of.isoformat(),)).fetchone()
    if row is None:
        rec["reason"] = "no successful league roster snapshot taken before the run"
        return {}, rec
    rec.update(snapshot_id=row[0], taken_at=row[1])
    age = as_of - datetime.fromisoformat(row[1])
    if age > timedelta(hours=max_age_hours):
        rec["reason"] = f"the newest roster snapshot is {age.total_seconds() / 3600:.0f}h old (limit {max_age_hours}h)"
        return {}, rec
    out = {pid: st for pid, st in con.execute(
        "SELECT player_id, status FROM lg_rosters WHERE snapshot_id=? AND player_id IS NOT NULL AND status IS NOT NULL",
        (row[0],))}
    if not out:
        rec["reason"] = "the roster snapshot carries no player statuses (taken before they were captured)"
        return {}, rec
    rec.update(used=True, n=len(out))
    return out, rec


def collect(con, as_of, league_view, max_age_hours=ROSTER_MAX_AGE_HOURS):
    """The record handed to score_week.py (write / read) and kept with the run:
      status_by_player  {gsis: raw Yahoo code}, '' = no status
      rostered          the roster snapshot used (or why none was)
      available         the league-availability fetch's status and how many statuses it gave
      unknown_codes     codes neither MAY_PLAY nor OUT (they never override)"""
    by, rostered = roster_statuses(con, as_of, max_age_hours)
    available = {"status": league_view["status"], "n": 0}
    if league_view["status"] == "ok":
        live = {pid: (r.get("player_status") or "") for pid, r in league_view["by_player"].items()}
        available["n"] = len(live)
        by = {**by, **live}
    return {"version": VERSION, "status_by_player": dict(sorted(by.items())), "rostered": rostered, "available": available,
            "unknown_codes": sorted({c for c in by.values() if not known(c)})}


def skipped(reason):
    return {"version": VERSION, "status_by_player": {}, "rostered": {"used": False, "reason": reason},
            "available": {"status": "skipped", "n": 0}, "unknown_codes": []}


def summary(rec):
    """The record without the per-player map: what the run config keeps about the sources."""
    return {k: v for k, v in rec.items() if k != "status_by_player"} | {"n_players": len(rec["status_by_player"])}


def write(path, rec):
    Path(path).write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
