#!/usr/bin/env python3
"""Yahoo's current player status as a check on a long-term weekly-roster status (pool rule v4, from 2026 week 8),
and Yahoo's status and team as a check on an ACT row of the weekly roster (pool rule v5, roster_conflict below).

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


def roster_teams(con, snapshot_id):
    """{gsis: Yahoo editorial team} from one league roster snapshot ('' / NULL teams say nothing). {} for an older
    lg_rosters table without the column."""
    if snapshot_id is None or "team" not in {c[1] for c in con.execute("PRAGMA table_info(lg_rosters)")}:
        return {}
    return {pid: team for pid, team in con.execute(
        "SELECT player_id, team FROM lg_rosters WHERE snapshot_id=? AND player_id IS NOT NULL AND team IS NOT NULL "
        "AND team<>''", (snapshot_id,))}


def collect(con, as_of, league_view, max_age_hours=ROSTER_MAX_AGE_HOURS):
    """The record handed to score_week.py (write / read) and kept with the run:
      status_by_player  {gsis: raw Yahoo code}, '' = no status
      team_by_player    {gsis: Yahoo's editorial NFL team, as Yahoo writes it} -- pool rule v5's team check (from
                        2026-11-10; a record without it skips that check)
      rostered          the roster snapshot used (or why none was)
      available         the league-availability fetch's status and how many statuses it gave
      unknown_codes     codes neither MAY_PLAY nor OUT (they never override)"""
    by, rostered = roster_statuses(con, as_of, max_age_hours)
    teams = roster_teams(con, rostered["snapshot_id"]) if rostered.get("used") else {}
    available = {"status": league_view["status"], "n": 0}
    if league_view["status"] == "ok":
        live = {pid: (r.get("player_status") or "") for pid, r in league_view["by_player"].items()}
        available["n"] = len(live)
        by = {**by, **live}
        teams = {**teams, **{pid: r["team"] for pid, r in league_view["by_player"].items() if r.get("team")}}
    return {"version": VERSION, "status_by_player": dict(sorted(by.items())), "team_by_player": dict(sorted(teams.items())),
            "rostered": rostered, "available": available, "unknown_codes": sorted({c for c in by.values() if not known(c)})}


def skipped(reason):
    return {"version": VERSION, "status_by_player": {}, "team_by_player": {}, "rostered": {"used": False, "reason": reason},
            "available": {"status": "skipped", "n": 0}, "unknown_codes": []}


def summary(rec):
    """The record without the per-player maps: what the run config keeps about the sources."""
    return ({k: v for k, v in rec.items() if k not in ("status_by_player", "team_by_player")}
            | {"n_players": len(rec["status_by_player"])}
            | ({"n_teams": len(rec["team_by_player"])} if "team_by_player" in rec else {}))


# ---- pool rule v5 (from 2026 week 10 at the earliest): Yahoo against nflverse's weekly roster ----------------------
# Found 2026-11-10: Kendre Miller (NO) was waived between Saturday 11-07 and Sunday 11-08 morning; at the week 9 sun run
# nflverse's weekly roster still said ACT for week 9 (it rewrote the row to CUT two days later), Yahoo said NA, ESPN had
# him as a free agent, and he was ranked production #4. Over the 7 runs that kept their roster file (week 6 sun to
# week 9 sun) the checks below would have fired three times, each on a player who did not play that week:
#   (a) ACT + Yahoo NA  -- Cody White (week 9 thu; released 11-05, re-signed 11-06), Kendre Miller (week 9 sun).
#   (b) a Yahoo team nflverse never had -- Jacoby Brissett (week 9 sun; ARI -> BAL after BAL's Thursday game).
# Yahoo NA alone is NOT off-roster: it is routine for practice-squad (DEV) players (46 such scored player-runs, 5 of
# whom played after an elevation), so (a) needs nflverse to say ACT. Yahoo never left a scored player's team empty --
# not even Miller's after his release -- so an empty team is not used as a signal.
ROSTER_RULE = "yahoo-roster-v1"
# Yahoo's editorial team codes where they differ from nflverse's (seen: LAR; the others defensively)
YAHOO_TEAM_CODES = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS"}


def nflverse_team_code(yahoo_team):
    return YAHOO_TEAM_CODES.get((yahoo_team or "").upper(), (yahoo_team or "").upper()) or None


def roster_conflict(nfl_row, nfl_teams_this_season, yahoo, yahoo_team, known_teams, norm=lambda t: t):
    """Pool rule v5 for one player whose newest weekly-roster row does not already put him off a roster.
      nfl_row                (week, status, team) -- his newest weekly-roster row at or before the week, or None
      nfl_teams_this_season  every team on his rows this season (through the week)
      yahoo, yahoo_team      Yahoo's raw status and editorial team; None = Yahoo says nothing
      known_teams            the nflverse team codes in the file this season (a Yahoo code outside them is never used)
      norm                   the nflverse team-code crosswalk (score_week.norm_team)
    -> None, or (outcome, team, why):
      ('off_roster', None, why)      nflverse says ACT, Yahoo says NA (Inactive: Coach's Decision or Not on Roster).
                                     At log time (Thursday / Sunday 07:00 Pacific) no game still to be played has
                                     announced its inactives, so for an ACT player NA means he is off the roster.
      ('team_changed', team, why)    Yahoo has him on a team nflverse has no row for this season: Yahoo is ahead of
                                     nflverse (a trade or signing it hasn't caught up with). He goes through v4's team
                                     check with Yahoo's team as his roster team -- team-change suppressed, never called
                                     off-roster. If Yahoo's team is one nflverse already had him on, nflverse is the
                                     newer source (it recorded the move away) and nothing changes."""
    if nfl_row is None:
        return None
    week, status, team = nfl_row
    if status == "ACT" and yahoo == "NA":
        return ("off_roster", None, f"Yahoo NA (Inactive: Coach's Decision or Not on Roster) while the weekly roster says "
                                    f"ACT (week {week}, {team})")
    y = nflverse_team_code(yahoo_team)
    known = {norm(t) for t in known_teams if t}
    if y and team and y in known and y != norm(team) and y not in {norm(t) for t in nfl_teams_this_season if t}:
        return ("team_changed", y, f"Yahoo lists him on {y}; the weekly roster has {team} (week {week}, {status}) and no "
                                   f"{y} row this season")
    return None


def write(path, rec):
    Path(path).write_text(json.dumps(rec, sort_keys=True), encoding="utf-8")


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))
