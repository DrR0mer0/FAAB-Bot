#!/usr/bin/env python3
"""Score every eligible player for a target (season, week) using the saved
O.D.D.S. model, and print the top N by predicted spike probability.

Works for a not-yet-played week: eligibility and features are computed via
features_lib.FeatureEngine using only data strictly before the target week,
the same function used to build the historical training table.

If a current weekly roster file is available (nflverse_raw/roster_weekly_
<season>.csv -- see load_current_roster; NOT the season-level roster_
<season>.csv, which is stale between refreshes and only used elsewhere by
data/crosscheck_roster_2026.py), candidates are cross-referenced against
it. A confirmed offseason team change (vs. the team inferred from the
player's last game) gets its schedule-dependent features (opponent
matchup, home/away, short week, presumed-starter check) recomputed for
the corrected team -- but the model score is suppressed entirely, since
usage features (touches, touch share) still reflect the old team and
there's no real data yet for the new one. A meaningful new same-position
competitor (judged from 2025 production or 2026 draft capital) suppresses
the incumbent's score the same way, on the theory that their trailing
touch share predates the competition. Those players are reported
separately as watch lists, not folded into the ranking.

Both suppressions are released automatically, and identically, once a player
has >= features_lib.MIN_PRIOR_GAMES games in the CURRENT season: at that
point their trailing window (used for touches/touch-share/etc.) is built
entirely from this season's real data, so no stale prior-season usage feeds
any feature and the offseason concern no longer applies -- regardless of
what the static roster/production checks would otherwise say.

Team check, pool rule v4 (from 2026 week 8): the recency release above only
holds when EVERY game of the trailing window (the last MIN_PRIOR_GAMES games
the trailing features read, features_lib.trailing_window) was played for the
player's current weekly-roster team. Otherwise his usage features describe
another team, and he goes through the team-changed suppression -- schedule-
dependent features recomputed for the roster team, score suppressed --
whatever his current-season game count. Two reasons, stored apart:
team_changed_watch_list when his most recent game was for another team (the
v3 test, now also for a player with 3+ games this season, e.g. traded this
week), mixed_window_watch_list when his most recent game was for the roster
team but an earlier window game was not. Found 2026-10-27: Marvin Harrison
Jr. (ARI, ARI, HOU) was ranked in week 7 on two Arizona games; Dameon Pierce
(KC 2025, PHI, PHI) was production's #1 and Audric Estime (NO, NO, CLE) its
#25 on windows reaching into last season's team. A player whose roster team
the file can't say (no row, or a status outside CONFIRMED_ROSTER_STATUSES)
is not checked.

Availability gate: a player whose most recent weekly roster status (from
the same --weekly-roster file as team-mapping above, via load_current_
roster's status_of) indicates a known long-term absence -- reserve/IR,
PUP, non-football injury, or suspension -- is suppressed unconditionally,
checked BEFORE anything else in the suppression loop (including the
recency-release above -- a player accumulating real current-season games
doesn't matter if they're currently on IR and can't play). Game-day
designations are a separate check (below), not part of this gate. From 2026
week 8 (pool rule v4) Yahoo's current player status can overrule it:
EXE / RES / PUP / SUS suppresses a player unless Yahoo says he may play (no
status, Q, GTD; never D) -- then he is treated as available, subject to the
Out/Doubtful check, and listed in yahoo_status_override_list. A player Yahoo
says nothing about keeps the long-term status; ESPN never decides. The rule
is tracker/yahoo_status.py's; `log` collects the statuses (league roster
snapshot + the league-availability fetch) and passes them in with
--yahoo-status. Without it (a run by hand) the long-term status stands, as
in v3. Found when nflverse kept Josh Jacobs on the exempt list in week 7,
three days after his reinstatement. This is a separate
DECISION from team-mapping (a player on reserve still belongs to his
team) even though both now read the same file -- status_of is never
filtered the way team_of/team_pos_roster are, so it sees every status,
not just the "confirmed" ones.

Off-roster check (from 2026-10-15): a player who is not on any NFL roster --
his newest weekly row at or before the week says CUT or RET, he has no row
this season at all (an unsigned player who passes eligibility on last
season's games), or he has no row for the week although his last team does
(a team on a bye has no rows that week, so a missing row only counts when the
team HAS rows) -- is not scored and is listed in off_roster_watch_list. Runs
right after the bye check. Found when Andy Dalton, released by PHI in week 5,
was ranked in week 6 on his stats-history team. If the file would put more
than OFF_ROSTER_MAX_SHARE of the candidates off a roster it is treated as
broken: the check is NOT applied and a [ROSTER WARNING] is printed (the
tracker's `log` refuses on it). Practice-squad (DEV) players stay in -- an
open question, not a decision. EXE (exempt list) joins the availability gate.

Out/Doubtful check (from 2026-10-18): a player designated Out or Doubtful
on the week's NFL injury report at run time is not ranked and is listed in
injury_watch_list -- the tracker's pool rule, applied to the ranking too.
Questionable players stay in. The definition (which statuses, how old the
report may be, the nflverse source and the ESPN fallback) is
tracker/injuries.py's, shared with the tracker: `log` resolves the
designations once and passes them in with --injury-designations; a run by
hand resolves them itself (injuries.resolve_standalone); an --as-of run or
--skip-injuries applies none (a live report would be from the run's future).
Applied last, to the players who would otherwise be ranked, so a player left
out for another reason keeps it. Like a bye week, this matches training: the
historical table only holds player-weeks that were played (of 2,363
skill-position player-weeks designated Out in 2016-2024 none recorded a stat).
What it can't know: Sunday teams' designations before Friday (a Thursday run
only sees Thursday-game players) and game-day inactives.

check_roster_staleness() prints a loud, hard-to-miss warning (not a hard
failure) if --weekly-roster is more than a few days old on disk, or its
newest rows don't reach close to the week being scored -- a quiet stale
roster file is exactly how a two-week-old snapshot went unnoticed. Run
data/fetch_weekly_update.py before scoring each week to keep it current.

Shadow scoring: alongside the production model, the same candidate pool
(same eligibility, same suppression -- neither depends on which model does
the scoring) is ALSO scored with a second, fixed "shadow" model -- by
default odds_xgb_model_production_10feature.joblib, the pre-Group-1
production model -- so a head-to-head against the current production model
accumulates week over week (see evaluation/verify_week.py). Skipped with a
warning if the shadow model file isn't present locally (it's gitignored,
like every .joblib). Like the production file, the shadow prediction file
must be generated before the week is played and committed frozen -- never
regenerated afterward, same discipline throughout this pipeline.

No game, no score: a player whose team is not on the target week's
schedule (a bye) is excluded before anything else and listed in
`no_game_watch_list` with no score. He can't spike that week, and the
historical table the model was trained and evaluated on never contained
such a row -- it only holds player-weeks that were actually played -- so a
ranked bye-week player was a live-only artifact, and a guaranteed miss in
evaluation/verify_week.py. Added 2026-10-08 after week 5, the season's
first bye week, put three of them in the production top 10; that week's
first snapshot is frozen as it was (see the `pool_changes` marker in
predictions/verification_log.json). The team is resolved the same way the
kickoff guard resolves it (weekly-roster team first), and the check is
skipped entirely if the week's schedule isn't loaded. Because it runs
first, a bye-week player who would otherwise be on another watch list
(reserve, team change, new competitor) appears on this one instead.

Kickoff guard: a mid-week re-run (e.g. Sunday, after some early games have
already kicked off) must not score a player whose game has already started
-- the whole point of a spike-probability ranking is deciding who to add
*before* the outcome is known. Checked first, unconditionally, ahead of
even the availability gate -- resolved from each game's actual kickoff
instant in nfl_games (via `--as-of`, default now), never from day-of-week,
so a late-season Saturday game, a 9:30am ET London kickoff, or a holiday
game are all caught by the same rule. These players are excluded from
scoring entirely and reported in `already_played_watch_list`, with their
rank/score CARRIED FORWARD from the week's first snapshot if one exists --
never re-scored, since the whole premise of excluding them is that there's
nothing new to compute.

TIMEZONE NOTE on kickoff_utc: despite the column name, nfl_games.kickoff_utc
is NOT a true UTC instant -- it's `gameday` + `gametime` with no offset
(flagged in data/load_nflverse_into_history_SAFE_v3.py since this project's
early days). Checked directly against 2026 data before relying on it here:
it's consistently US/Eastern LOCAL time, regardless of the game's actual
venue -- nflverse's `gametime` column is always Eastern. Confirmed two
ways: every 2026 early-window game (the "1:00pm" slot) stores '13:00:00'
even when hosted by a Pacific-zone team (2026 week 17 KC @ LAC, week 18
SEA @ LA both do) -- a literal 1:00pm Pacific kickoff would be a very
unusual early-window slot, and if the column were true UTC it would read
'17:00' or '18:00' for a 1:00pm ET game, not '13:00'. And the lone 2026
London game (week 6, HOU @ JAX) stores '09:30:00' -- nflverse's documented
Eastern-denominated time for a London "breakfast" kickoff, not its ~14:30
UTC/BST local equivalent a true-UTC column would show. KICKOFF_TZ below
localizes the naive timestamp as US/Eastern via the IANA database (so it's
correct across the DST boundary the NFL season straddles -- EDT in
September, EST by January) and converts to a real UTC instant for
comparison against `--as-of`. If this ever stops holding for a future
season (a schema change upstream, say), the guard would silently compare
against the wrong instant -- there is no live check against that here.

SNAPSHOT NAMING: the first run for a week keeps the existing bare filename
(e.g. predictions/2026_week04.json) -- nothing changes there. A later
same-week run must pass a DIFFERENT --json-out so it doesn't overwrite the
first, by convention a snapshot label before the extension, e.g.
predictions/2026_week04_sunday.json; the existing stem-based shadow/
markdown derivation (`_shadow`, `.md`) already applies consistently to
whatever stem is given, so a later snapshot automatically gets
2026_week04_sunday_shadow.json, 2026_week04_sunday.md, and
2026_week04_sunday_shadow.md to match. Carry-forward (above) always reads
from the bare, UNsuffixed file for the matching role (production reads the
bare production file, shadow reads the bare shadow file) via
--predictions-dir, independent of where --json-out writes THIS run's
output -- so a dry run to a scratch location still carries forward from
the real, committed first snapshot. evaluation/verify_week.py is
unchanged and only ever reads the bare filename too, so it keeps treating
the first snapshot as the sole authoritative record for the running log
and the shadow head-to-head; comparing a later snapshot against the first
one directly would need restricting to their shared population first,
since the later snapshot's stable pool excludes whoever's already played.
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import joblib
import numpy as np

from features_lib import MIN_PRIOR_GAMES, PERSISTED_FEATURE_COLS, FeatureEngine
from model_metadata import read_metadata

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "data"))
sys.path.insert(0, str(REPO_ROOT / "tracker"))
from team_crosswalk import norm_team  # noqa: E402 -- shared crosswalk; see data/team_crosswalk.py
import injuries  # noqa: E402 -- the Out/Doubtful rule, shared with the tracker's pool rule
import yahoo_status  # noqa: E402 -- Yahoo's status against a long-term roster status (pool rule v4), shared likewise

# See the module docstring's TIMEZONE NOTE for how this was determined.
KICKOFF_TZ = ZoneInfo("America/New_York")

# Only these statuses represent a confirmed, current team assignment. CUT/RET/
# EXE players aren't actually on the team a roster file last associated them
# with, so they're excluded rather than asserting a bogus team.
CONFIRMED_ROSTER_STATUSES = {"ACT", "DEV", "RES"}
TEAM_CHANGE_REASON = "team changed -- his most recent game was for another team; insufficient data on new team"
MIXED_WINDOW_REASON = ("trailing window mixes teams -- his most recent game was for his current team but an earlier one "
                       "the trailing features read was not")
YAHOO_OVERRIDE_REASON = ("long-term weekly-roster status overruled: Yahoo's current status says he may play "
                         "(tracker/yahoo_status.py) -- treated as available, subject to the injury report")
NEW_COMPETITOR_REASON = "new same-position addition(s) this offseason — trailing touch share predates the competition"

# Roster statuses that mean "known unavailable for an extended stretch,
# not just this game" -- nflverse's own dictionary_roster_status.csv
# documents RES ("reserve list"), PUP, and RSN (non-football injury
# reserve) as separate status values, but in practice this data source
# bundles IR/PUP/NFI all under the single coarse "RES" code (confirmed by
# cross-referencing status_description_abbr sub-codes -- R01 dominates and
# was confirmed, via Jordan Mason, to mean IR). SUS (suspended) is
# included per the task; PUP/RSN are included defensively in case a future
# pull ever reports them as distinct top-level statuses. Does NOT include
# CUT/RET -- those are a different category (no longer on the team at all)
# and handled by the off-roster check below. EXE (the exempt list) is here
# since 2026-10-15: on a team, but not allowed to play.
UNAVAILABLE_STATUSES = {"RES", "PUP", "SUS", "RSN", "EXE"}
UNAVAILABLE_REASON = ("known long-term unavailable (reserve/IR, PUP, non-football injury, suspension, or exempt list) "
                      "per the most recent weekly roster")

# Not on any NFL roster (from 2026-10-15): released or retired as of the most
# recent weekly row at or before the week, no row at all this season (an
# unsigned player who passes eligibility on last season's games), or no row
# for the week although his last team has rows for it (dropped without a CUT
# row). A team with no rows for the week is on a bye, so a missing row is
# evidence only when the team HAS rows. Found 2026-10-15: Andy Dalton, released
# by PHI in week 5, was ranked in the week 6 pool on his stats-history team.
OFF_ROSTER_STATUSES = {"CUT", "RET"}
OFF_ROSTER_REASON = "not on an NFL roster per the weekly roster (released, retired, or unsigned) -- not scored"
# A file that would put more of the candidates than this off a roster is broken (2026-10-15: 20 of 372 pool
# players, ~5%), and the check is skipped with a [ROSTER WARNING] rather than gutting the pool.
OFF_ROSTER_MAX_SHARE = 0.25
OFF_ROSTER_WARNING_TAG = "[ROSTER WARNING]"
INJURY_REASON = "designated on the week's NFL injury report (see injury_rule) -- not ranked"

# Fantasy-relevant offensive positions only; IDP/O-line/K/P aren't rosterable
# in this league.
ROSTERABLE_POSITIONS = {"QB", "RB", "WR", "TE"}

# "Meaningful threat" bar for a new same-position arrival: either they
# produced at a real starter/committee-back level in 2025, or they carry
# first-few-round draft capital in the 2026 class (a Day 1-2 rookie is often
# a bigger threat to an incumbent's role than a marginal veteran). A rookie
# or futures signing with neither trips nothing.
PROD_THRESHOLDS = {"RB": 50, "WR": 30, "TE": 30, "QB": 100}  # touches/targets/targets/attempts
DRAFT_CAPITAL_PICK_CUTOFF = 96  # ~3 rounds at 32 picks/round

ALREADY_PLAYED_REASON = "game already kicked off as of this run -- not re-scored"
NO_GAME_REASON = "team has no game this week (bye) -- not scored"


def window_team_check(window_teams, roster_team):
    """Pool rule v4's team check (see the module docstring). window_teams: the team of each trailing-window game,
    oldest first; roster_team: his current weekly-roster team, or None if the file can't say.
    -> None (all on the roster team, or nothing to compare), 'team_changed' (most recent game for another team) or
    'mixed_window' (most recent on the roster team, an earlier one not)."""
    if not roster_team or not window_teams:
        return None
    current = norm_team(roster_team)
    teams = [norm_team(t) for t in window_teams]
    if teams[-1] != current:
        return "team_changed"
    if any(t != current for t in teams):
        return "mixed_window"
    return None


def classify_v4(roster_status, yahoo, window_teams, roster_team, games_this_season):
    """Pool rule v4's decisions for one player who has a game, is on a roster and hasn't kicked off, in the loop's
    order: the availability gate (Yahoo may overrule a long-term status), the team check, the recency release.
    -> (outcome, yahoo_overruled); outcome 'unavailable' | 'team_changed' | 'mixed_window' | 'released' (ranked)
    | None (on to the new-competitor check)."""
    overruled = False
    if roster_status in UNAVAILABLE_STATUSES:
        if not yahoo_status.overrides(roster_status, yahoo):
            return "unavailable", False
        overruled = True
    team = window_team_check(window_teams, roster_team)
    if team:
        return team, overruled
    if games_this_season >= MIN_PRIOR_GAMES:
        return "released", overruled
    return None, overruled


def parse_kickoff_utc(kickoff_str):
    """nfl_games.kickoff_utc -> a real, aware UTC datetime, or None if
    missing/unparseable. See the module docstring's TIMEZONE NOTE for why
    this localizes the naive string as US/Eastern (KICKOFF_TZ) rather than
    treating it as already being UTC despite the column name."""
    if not kickoff_str:
        return None
    try:
        naive = datetime.fromisoformat(kickoff_str)
    except ValueError:
        return None
    return naive.replace(tzinfo=KICKOFF_TZ).astimezone(timezone.utc)


def load_kickoffs(con, season, week):
    """team -> (kickoff_utc as an aware UTC datetime, opponent) for every
    non-playoff game in (season, week), keyed both home and away. Same
    is_playoffs=0 filter FeatureEngine's own game_info uses, and the same
    norm_team() crosswalk (nfl_games uses each franchise's historical
    code; player-facing team values elsewhere in this script already use
    the current one)."""
    kickoffs = {}
    for r in con.execute(
        "SELECT home_team, away_team, kickoff_utc FROM nfl_games WHERE season=? AND week=? AND is_playoffs=0",
        (season, week),
    ):
        dt = parse_kickoff_utc(r["kickoff_utc"])
        if dt is None:
            continue
        home, away = norm_team(r["home_team"]), norm_team(r["away_team"])
        kickoffs[home] = (dt, away)
        kickoffs[away] = (dt, home)
    return kickoffs


def load_scheduled_teams(con, season, week):
    """Every team with a non-playoff game in (season, week), in current team
    codes -- straight from the schedule's team columns, independent of whether
    a kickoff time is present or parseable. Empty if the week's schedule isn't
    loaded at all, in which case the no-game check below is skipped rather than
    excluding everybody."""
    teams = set()
    for r in con.execute("SELECT home_team, away_team FROM nfl_games WHERE season=? AND week=? AND is_playoffs=0", (season, week)):
        teams.add(norm_team(r["home_team"]))
        teams.add(norm_team(r["away_team"]))
    return teams


def build_already_played_records(already_played_base, names, baseline_path):
    """already_played_base: [(pid, feat, kickoff_dt, opponent), ...].
    Looks up each player's rank (from the baseline file's `top` list, if
    present there) and score (from its `scored_pool`, the broader stable
    pool) in baseline_path -- the week's FIRST, unsuffixed snapshot for
    this model's role (production or shadow; see the module docstring's
    SNAPSHOT NAMING note) -- and labels it explicitly as carried forward.
    No new score is ever computed here. carried_forward is None if
    baseline_path doesn't exist yet (this IS the first run, or kickoff
    beat even that), or if this specific player wasn't scored in it
    (e.g. they were suppressed there too)."""
    rank_by_pid, score_by_pid, baseline_name = {}, {}, None
    if baseline_path is not None and baseline_path.exists():
        baseline_name = baseline_path.name
        with open(baseline_path, encoding="utf-8") as f:
            baseline = json.load(f)
        rank_by_pid = {r["player_id"]: r["rank"] for r in baseline.get("top", [])}
        score_by_pid = {r["player_id"]: r["score"] for r in baseline.get("scored_pool", [])}

    records = []
    for pid, feat, kickoff_dt, opponent in already_played_base:
        rec = {
            "player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"], "team": feat["_team"],
            "kickoff": kickoff_dt.isoformat(), "opponent": opponent, "reason": ALREADY_PLAYED_REASON,
        }
        if pid in score_by_pid:
            rec["carried_forward"] = {
                "source": baseline_name, "score": score_by_pid[pid], "rank": rank_by_pid.get(pid),
            }
        else:
            rec["carried_forward"] = None
        records.append(rec)
    return records


def get_eligible_candidates(engine, season, week):
    """The season-gap reset in features_lib only catches *structural* holes in
    the loaded-season sequence (e.g. 2019, or 2025 when scoring into 2026);
    it says nothing about an individual player who simply stopped playing.
    2024->2025 has no such hole, so a long-retired veteran (Matt Ryan, Julio
    Jones, ...) whose last real game was years ago would otherwise still
    pass eligibility on stale data. Require a recent-enough appearance to be
    a plausible current-roster candidate."""
    recency_cutoff = season - 1
    candidates = []
    for pid, entries in engine.player_history.items():
        if entries[-1][0] < recency_cutoff:
            continue
        feat = engine.compute_features(season, week, pid)
        if feat is None:
            continue
        candidates.append((pid, feat))
    return candidates


def render_markdown_report(output, status_flags=None, flag_note=None):
    """Renders the same content already written to --json-out as a
    markdown report, matching the style of evaluation/verify_week.py's
    _verified.md reports so prediction and verification files read
    consistently. Takes exactly the dict shape written as JSON (or one
    read back from a previously-written file, e.g. model/render_prediction_
    report.py), so a report can be (re)rendered without recomputing or
    altering the JSON itself.

    status_flags ({player_id: label}, e.g. "OUT") and flag_note are DISPLAY
    ONLY: a flagged player's name gets the label next to it and the note is
    printed above the table. Scores, ranks, row order and the JSON are
    untouched, and with neither argument the output is exactly what it was
    before they existed. tracker/tracker.py's `log` passes the week's injury
    designations here; nothing in this script fetches or decides them."""
    counts = output["counts"]
    model = output.get("model") or {}
    lines = [
        f"# Predictions: {output['season']} week {output['week']}",
        "",
        f"Model: `{model.get('filename')}` (commit `{model.get('git_commit')}`)",
        "",
        f"Generated: {output.get('generated_at')}",
        "",
        f"- Total eligible: {counts['total_eligible']}",
        f"- After position filter (QB/RB/WR/TE): {counts['after_position_filter']}",
        f"- Stable scored: {counts['stable_scored']}"
        + (f" ({counts['released_from_offseason_suppression']} released from offseason suppression this week)"
           if counts.get("released_from_offseason_suppression") is not None else ""),
    ]
    if counts.get("unavailable_suppressed") is not None:
        lines.append(f"- Unavailable (reserve/PUP/NFI/suspended) suppressed: {counts['unavailable_suppressed']}")
    if counts.get("yahoo_status_overrides"):
        lines.append(f"- Long-term roster status overruled by Yahoo's current status (scored): {counts['yahoo_status_overrides']}")
    if counts.get("no_game"):
        lines.append(f"- No game this week (bye), not scored: {counts['no_game']}")
    if counts.get("off_roster"):
        lines.append(f"- Not on an NFL roster (released, retired or unsigned), not scored: {counts['off_roster']}")
    if counts.get("injury_out_doubtful"):
        lines.append(f"- Out/Doubtful on the injury report, not ranked: {counts['injury_out_doubtful']}")
    if counts.get("already_played") is not None:
        lines.append(f"- Already played as of this run (not re-scored, rank/score carried forward where available): "
                      f"{counts['already_played']}")
    lines += [
        f"- Team-changed suppressed: {counts['team_changed_suppressed']}",
        *([f"- Mixed-team trailing window suppressed: {counts['mixed_window_suppressed']}"]
          if counts.get("mixed_window_suppressed") is not None else []),
        f"- New-competitor suppressed: {counts['new_competitor_suppressed']} "
        f"({counts['new_competitor_via_production_only']} via production, "
        f"{counts['new_competitor_via_draft_only']} via draft, {counts['new_competitor_via_both']} via both)",
        "",
    ]
    if flag_note:
        lines += [flag_note, ""]
    lines += [
        "| Rank | Name | Pos | Team | Score |",
        "|---:|---|---|---|---:|",
    ]
    for r in output["top"]:
        flag = (status_flags or {}).get(r["player_id"])
        name = f"{r['name']} **({flag})**" if flag else r["name"]
        lines.append(f"| {r['rank']} | {name} | {r['pos']} | {r['team']} | {r['score']:.4f} |")
    withheld = output.get("injury_watch_list") or []
    if withheld:
        rule = output.get("injury_rule") or {}
        src = {"nflverse": "the NFL injury report (nflverse)", "espn-fallback": "ESPN's injury status (the official report "
               "was unavailable)"}.get(rule.get("source"), rule.get("source") or "the injury report")
        lines += ["", "## Out/Doubtful (not ranked)", "",
                  f"_Designated on {src} when this was generated; Questionable players are ranked as usual._", ""]
        lines += [f"- {r['name']} ({r['pos']}, {r['team']}): {r['status']}" for r in withheld]
    return "\n".join(lines) + "\n"


def load_current_roster(path, season, week):
    """Reads nflverse's weekly_rosters release (roster_weekly_<season>.csv)
    -- one row per player PER WEEK, unlike the season-level
    roster_<season>.csv (still fetched for data/crosscheck_roster_2026.py,
    but no longer read here) whose own `week` column means "the most
    recent week this player's entry was touched," which is ambiguous for
    someone quietly sitting on reserve. Filters to each player's status as
    of the most recent available week <= the target week -- unambiguous,
    no interpretation needed.

    Returns:
      team_of: gsis_id -> team, for CONFIRMED_ROSTER_STATUSES players only
               (team-change detection).
      team_pos_roster: (team, position) -> [gsis_id, ...], same filter
               (new-same-position-competitor detection).
      draft_pick_of: gsis_id -> overall pick number, rookies of this draft
               class only (rookie_year == season).
      status_of: gsis_id -> status, for EVERY player with a known row --
               deliberately NOT filtered to CONFIRMED_ROSTER_STATUSES,
               since the availability gate needs to see RES/PUP/SUS/RSN
               regardless of whether that status is "confirmed."
    """
    latest = {}  # gsis_id -> (week, row)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("season") != str(season):
                continue
            try:
                w = int(r.get("week") or 0)
            except ValueError:
                continue
            if w <= 0 or w > week:
                continue
            pid = r.get("gsis_id")
            if not pid:
                continue
            prev = latest.get(pid)
            if prev is None or w > prev[0]:
                latest[pid] = (w, r)

    team_of, team_pos_roster, draft_pick_of, status_of = {}, {}, {}, {}
    for pid, (_w, r) in latest.items():
        status = r.get("status")
        status_of[pid] = status
        if status not in CONFIRMED_ROSTER_STATUSES:
            continue
        team_of[pid] = r["team"]
        team_pos_roster.setdefault((r["team"], r["position"]), []).append(pid)
        if r.get("rookie_year") == str(season) and r.get("draft_number"):
            draft_pick_of[pid] = int(r["draft_number"])
    return team_of, team_pos_roster, draft_pick_of, status_of


def load_roster_presence(path, season, week):
    """(latest, teams_in_week) from the weekly roster file: latest maps every
    gsis_id with a row this season to (week, status, team) of its newest row at
    or before `week`; teams_in_week is the set of teams with any row for
    `week` itself (a team on a bye has none)."""
    latest, teams_in_week = {}, set()
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("season") != str(season):
                continue
            try:
                w = int(r.get("week") or 0)
            except ValueError:
                continue
            if w <= 0 or w > week:
                continue
            if w == week:
                teams_in_week.add(r.get("team"))
            pid = r.get("gsis_id")
            if pid and (pid not in latest or w > latest[pid][0]):
                latest[pid] = (w, r.get("status"), r.get("team"))
    return latest, teams_in_week


def off_roster_reason(pid, latest, teams_in_week, week):
    """Why a player is not on an NFL roster for `week`, or None if he is (or
    the file can't say). See OFF_ROSTER_STATUSES. Shared with the tracker's
    pool rule (tracker.split_pool_off_roster)."""
    row = latest.get(pid)
    if row is None:
        return "no roster row this season"
    w, status, team = row
    if status in OFF_ROSTER_STATUSES:
        return f"{status} (week {w}, {team})"
    if w < week and team in teams_in_week:
        return f"no week-{week} row; last row week {w} {status} {team}, which has week-{week} rows"
    return None


STALE_ROSTER_DAYS = 3


def check_roster_staleness(path, season, week):
    """Loud, hard-to-miss warning if roster_weekly_<season>.csv looks
    stale -- either the file on disk hasn't been refreshed recently, or it
    simply has no rows anywhere near the week being scored. A quiet stale
    file is exactly how the team-changed/new-competitor checks ran on a
    two-week-old snapshot unnoticed (see CLAUDE.md) -- this makes it
    impossible to miss on the console, without refusing to run."""
    if not os.path.exists(path):
        return

    age_days = (time.time() - os.path.getmtime(path)) / 86400
    max_week_in_file = 0
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("season") != str(season):
                continue
            try:
                w = int(r.get("week") or 0)
            except ValueError:
                continue
            max_week_in_file = max(max_week_in_file, w)

    problems = []
    if age_days > STALE_ROSTER_DAYS:
        problems.append(f"file on disk is {age_days:.1f} days old (> {STALE_ROSTER_DAYS}-day threshold)")
    if max_week_in_file < week - 1:
        problems.append(f"newest rows in the file are for week {max_week_in_file}, but scoring week {week} "
                         f"(expected rows at least through week {week - 1})")

    if problems:
        banner = "!" * 78
        print(f"\n{banner}")
        print(f"[STALE ROSTER WARNING] {path}")
        for p in problems:
            print(f"  - {p}")
        print("  Team-changed / new-competitor / availability suppressions below may be WRONG.")
        print(f"  Fix: refresh with data/fetch_weekly_update.py --season {season} before trusting this output.")
        print(f"{banner}\n")


def load_and_score(model_path, label, stable):
    """Loads a model bundle, scores the shared `stable` candidate pool with
    it, and returns (model_commit, model_meta, feature_cols, proba, ranked)
    -- or None if model_path doesn't exist (used for an optional shadow
    model). `label` is only for console messages, e.g. "PRODUCTION" or
    "SHADOW"."""
    if not os.path.exists(model_path):
        return None

    bundle = joblib.load(model_path)
    model = bundle["model"]
    feature_cols = bundle["feature_cols"]
    # The saved model may use any subset of PERSISTED_FEATURE_COLS (e.g. the
    # production model trains on features_lib.PRODUCTION_FEATURE_COLS, a
    # strict subset that excludes the rejected share_delta_vs_prior_season --
    # see features_lib.py for why) -- just check it's a real subset, not an
    # exact match.
    unknown = set(feature_cols) - set(PERSISTED_FEATURE_COLS)
    assert not unknown, f"saved {label} model uses unknown feature columns: {unknown}"
    print(f"[INFO] {label} model ({Path(model_path).name}) uses {len(feature_cols)} feature(s): {feature_cols}")

    model_meta = read_metadata(model_path)
    model_commit = model_meta.get("git_commit") if model_meta else None
    print(f"[INFO] {label} model identity: {Path(model_path).name}"
          + (f" (git_commit={model_commit})" if model_commit else " (no metadata sidecar found -- commit hash unknown)"))

    X = np.array([[c[1][col] if c[1][col] is not None else np.nan for col in feature_cols] for c in stable])
    proba = model.predict_proba(X)[:, 1] if len(stable) else np.array([])
    return model_commit, model_meta, feature_cols, proba


def print_top_table(label, ranked, args, names):
    print(f"\n[{label}] Top {len(ranked)} by predicted spike probability, {args.season} week {args.week} "
          f"(stable candidates only, both flags clear):")
    header = f"{'Name':24} {'Pos':4} {'Team':5} {'Score':>7}"
    print(header)
    print("-" * len(header))
    for (pid, feat), score in ranked:
        display = names.get(pid, pid)
        print(f"{display:24.24} {feat['_pos']:4} {feat['_team']:5} {score:7.4f}")


def write_output(model_path, model_commit, model_meta, args, names, ranked, stable, proba,
                  all_eligible_count, n_candidates, n_released_by_recency,
                  team_changed_records, new_competitor_records, unavailable_records,
                  already_played_records, no_game_records, off_roster_records, injury_records, injury_rule,
                  n_via_production_only, n_via_draft_only, n_via_both, json_out_path,
                  mixed_window_records, yahoo_override_records, yahoo_summary):
    def player_record(pid, feat, score=None):
        return {
            "player_id": pid, "name": names.get(pid, pid),
            "pos": feat["_pos"], "team": feat["_team"],
            "score": float(score) if score is not None else None,
        }

    ranked_records = []
    for rank, ((pid, feat), score) in enumerate(ranked, start=1):
        rec = player_record(pid, feat, score)
        rec["rank"] = rank
        ranked_records.append(rec)

    scored_pool_records = [
        player_record(pid, feat, s)
        for (pid, feat), s in sorted(zip(stable, proba), key=lambda x: x[1], reverse=True)
    ]

    output = {
        "season": args.season,
        "week": args.week,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "path": str(Path(model_path).resolve()),
            "filename": Path(model_path).name,
            "git_commit": model_commit,
            "role": model_meta.get("role") if model_meta else None,
        },
        "counts": {
            "total_eligible": all_eligible_count,
            "after_position_filter": n_candidates,
            "stable_scored": len(stable),
            "released_from_offseason_suppression": n_released_by_recency,
            "unavailable_suppressed": len(unavailable_records),
            "no_game": len(no_game_records),
            "off_roster": len(off_roster_records),
            "injury_out_doubtful": len(injury_records),
            "already_played": len(already_played_records),
            "team_changed_suppressed": len(team_changed_records),
            "mixed_window_suppressed": len(mixed_window_records),
            "yahoo_status_overrides": len(yahoo_override_records),
            "new_competitor_suppressed": len(new_competitor_records),
            "new_competitor_via_production_only": n_via_production_only,
            "new_competitor_via_draft_only": n_via_draft_only,
            "new_competitor_via_both": n_via_both,
        },
        "top": ranked_records,
        "scored_pool": scored_pool_records,
        "unavailable_watch_list": unavailable_records,
        "no_game_watch_list": no_game_records,
        "off_roster_watch_list": off_roster_records,
        "injury_watch_list": injury_records,
        "injury_rule": {k: v for k, v in injury_rule.items() if k != "excluded"},
        "already_played_watch_list": already_played_records,
        "team_changed_watch_list": team_changed_records,
        "mixed_window_watch_list": mixed_window_records,
        "new_competitor_watch_list": new_competitor_records,
        "yahoo_status_override_list": yahoo_override_records,
        "yahoo_status": yahoo_summary,
    }

    json_out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"[SAVED] scoring output written to {json_out_path}")

    md_out_path = json_out_path.with_suffix(".md")
    md_out_path.write_text(render_markdown_report(output), encoding="utf-8")
    print(f"[SAVED] markdown report written to {md_out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--model", default=str(REPO_ROOT / "odds_xgb_model_production.joblib"))
    ap.add_argument("--shadow-model", default=str(REPO_ROOT / "odds_xgb_model_production_10feature.joblib"),
                     help="also score the same candidate pool with this model, for a head-to-head "
                          "tracked in verify_week.py; skipped with a warning if the file isn't present locally")
    ap.add_argument("--no-shadow", action="store_true", help="skip shadow scoring even if --shadow-model exists")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--weekly-roster", default=None,
                     help="defaults to nflverse_raw/roster_weekly_<season>.csv if present; single source for "
                          "team-mapping, new-competitor detection, AND the availability gate")
    ap.add_argument("--json-out", default=None, help="if set, write the full scoring output (top N, scored pool, both watch lists, model identity) as JSON to this path, and a matching shadow file (same basename + _shadow) if shadow scoring runs")
    ap.add_argument("--predictions-dir", default=str(REPO_ROOT / "predictions"),
                     help="where to look for the week's FIRST (bare, unsuffixed) snapshot when carrying forward "
                          "already-played players' rank/score -- independent of --json-out, so a dry run to a "
                          "scratch location still carries forward from the real committed baseline")
    ap.add_argument("--injury-designations", default=None,
                    help="the week's Out/Doubtful designations as resolved by tracker/tracker.py log (a JSON file from "
                         "injuries.write_designations); without it they are resolved here, from the same sources")
    ap.add_argument("--skip-injuries", action="store_true", help="apply no Out/Doubtful check (recorded as skipped)")
    ap.add_argument("--yahoo-status", default=None,
                    help="Yahoo's current player statuses as collected by tracker/tracker.py log (a JSON file from "
                         "yahoo_status.write); without it a long-term roster status is never overruled (as in pool rule v3)")
    ap.add_argument("--as-of", default=None,
                     help="ISO8601 instant to evaluate the kickoff guard against (default: now; naive input is "
                          "assumed UTC). For testing, e.g. --as-of 2026-10-05T14:00:00+00:00 to simulate a "
                          "Sunday-afternoon run after early games have kicked off")
    args = ap.parse_args()

    if args.as_of:
        as_of = datetime.fromisoformat(args.as_of)
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of = datetime.now(timezone.utc)

    weekly_roster_path = args.weekly_roster or str(REPO_ROOT / "nflverse_raw" / f"roster_weekly_{args.season}.csv")
    roster_team_of, team_pos_roster, draft_pick_of, weekly_status_of = {}, {}, {}, {}
    roster_latest, roster_teams_in_week = {}, set()
    if os.path.exists(weekly_roster_path):
        check_roster_staleness(weekly_roster_path, args.season, args.week)
        roster_team_of, team_pos_roster, draft_pick_of, weekly_status_of = load_current_roster(
            weekly_roster_path, args.season, args.week
        )
        roster_latest, roster_teams_in_week = load_roster_presence(weekly_roster_path, args.season, args.week)
        n_flagged_unavailable = sum(1 for s in weekly_status_of.values() if s in UNAVAILABLE_STATUSES)
        print(f"[INFO] cross-referencing against {weekly_roster_path}: {len(roster_team_of)} players with a "
              f"confirmed current team, {len(draft_pick_of)} rookies with known {args.season}-draft pick numbers, "
              f"{n_flagged_unavailable} flagged long-term unavailable")
    else:
        print(f"[INFO] no weekly roster file at {weekly_roster_path} -- skipping offseason team-change / "
              f"new-competitor cross-checks and the availability gate")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    engine = FeatureEngine(con)

    names = {row["player_id"]: row["full_name"] for row in con.execute("SELECT player_id, full_name FROM ref_players")}

    kickoffs = load_kickoffs(con, args.season, args.week)
    scheduled_teams = load_scheduled_teams(con, args.season, args.week)
    print(f"[INFO] kickoff guard evaluated as-of {as_of.isoformat()} against {len(kickoffs) // 2} "
          f"scheduled game(s) for {args.season} week {args.week}")

    # Not to be confused with the per-player kickoff guard below (also
    # called "already played" in this script, but a different thing: this
    # is week-level -- does ANY stat data exist for this week at all --
    # while the guard is per-player and based on actual kickoff instants.
    week_has_any_stat_data = bool(engine.played_this_week.get((args.season, args.week)))
    print(f"[INFO] {args.season} week {args.week}: "
          f"{'ALREADY HAS stat data (historical/backtest mode)' if week_has_any_stat_data else 'no stat data yet (genuine future-week mode)'}")

    candidates = get_eligible_candidates(engine, args.season, args.week)
    all_eligible_count = len(candidates)
    candidates = [(pid, feat) for pid, feat in candidates if feat["_pos"] in ROSTERABLE_POSITIONS]
    print(f"[INFO] {all_eligible_count} eligible players for {args.season} week {args.week}; "
          f"{len(candidates)} after restricting to {sorted(ROSTERABLE_POSITIONS)}")

    if not candidates:
        print("[DONE] no eligible players -- nothing to score.")
        con.close()
        return

    # A player is "on team T's 2025 roster" if they have a real 2025 game for
    # T -- used to detect a same-position teammate who is genuinely new to
    # the team this offseason (free agent signing, trade, or rookie), not
    # just someone who happened to change teams themselves.
    prior_season = args.season - 1
    players_on_team_2025 = {}
    rb_touches_2025, wr_te_targets_2025 = {}, {}
    for pid, entries in engine.player_history.items():
        for (s, _w, tm, pos, touches) in entries:
            if s != prior_season:
                continue
            players_on_team_2025.setdefault(tm, set()).add(pid)
            if pos == "RB":
                rb_touches_2025[pid] = rb_touches_2025.get(pid, 0) + touches

    # rec_tgt (targets) isn't part of the "touches" tuple in player_history,
    # so pull it straight from player_week_stats for WR/TE.
    for pid, tgt in con.execute(
        "SELECT player_id, SUM(rec_tgt) FROM player_week_stats WHERE season=? AND pos IN ('WR','TE') GROUP BY player_id",
        (prior_season,),
    ):
        wr_te_targets_2025[pid] = tgt or 0

    # pass attempts aren't in player_week_stats at all (never needed by the
    # half12 scoring profile) -- read them from the raw fetched prior-season
    # CSV instead of extending the core schema for one threshold check.
    qb_attempts_2025 = {}
    raw_2025_path = str(REPO_ROOT / "nflverse_raw" / f"stats_player_week_{prior_season}.csv")
    if os.path.exists(raw_2025_path):
        with open(raw_2025_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("attempts"):
                    pid2 = r["player_id"]
                    qb_attempts_2025[pid2] = qb_attempts_2025.get(pid2, 0) + int(r["attempts"])

    def meaningful_threat(pid2, pos2):
        """Returns (is_threat, via_production, via_draft)."""
        prod_bar = PROD_THRESHOLDS.get(pos2)
        if pos2 == "RB":
            prod_val = rb_touches_2025.get(pid2, 0)
        elif pos2 in ("WR", "TE"):
            prod_val = wr_te_targets_2025.get(pid2, 0)
        elif pos2 == "QB":
            prod_val = qb_attempts_2025.get(pid2, 0)
        else:
            prod_val = 0
        via_production = prod_bar is not None and prod_val >= prod_bar
        pick = draft_pick_of.get(pid2)
        via_draft = pick is not None and pick <= DRAFT_CAPITAL_PICK_CUTOFF
        return (via_production or via_draft), via_production, via_draft

    # Out/Doubtful designations (tracker/injuries.py's rule; see the module docstring)
    if args.injury_designations:
        injury_rule = injuries.read_designations(args.injury_designations)
    elif args.skip_injuries or args.as_of:
        injury_rule = injuries.skipped_record("--skip-injuries" if args.skip_injuries else "--as-of run: no live injury report",
                                              injuries.rule()[0], "score_week")
    else:
        injury_rule = injuries.resolve_standalone(con, args.season, args.week, as_of)
    injury_out = injuries.excluded_ids(injury_rule["excluded"], injury_rule["exclude"])
    print(f"[INFO] injury designations: source {injury_rule['source']} (from {injury_rule.get('provided_by')})"
          + (f" -- {injury_rule['reason']}" if injury_rule.get("reason") else "")
          + f"; {len(injury_out)} player(s) designated {'/'.join(injury_rule['exclude'])}")

    # Yahoo's current statuses (pool rule v4; tracker/yahoo_status.py): may overrule a long-term roster status
    if args.yahoo_status:
        yahoo_rec = yahoo_status.read(args.yahoo_status)
    else:
        yahoo_rec = yahoo_status.skipped("not provided (score_week.py run by hand): long-term roster statuses stand")
    yahoo_of = yahoo_rec["status_by_player"]
    print(f"[INFO] Yahoo statuses: {len(yahoo_of)} player(s) (rostered: "
          + (f"snapshot {yahoo_rec['rostered']['snapshot_id']} taken {yahoo_rec['rostered']['taken_at']}"
             if yahoo_rec["rostered"].get("used") else yahoo_rec["rostered"].get("reason"))
          + f"; available: {yahoo_rec['available']['status']}, {yahoo_rec['available']['n']})")
    for code in yahoo_rec["unknown_codes"]:
        print(f"[WARN] unknown Yahoo status {code!r} -- it never overrules a roster status; add it to yahoo_status.py")

    stable, team_changed_list, new_competitor_list, unavailable_list, already_played_list = [], [], [], [], []
    mixed_window_list, yahoo_override_list = [], []
    no_game_list = []
    off_roster_list = []
    injury_list = []
    if roster_latest:
        would_drop = sum(1 for pid, _feat in candidates if off_roster_reason(pid, roster_latest, roster_teams_in_week, args.week))
        if would_drop > OFF_ROSTER_MAX_SHARE * len(candidates):
            print(f"{OFF_ROSTER_WARNING_TAG} {weekly_roster_path} would put {would_drop} of {len(candidates)} candidates off an "
                  f"NFL roster (more than {OFF_ROSTER_MAX_SHARE:.0%}) -- it looks incomplete, so the off-roster check was NOT "
                  f"applied. Refresh it with data/fetch_weekly_update.py.")
            roster_latest = {}
    n_via_production_only = n_via_draft_only = n_via_both = 0
    n_released_by_recency = 0
    for pid, feat in candidates:
        # No game, no score: a player whose team isn't on this week's schedule
        # (a bye) can't spike, and the historical table the model was trained
        # and evaluated on never contained such a row -- it only has
        # player-weeks that were actually played. Same team resolution as the
        # kickoff guard below. Skipped if the week's schedule isn't loaded.
        if scheduled_teams and (roster_team_of.get(pid) or feat["_team"]) not in scheduled_teams:
            no_game_list.append((pid, feat))
            continue

        # Not on an NFL roster: released, retired or unsigned (see
        # OFF_ROSTER_STATUSES). Only with a weekly roster file for the season;
        # without one nothing can be said and nobody is dropped.
        off_reason = off_roster_reason(pid, roster_latest, roster_teams_in_week, args.week) if roster_latest else None
        if off_reason:
            off_roster_list.append((pid, feat, off_reason))
            continue

        # Kickoff guard first, unconditionally -- ahead of even the
        # availability gate below. Whatever team this player is ACTUALLY on
        # (roster_team_of, same resolution the team-changed check below
        # uses) determines which game's kickoff applies; a team on a bye
        # this week has no entry in `kickoffs` and the guard is simply a
        # no-op for it. See the module docstring's TIMEZONE NOTE for how
        # kickoff_dt was resolved to a real UTC instant.
        kickoff_info = kickoffs.get(roster_team_of.get(pid) or feat["_team"])
        if kickoff_info is not None:
            kickoff_dt, opponent = kickoff_info
            if kickoff_dt <= as_of:
                already_played_list.append((pid, feat, kickoff_dt, opponent))
                continue


        # Availability gate next, unconditionally -- ahead of even the
        # recency-release below. A player on reserve who happens to have
        # accumulated 3+ real current-season games (hurt mid-season, say)
        # still can't play; games_played_this_season says nothing about
        # whether he's currently allowed on the field. Pool rule v4: Yahoo's
        # current status may overrule an EXE / RES / PUP / SUS (classify_v4).
        #
        # Then the team check (v4): the recency release -- a trailing window
        # built entirely from current-season games -- only holds when every
        # game in it was played for his current weekly-roster team; any
        # window game for another team sends him to the team-changed
        # suppression, whatever his current-season game count.
        roster_status = weekly_status_of.get(pid)
        yahoo = yahoo_of.get(pid)
        inferred_team = feat["_team"]
        roster_team = roster_team_of.get(pid)
        if roster_team is None and yahoo_status.overrides(roster_status, yahoo) and pid in roster_latest:
            roster_team = roster_latest[pid][2]   # exempt / suspended / PUP: not "confirmed", but still on that team
        window_teams = [h[2] for h in engine.trailing_window(pid, args.season, args.week)]
        outcome, overruled = classify_v4(roster_status, yahoo, window_teams, roster_team, feat["games_played_this_season"])
        if outcome == "unavailable":
            unavailable_list.append((pid, feat, roster_status, yahoo))
            continue
        if overruled:
            yahoo_override_list.append((pid, feat, roster_status, yahoo, roster_team))
        if outcome in ("team_changed", "mixed_window"):
            corrected = engine.compute_schedule_dependent_features(args.season, args.week, roster_team, feat["_pos"], pid)
            (team_changed_list if outcome == "team_changed" else mixed_window_list).append(
                (pid, feat, inferred_team, roster_team, corrected, window_teams))
            continue
        if outcome == "released":
            stable.append((pid, feat))
            n_released_by_recency += 1
            continue

        team = roster_team or inferred_team
        pos = feat["_pos"]
        new_arrivals = [
            p2 for p2 in team_pos_roster.get((team, pos), [])
            if p2 != pid and p2 not in players_on_team_2025.get(team, set())
        ]

        meaningful, any_prod, any_draft = [], False, False
        for p2 in new_arrivals:
            is_threat, via_prod, via_draft = meaningful_threat(p2, pos)
            if is_threat:
                meaningful.append(p2)
                any_prod = any_prod or via_prod
                any_draft = any_draft or via_draft

        if meaningful:
            new_competitor_list.append((pid, feat, team, meaningful))
            if any_prod and any_draft:
                n_via_both += 1
            elif any_prod:
                n_via_production_only += 1
            else:
                n_via_draft_only += 1
        else:
            stable.append((pid, feat))

    # Out or Doubtful on the week's injury report: not ranked (tracker/injuries.py's rule, shared with the tracker's
    # pool rule). Applied LAST, to the players who would otherwise be ranked: a player already left out for another
    # reason (bye, off roster, game started, reserve list, offseason suppression) keeps that reason, so
    # injury_watch_list means "ranked but for the injury report" -- the same set the tracker's pool rule excluded
    # before score_week.py applied it.
    injury_list = [(pid, feat, injury_out[pid]) for pid, feat in stable if pid in injury_out]
    stable = [(pid, feat) for pid, feat in stable if pid not in injury_out]

    print(f"[INFO] {len(stable)} stable candidates scored ({n_released_by_recency} of those released from "
          f"offseason suppression this week, having reached {MIN_PRIOR_GAMES}+ current-season games); "
          f"{len(no_game_list)} with no game this week (bye, excluded); "
          f"{len(off_roster_list)} not on an NFL roster (excluded); "
          f"{len(injury_list)} Out/Doubtful (excluded); "
          f"{len(already_played_list)} already played as of {as_of.isoformat()} (excluded, not re-scored); "
          f"{len(unavailable_list)} unavailable (reserve/PUP/NFI/suspended/exempt, score suppressed); "
          f"{len(team_changed_list)} team-changed (score suppressed); "
          f"{len(mixed_window_list)} with a mixed-team trailing window (score suppressed); "
          f"{len(yahoo_override_list)} long-term roster status(es) overruled by Yahoo; "
          f"{len(new_competitor_list)} with a meaningful new same-position competitor this offseason (score suppressed)")
    print(f"[INFO]   of those {len(new_competitor_list)}: {n_via_production_only} via prior-production threshold only, "
          f"{n_via_draft_only} via draft-capital only, {n_via_both} via both")

    if already_played_list:
        print(f"\nWatch list -- {len(already_played_list)} players whose game has already kicked off, not re-scored ({ALREADY_PLAYED_REASON}):")
        header0 = f"{'Name':24} {'Pos':4} {'Team':5} {'Opp':4} {'Kickoff (UTC)'}"
        print(header0)
        print("-" * len(header0))
        for pid, feat, kickoff_dt, opponent in sorted(already_played_list, key=lambda w: (w[2], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            print(f"{display:24.24} {feat['_pos']:4} {feat['_team']:5} {opponent:4} {kickoff_dt.isoformat()}")

    if unavailable_list:
        print(f"\nWatch list -- {len(unavailable_list)} players known long-term unavailable, score suppressed ({UNAVAILABLE_REASON}):")
        header1 = f"{'Name':24} {'Pos':4} {'Team':5} {'Status'}"
        print(header1)
        print("-" * len(header1))
        for pid, feat, status, yahoo in sorted(unavailable_list, key=lambda w: (w[1]["_team"], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            print(f"{display:24.24} {feat['_pos']:4} {feat['_team']:5} {status}"
                  + (f"  (Yahoo: {yahoo or 'no status'})" if yahoo is not None and status in yahoo_status.OVERRIDABLE else ""))

    if yahoo_override_list:
        print(f"\nOverruled by Yahoo -- {len(yahoo_override_list)} players whose long-term roster status Yahoo contradicts, "
              f"treated as available ({YAHOO_OVERRIDE_REASON}):")
        for pid, feat, status, yahoo, team in sorted(yahoo_override_list, key=lambda w: names.get(w[0], w[0])):
            print(f"{names.get(pid, pid):24.24} {feat['_pos']:4} {team or feat['_team']:5} weekly roster {status}, "
                  f"Yahoo {yahoo or 'no status'}")

    if team_changed_list:
        print(f"\nWatch list -- {len(team_changed_list)} players whose most recent game was for another team, score suppressed ({TEAM_CHANGE_REASON}):")
        header2 = f"{'Name':24} {'Pos':4} {'Old':4} {'New':4} {'Opp':4} {'Home':5} {'ShortWk':7} {'StarterAbsent':13} {'Matchup':>8}"
        print(header2)
        print("-" * len(header2))
        for pid, feat, old_team, new_team, corrected, _w in sorted(team_changed_list, key=lambda w: (w[3], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            opp = corrected["_opp"] or "?"
            home = corrected["is_home"]
            sw = corrected["is_short_week"]
            sap = corrected["starter_absent_proxy"]
            matchup = corrected["opponent_position_matchup"]
            matchup_s = f"{matchup:.2f}" if matchup is not None else "None"
            print(f"{display:24.24} {feat['_pos']:4} {old_team:4} {new_team:4} {opp:4} {str(home):5} {str(sw):7} {str(sap):13} {matchup_s:>8}")

    if mixed_window_list:
        print(f"\nWatch list -- {len(mixed_window_list)} players whose trailing window mixes teams, score suppressed "
              f"({MIXED_WINDOW_REASON}):")
        for pid, feat, _old, new_team, _corrected, window in sorted(mixed_window_list, key=lambda w: (w[3], names.get(w[0], w[0]))):
            print(f"{names.get(pid, pid):24.24} {feat['_pos']:4} window {', '.join(window)} -> now {new_team}")

    if new_competitor_list:
        print(f"\nWatch list -- {len(new_competitor_list)} players with a meaningful new same-position competitor this offseason, score suppressed ({NEW_COMPETITOR_REASON}):")
        header3 = f"{'Name':24} {'Pos':4} {'Team':5} {'New competitor(s) [why]'}"
        print(header3)
        print("-" * len(header3))
        for pid, feat, team, competitors in sorted(new_competitor_list, key=lambda w: (w[2], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            comp_strs = []
            for c in competitors:
                _, via_prod, via_draft = meaningful_threat(c, feat["_pos"])
                why = "prod+draft" if (via_prod and via_draft) else ("draft" if via_draft else "prod")
                comp_strs.append(f"{names.get(c, c)} [{why}]")
            print(f"{display:24.24} {feat['_pos']:4} {team:5} {', '.join(comp_strs)}")

    # Watch-list JSON records are model-independent (same suppression logic
    # regardless of which model scores the stable pool) -- built once, reused
    # for both the production and shadow output files.
    no_game_records = [
        {"player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"],
         "team": roster_team_of.get(pid) or feat["_team"], "reason": NO_GAME_REASON}
        for pid, feat in no_game_list
    ]
    injury_records = [
        {"player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"], "team": roster_team_of.get(pid) or feat["_team"],
         "status": status, "reason": INJURY_REASON}
        for pid, feat, status in sorted(injury_list, key=lambda x: (x[2], names.get(x[0], x[0])))
    ]
    off_roster_records = [
        {"player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"], "team": feat["_team"],
         "roster": why, "reason": OFF_ROSTER_REASON}
        for pid, feat, why in off_roster_list
    ]

    unavailable_records = []
    for pid, feat, status, yahoo in unavailable_list:
        unavailable_records.append({
            "player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"], "team": feat["_team"],
            "status": status, "yahoo_status": yahoo, "reason": UNAVAILABLE_REASON,
        })

    yahoo_override_records = [
        {"player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"], "team": team or feat["_team"],
         "weekly_roster_status": status, "yahoo_status": yahoo, "reason": YAHOO_OVERRIDE_REASON}
        for pid, feat, status, yahoo, team in sorted(yahoo_override_list, key=lambda w: names.get(w[0], w[0]))]

    def team_record(pid, feat, old_team, new_team, corrected, window, reason):
        return {
            "player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"],
            "old_team": old_team, "new_team": new_team, "window_teams": window,
            "reason": reason,
            "corrected_features": {
                "opponent": corrected["_opp"], "is_home": corrected["is_home"],
                "is_short_week": corrected["is_short_week"],
                "starter_absent_proxy": corrected["starter_absent_proxy"],
                "opponent_position_matchup": corrected["opponent_position_matchup"],
            },
        }

    team_changed_records = [team_record(*t, TEAM_CHANGE_REASON) for t in team_changed_list]
    mixed_window_records = [team_record(*t, MIXED_WINDOW_REASON) for t in mixed_window_list]

    new_competitor_records = []
    for pid, feat, team, competitors in new_competitor_list:
        comp_details = []
        for c in competitors:
            _, via_prod, via_draft = meaningful_threat(c, feat["_pos"])
            comp_details.append({
                "player_id": c, "name": names.get(c, c),
                "via_production": via_prod, "via_draft_capital": via_draft,
            })
        new_competitor_records.append({
            "player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"], "team": team,
            "reason": NEW_COMPETITOR_REASON,
            "new_competitors": comp_details,
        })

    # The week's FIRST (bare, unsuffixed) snapshot for each role, independent
    # of where THIS run's --json-out points -- see the module docstring's
    # SNAPSHOT NAMING note. Used only to carry forward already-played
    # players' rank/score; never read for anything else.
    baseline_production_path = Path(args.predictions_dir) / f"{args.season}_week{args.week:02d}.json"
    baseline_shadow_path = baseline_production_path.with_name(
        baseline_production_path.stem + "_shadow" + baseline_production_path.suffix
    )

    def run_pass(model_path, label, json_out_path, baseline_path):
        result = load_and_score(model_path, label, stable)
        if result is None:
            print(f"[WARN] {label} model not found at {model_path} -- skipping {label.lower()} scoring")
            return
        model_commit, model_meta, feature_cols, proba = result
        ranked = sorted(zip(stable, proba), key=lambda x: x[1], reverse=True)[: args.top]
        print_top_table(label, ranked, args, names)
        if json_out_path is not None:
            already_played_records = build_already_played_records(already_played_list, names, baseline_path)
            write_output(model_path, model_commit, model_meta, args, names, ranked, stable, proba,
                         all_eligible_count, len(candidates), n_released_by_recency,
                         team_changed_records, new_competitor_records, unavailable_records,
                         already_played_records, no_game_records, off_roster_records, injury_records, injury_rule,
                         n_via_production_only, n_via_draft_only, n_via_both, json_out_path,
                         mixed_window_records, yahoo_override_records, yahoo_status.summary(yahoo_rec))

    json_out_path = Path(args.json_out) if args.json_out else None
    run_pass(args.model, "PRODUCTION", json_out_path, baseline_production_path)

    if not args.no_shadow:
        shadow_json_out = json_out_path.with_name(json_out_path.stem + "_shadow" + json_out_path.suffix) if json_out_path else None
        run_pass(args.shadow_model, "SHADOW", shadow_json_out, baseline_shadow_path)

    con.close()


if __name__ == "__main__":
    main()
