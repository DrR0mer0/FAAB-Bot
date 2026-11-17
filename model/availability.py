#!/usr/bin/env python3
"""Pre-game availability of a player for one (season, week): the as-of source behind starter_absent_asof.

The leaky starter_absent_proxy asks "did the presumed starter play this week?" -- an answer that only exists after
the games. This module answers the question that exists BEFORE them: "is the presumed starter known to be out?",
from two pre-game sources only:

  * the NFL injury report (nflverse `injuries`): designated Out or Doubtful -> absent. Questionable -> available,
    the same line as the pool rule (tracker/injuries.py).
  * the weekly roster (nflverse `weekly_rosters`) for that week: a status in ABSENT_ROSTER_STATUSES (reserve list,
    suspended, exempt list, practice squad, released, retired), on another team, or no row for the week while his
    team has rows -> absent. Only from WEEKLY_ROSTER_FIRST_SEASON (2016): earlier files are season-level, so those
    seasons are unknown (None).

Never read: INA (the game-day inactive list, announced ~90 minutes before kickoff -- after a Sunday-morning run for
every game, so it can't be used live), stat rows or snap counts for the week itself.

Checked 2026-11-16 (read-only, 2022-2024 RB/WR/TE): of 15,428 player-weeks with a stat row, none carried a reserve
status in that week's roster row and one was designated Doubtful -- the week-W roster row is not written after the
game, so reading it for week W does not leak the outcome.
Checked again 2026-11-17 for every season 2010-2025 (QB/RB/WR/TE player-weeks with a stat row): 0.0-0.2% would be
called absent in 2016-2025, 21-26% in 2010-2015 -- hence the cutoff. train_candidate_v3.py re-checks it before
training and stops above 1% in any season from 2016.

What historical data CAN'T reproduce: each injury-report row is the player-week's FINAL designation, so a history-
built value matches what a Sunday-morning run knows (Friday's report plus Saturday moves), not a Thursday run. Live,
a Thursday run sees only the reserve-list half for most teams; that is a known, documented train/serve gap, not a
bug (see the change plan, docs/starter_absent_asof_plan.md).

Returns True (absent), False (available) or None (no roster data for that season-week -- the feature is NULL then,
which XGBoost routes as missing; never guess 0).
"""
import csv
from pathlib import Path

ABSENT_DESIGNATIONS = {"Out", "Doubtful"}
# ONE set of "won't play this week" weekly-roster statuses, for history and for live runs alike (score_week.py's
# candidate availability reads it too, on top of the pool rule's own checks): reserve/IR (RES), PUP, non-football
# injury (NFI), suspended (SUS, RSN), the exempt list (EXE, E01, E14), the practice squad (DEV), released (CUT) and
# retired (RET). The trade codes (TRC, TRD, TRT) are not here: a traded player is caught by the team check.
ABSENT_ROSTER_STATUSES = frozenset({"RES", "PUP", "NFI", "SUS", "RSN", "EXE", "E01", "E14", "DEV", "CUT", "RET"})

# Weekly rosters are only WEEKLY from 2016 on. Measured 2026-11-17 (read-only, QB/RB/WR/TE player-weeks with a stat
# row, i.e. the player played): this module would have called 21-26% of them absent in each season 2010-2015, against
# 0.0-0.2% in every season 2016-2025. Two causes: (1) the 2010-2015 files repeat a season-level roster every week
# (about 1,750 rows a week; 6-9% of players change status within a season, against 33% in 2018), so a starter who
# went on IR in week 12 shows RES from week 1 -- noise and, worse, future information; (2) they use GSIS team codes
# for five franchises (GSIS_TEAM_ALIASES below). Before this season the roster can't say who was available in a given
# week, so availability is unknown: has_week() is False and absent() returns None, and starter_absent_asof is NULL
# for every row of those seasons -- one meaning wherever the feature has a value (docs/starter_absent_asof_plan.md,
# section 3, correction of 2026-11-17).
WEEKLY_ROSTER_FIRST_SEASON = 2016

