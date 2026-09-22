#!/usr/bin/env python3
"""Shared team-code crosswalk.

nfl_games and team_week_stats preserve each franchise's HISTORICAL
abbreviation for the season it actually happened in (e.g. 'OAK' through
2019, 'SD' through 2016, 'STL' through 2015). player_week_stats instead
retroactively labels every season with the team's CURRENT code ('LV',
'LAC', 'LA'). Any join across that boundary needs this crosswalk, or a
relocated franchise's rows silently never match.

norm_team() converts a historical-convention code to its current
equivalent; it's a no-op for a code that's already current (or that was
never relocated), so it's always safe to apply defensively even to a
source you believe is already on the current-code convention.

Originally duplicated in model/features_lib.py and model/generate_labels_
and_breakouts.py (each table that needed it got its own copy); extracted
here once a third and fourth table (team_week_stats' Vegas columns, and
now the play-by-play aggregates in this same data/ directory) needed the
same crosswalk, so a fifth consumer doesn't get a fifth copy.
"""

TEAM_ALIASES = {"STL": "LA", "SD": "LAC", "OAK": "LV"}


def norm_team(code):
    return TEAM_ALIASES.get(code, code)
