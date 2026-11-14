#!/usr/bin/env python3
"""The "thin window" flag on score_week.py's scored players. DISPLAY ONLY (from 2026-11-12).

A player is only scored with MIN_PRIOR_GAMES (3) games in his trailing window (features_lib.trailing_window), but those
are the last 3 games he PLAYED, however far back they reach -- an injured player's window can span half a season.
A.J. Brown, 2026 week 10 (NE): his window is weeks 1, 6 and 7 (44% of the snaps in week 1 before an ankle injury), and
he played only one of NE's last three games before a knee injury. The score reads three games; the recent picture is
one. This flag says so beside the name; it changes no score, rank, pool or pick.

DEFINITION. Of his current team's THIN_WINDOW_TEAM_GAMES most recent regular-season games before the target week
(nfl_games, this season and the one before), he played fewer than THIN_WINDOW_MIN_GAMES -- "played" meaning a stats row
or offense snaps for that team that week. A game his team played before he joined it counts as one he didn't play.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "data"))
from team_crosswalk import norm_team  # noqa: E402
from starter_back import weeks_text  # noqa: E402

THIN_WINDOW_TEAM_GAMES = 3   # the team's most recent regular-season games looked at
THIN_WINDOW_MIN_GAMES = 2    # flagged when he played fewer of them than this


def team_games(con, season, week):
    """{team: [(season, week), ...]} -- every team's regular-season games before (season, week), this season and the
    previous one, oldest first."""
    out = {}
    for s, w, home, away in con.execute(
            "SELECT season, week, home_team, away_team FROM nfl_games WHERE is_playoffs=0 AND season IN (?, ?) "
            "AND (season < ? OR week < ?) ORDER BY season, week", (season - 1, season, season, week)):
        for t in (norm_team(home), norm_team(away)):
            out.setdefault(t, []).append((s, w))
    return out


def thin_window(team, recent_team_games, played, window, target_season):
    """The flag dict, or None. recent_team_games: the team's games, oldest first; played: {(season, week)} he played
    for this team; window: features_lib.trailing_window tuples (season, week, team, pos, touches)."""
    last = recent_team_games[-THIN_WINDOW_TEAM_GAMES:]
    if not last:
        return None
    got = [g for g in last if g in played]
    if len(got) >= THIN_WINDOW_MIN_GAMES:
        return None
    missed = [g for g in last if g not in played]
    win = [(s, w) for s, w, *_ in window]
    flag = {"team": team, "team_games": [{"season": s, "week": w} for s, w in last],
            "played": [{"season": s, "week": w} for s, w in got], "missed": [{"season": s, "week": w} for s, w in missed],
            "window": [{"season": s, "week": w} for s, w in win]}
    flag["text"] = describe(flag, target_season)
    return flag


def describe(flag, target_season):
    """e.g. 'played 1 of NE's last 3 games (missed weeks 8-9); the model's 3-game window is weeks 1, 6-7'."""
    n_team = len(flag["team_games"])
    text = f"played {len(flag['played'])} of {flag['team']}'s last {n_team} games"
    if flag["missed"]:
        text += f" (missed {weeks_text(flag['missed'], target_season)})"
    if flag["window"]:
        text += f"; the model's {len(flag['window'])}-game window is {weeks_text(flag['window'], target_season)}"
    return text