# GSIS team codes in the 2010-2015 weekly-roster files (data/team_crosswalk.py maps only STL/SD/OAK). Those seasons
# are unknown anyway (above); mapped for robustness, so a team code never reads as "on another team".
GSIS_TEAM_ALIASES = {"ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "SL": "LA"}


class Availability:
    """Keyed (season, week, player_id).

    designation  {key: report_status}   -- non-empty injury-report statuses only
    roster       {key: (team, status)}  -- weekly-roster rows
    roster_weeks {(season, week)}       -- season-weeks with any roster rows (missing = unknown, not absent)
    teams_in_week {(season, week, team)} -- teams with rows that week (a team on a bye has none)
    """

    def __init__(self, norm_team=lambda t: t):
        self.norm_team = norm_team
        self.designation = {}
        self.roster = {}
        self.roster_weeks = set()
        self.teams_in_week = set()

    # ---- builders ---------------------------------------------------------------------------------------------
    @classmethod
    def from_nflverse(cls, raw_dir, seasons, norm_team=lambda t: t, game_type="REG"):
        """Historical source: nflverse_raw/injuries_<season>.csv and roster_weekly_<season>.csv. A season whose files
        are missing is simply not loaded (its rows come out None); the caller reports which seasons those were."""
        av = cls(norm_team)
        raw_dir = Path(raw_dir)
        av.loaded_seasons, av.missing = [], []
        for s in seasons:
            inj, ros = raw_dir / f"injuries_{s}.csv", raw_dir / f"roster_weekly_{s}.csv"
            if not ros.exists():
                av.missing.append(ros.name)
                continue
            av._add_roster_csv(ros, s, game_type)
            if inj.exists():
                av._add_injury_csv(inj, s, game_type)
            else:
                av.missing.append(inj.name)
            av.loaded_seasons.append(s)
        return av

    @classmethod
    def from_live(cls, season, week, available_fn, has_data):
        """Live source: score_week.py's own "is this player available to play for this team this week?" -- the
        check behind the starter-back flag (starter_back_available): on an NFL roster, not on a long-term list
        unless Yahoo's status overrules it (pool rule v4), not on the practice squad, not Out/Doubtful, weekly-roster
        team == team. Reusing it keeps the feature consistent with the pool rule the same run applies. Yahoo's
        override has no history, so the training values can't include it -- it only matters where nflverse's
        weekly roster lags (e.g. a player back from IR before nflverse shows it), which the final historical
        files no longer do.
          available_fn(player_id, team) -> bool  (score_week.py's candidate_available: the starter-back check plus
                                                 ABSENT_ROSTER_STATUSES, the set history uses, and INA counted as
                                                 on the team, as history never calls INA absent)
          has_data: False when the run has no weekly roster (the feature is then NULL, as in training)"""
        av = cls()
        av._live = (season, week, available_fn)
        # (a live season is always >= WEEKLY_ROSTER_FIRST_SEASON; has_week() still applies the cutoff)
        if has_data:
            av.roster_weeks.add((season, week))
        return av

    def _add_roster_csv(self, path, season, game_type):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("game_type", "REG") != game_type or not r.get("gsis_id"):
                    continue
                try:
                    w = int(r["week"])
                except (KeyError, ValueError):
                    continue
                team = self.team_code(r["team"]) if r.get("team") else None
                self.roster[(season, w, r["gsis_id"])] = (team, r.get("status"))
                self.roster_weeks.add((season, w))
                if team:
                    self.teams_in_week.add((season, w, team))

    def _add_injury_csv(self, path, season, game_type):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("game_type", "REG") != game_type or not r.get("gsis_id") or not r.get("report_status"):
                    continue
                try:
                    w = int(r["week"])
                except (KeyError, ValueError):
                    continue
                self.designation[(season, w, r["gsis_id"])] = r["report_status"]

    def team_code(self, code):
        return self.norm_team(GSIS_TEAM_ALIASES.get(code, code))

    # ---- the decision -----------------------------------------------------------------------------------------
    def has_week(self, season, week):
        """Whether there is WEEKLY roster data for (season, week) -- without it the feature is NULL for every row,
        the starter's own rows included (training always has a roster; a designations-only value would be a different
        feature). False for every season before WEEKLY_ROSTER_FIRST_SEASON, whose files are season-level."""
        return season >= WEEKLY_ROSTER_FIRST_SEASON and (season, week) in self.roster_weeks

    def absent(self, season, week, player_id, team):
        """True if player_id is known before kickoff not to play for `team` in (season, week); False if nothing says
        so; None if there is no roster data for that season-week at all."""
        if season < WEEKLY_ROSTER_FIRST_SEASON:
            return None                    # season-level roster files: unknown, not "available" (see the constant)
        live = getattr(self, "_live", None)
        if live is not None:
            if (season, week) != live[:2] or not self.has_week(season, week):
                return None
            return not live[2](player_id, self.norm_team(team))
        if self.designation.get((season, week, player_id)) in ABSENT_DESIGNATIONS:
            return True
        if (season, week) not in self.roster_weeks:
            return None
        row = self.roster.get((season, week, player_id))
        team = self.team_code(team)
        if row is None:
            # No row for the week: off every roster -- but only if his team has rows (a bye-week team has none).
            return True if (season, week, team) in self.teams_in_week else None
        rteam, status = row
        if status in ABSENT_ROSTER_STATUSES:
            return True
        if rteam is not None and rteam != team:
            return True
        return False
