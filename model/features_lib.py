#!/usr/bin/env python3
"""Shared feature computation for player_week_features, used by both the
historical training-table builder (generate_player_week_features.py) and
live scoring (score_week.py).

FeatureEngine precomputes lookups once from the DB, then compute_features()
answers a single (season, week, player_id) query using only data strictly
before that week. Eligibility (>=3 prior games, respecting season-gap
boundaries) is the only gate -- it does NOT depend on labels_player_week
row existence, so it works equally for an already-played historical week or
a not-yet-played future one.
"""
import sqlite3

MIN_PRIOR_GAMES = 3

# nfl_games (schedules) preserves each franchise's historical abbreviation,
# but player_week_stats retroactively uses the team's *current* code for every
# season. Without this crosswalk, games involving a relocated franchise never
# match between the two tables.
TEAM_ALIASES = {"STL": "LA", "SD": "LAC", "OAK": "LV"}


def norm_team(code):
    return TEAM_ALIASES.get(code, code)


# Positions where carries+receptions is not a meaningful usage signal.
NO_TOUCH_SIGNAL_CANDIDATES = ["K", "P", "DST", "DEF"]


def linreg_slope(ys):
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    xmean = sum(xs) / n
    ymean = sum(ys) / n
    num = sum((x - xmean) * (y - ymean) for x, y in zip(xs, ys))
    den = sum((x - xmean) ** 2 for x in xs)
    return num / den if den else 0.0


# Every feature persisted into player_week_features -- the complete
# historical record. This legitimately includes share_delta_vs_prior_season:
# that feature won its 2023 validation slice, got folded in here and trained
# into a real candidate model, then lost the 2025 held-out test and was
# rejected (see CLAUDE.md "Methodology rules" and
# evaluation/eval_share_delta_on_2025.py's "rejected_candidate" metadata).
# It stays in the persisted table (a historical record) and in the rejected
# candidate's own joblib, but the *production* model never trained on it --
# hence PRODUCTION_FEATURE_COLS below, a strict subset. This split is
# intentional, not drift: don't merge them back into one list, and don't
# "fix" PRODUCTION_FEATURE_COLS by adding share_delta back to it.
PERSISTED_FEATURE_COLS = [
    "trailing_touches_avg",
    "trailing_touches_trend",
    "trailing_team_touch_share",
    "opponent_position_matchup",
    "experience_seasons",
    "games_played_this_season",
    "is_short_week",
    "is_home",
    "is_bye_return",
    "starter_absent_proxy",
    "share_delta_vs_prior_season",
]

# What the live-scoring production model actually trains and scores on
# (train_production_model.py / odds_xgb_model_production.joblib) --
# PERSISTED_FEATURE_COLS minus the rejected share_delta_vs_prior_season.
PRODUCTION_FEATURE_COLS = [c for c in PERSISTED_FEATURE_COLS if c != "share_delta_vs_prior_season"]

# Group 1 hypothesis: receiving-opportunity features, computed by
# compute_features() but NOT part of PERSISTED_FEATURE_COLS or
# PRODUCTION_FEATURE_COLS -- not yet adopted into any persisted table or
# production model. See evaluation/ for the group's held-out test.
RECEIVING_OPPORTUNITY_FEATURES = [
    "trailing_target_share",
    "trailing_air_yards_share",
    "trailing_adot",
]


class FeatureEngine:
    def __init__(self, con: sqlite3.Connection):
        con.row_factory = sqlite3.Row
        self.con = con

        self.stats_seasons = sorted(r[0] for r in con.execute("SELECT DISTINCT season FROM player_week_stats"))
        self.games_seasons = sorted(r[0] for r in con.execute("SELECT DISTINCT season FROM nfl_games"))

        actual_positions = {r[0] for r in con.execute("SELECT DISTINCT pos FROM player_week_stats")}
        self.no_touch_signal_pos = {p for p in NO_TOUCH_SIGNAL_CANDIDATES if p in actual_positions}

        # Non-playoff (season,week,team) exclusion set, across every season we
        # have a schedule for (historical AND future -- playoffs are known in
        # advance for a completed regular season, though usually irrelevant
        # for live scoring of an upcoming regular-season week).
        self._playoff_team_weeks = set()
        for season, week, home, away in con.execute(
            "SELECT season, week, home_team, away_team FROM nfl_games WHERE is_playoffs = 1"
        ):
            self._playoff_team_weeks.add((season, week, norm_team(home)))
            self._playoff_team_weeks.add((season, week, norm_team(away)))

        # player_history[player_id] = sorted [(season, week, team, pos, touches), ...]
        # non-playoff games actually played, across every loaded season.
        self.player_history = {}
        for r in con.execute(
            "SELECT season, week, player_id, team, pos, rush_att, rec_rec FROM player_week_stats ORDER BY player_id, season, week"
        ):
            if (r["season"], r["week"], r["team"]) in self._playoff_team_weeks:
                continue
            touches = (r["rush_att"] or 0) + (r["rec_rec"] or 0)
            self.player_history.setdefault(r["player_id"], []).append(
                (r["season"], r["week"], r["team"], r["pos"], touches)
            )

        # team_week_touches[(season,week,team)] = total team touches that week
        self.team_week_touches = {}
        for entries in self.player_history.values():
            for (s, w, tm, _pos, t) in entries:
                k = (s, w, tm)
                self.team_week_touches[k] = self.team_week_touches.get(k, 0) + t

        # team_played_weeks[(season,team)]: weeks with actual stat data (used for
        # opponent-matchup, which needs real fantasy_points_half to average).
        self.team_played_weeks = {}
        for entries in self.player_history.values():
            for (s, w, tm, _pos, _t) in entries:
                self.team_played_weeks.setdefault((s, tm), set()).add(w)
        for k in self.team_played_weeks:
            self.team_played_weeks[k] = sorted(self.team_played_weeks[k])

        # team_scheduled_weeks[(season,team)]: weeks from the schedule itself
        # (nfl_games), known in advance -- used for is_bye_return so it still
        # works on a not-yet-played week.
        self.team_scheduled_weeks = {}
        self.game_info = {}  # (season,week,team) -> (is_thursday, is_home, opponent)
        for season, week, home, away, is_thu in con.execute(
            "SELECT season, week, home_team, away_team, is_thursday FROM nfl_games WHERE is_playoffs = 0"
        ):
            h, a = norm_team(home), norm_team(away)
            self.team_scheduled_weeks.setdefault((season, h), set()).add(week)
            self.team_scheduled_weeks.setdefault((season, a), set()).add(week)
            self.game_info[(season, week, h)] = (is_thu, 1, a)
            self.game_info[(season, week, a)] = (is_thu, 0, h)
        for k in self.team_scheduled_weeks:
            self.team_scheduled_weeks[k] = sorted(self.team_scheduled_weeks[k])

        # pos_allowed[(season,week,team_faced,pos)] = total fantasy_points_half
        # scored against team_faced that week by players at pos.
        self.pos_allowed = {}
        try:
            points_of = {
                (s, w, pid): fp
                for s, w, pid, fp in con.execute("SELECT season, week, player_id, fantasy_points_half FROM labels_player_week")
            }
        except sqlite3.OperationalError:
            points_of = {}
        for r in con.execute("SELECT season, week, player_id, team, opp, pos FROM player_week_stats"):
            if (r["season"], r["week"], r["team"]) in self._playoff_team_weeks:
                continue
            fp = points_of.get((r["season"], r["week"], r["player_id"]))
            if fp is None:
                continue
            k = (r["season"], r["week"], r["opp"], r["pos"])
            self.pos_allowed[k] = self.pos_allowed.get(k, 0.0) + fp

        # played_this_week[(season,week)] = set of player_ids with any row that week
        self.played_this_week = {}
        for entries_pid, entries in self.player_history.items():
            for (s, w, _tm, _pos, _t) in entries:
                self.played_this_week.setdefault((s, w), set()).add(entries_pid)

        # roster_by_team_pos_season[(season,team,pos)] = set(player_id) who ever had a row there
        self.roster = {}
        for pid, entries in self.player_history.items():
            for (s, _w, tm, pos, _t) in entries:
                self.roster.setdefault((s, tm, pos), set()).add(pid)

        self.first_season_of = {
            pid: fs for pid, fs in con.execute("SELECT player_id, first_season FROM ref_players")
        }

        # recv_game_stats[(player_id, season, week)] = (target_share, air_yards_share,
        # receiving_air_yards, rec_tgt) for that single game -- source for the
        # Group 1 receiving-opportunity features. Not playoff-filtered here;
        # only ever looked up via a window already built from player_history,
        # which is.
        self.recv_game_stats = {
            (r["player_id"], r["season"], r["week"]): (
                r["target_share"], r["air_yards_share"], r["receiving_air_yards"], r["rec_tgt"],
            )
            for r in con.execute(
                "SELECT season, week, player_id, target_share, air_yards_share, receiving_air_yards, rec_tgt FROM player_week_stats"
            )
        }

        # actual_team_pos[(season,week,player_id)] = (team, pos) as recorded for
        # that exact week -- ground truth when the week has already been played
        # (e.g. after a trade). Only a future, not-yet-played week has to fall
        # back to inferring team/pos from the player's most recent prior game.
        self.actual_team_pos = {
            (r["season"], r["week"], r["player_id"]): (r["team"], r["pos"])
            for r in con.execute("SELECT season, week, player_id, team, pos FROM player_week_stats")
        }

    def _gap_after(self, target_season):
        combined = sorted(set(self.stats_seasons) | {target_season})
        return {combined[i - 1] for i in range(1, len(combined)) if combined[i] - combined[i - 1] > 1}

    @staticmethod
    def _crosses_gap(prev_season, cur_season, gap_after):
        return any(prev_season <= g < cur_season for g in gap_after)

    def _touches_window(self, player_id, season, week):
        """Prior games for player_id strictly before (season,week), with the
        gap-reset rule applied -- including a final check for a gap between
        the player's last known game and the query point itself (this is what
        makes a not-yet-loaded season correctly wipe eligibility for live
        scoring, the same way the historical 2018->2020 hole does)."""
        hist = self.player_history.get(player_id, [])
        prior = [h for h in hist if (h[0], h[1]) < (season, week)]
        if not prior:
            return []
        gap_after = self._gap_after(season)
        window = []
        for h in prior:
            if window and self._crosses_gap(window[-1][0], h[0], gap_after):
                window = []
            window.append(h)
        if window and self._crosses_gap(window[-1][0], season, gap_after):
            window = []
        return window

    def _trailing_touch_share(self, player_id, season, week):
        """Returns (share, last3_window) or (None, window) if not eligible."""
        window = self._touches_window(player_id, season, week)
        if len(window) < MIN_PRIOR_GAMES:
            return None, window
        last3 = window[-MIN_PRIOR_GAMES:]
        player_sum = sum(t for (_, _, _, _, t) in last3)
        team_sum = sum(self.team_week_touches.get((s, w, tm), 0) for (s, w, tm, _, _) in last3)
        share = (player_sum / team_sum) if team_sum else None
        return share, last3

    def _presumed_starter(self, season, week, team, pos):
        candidates = self.roster.get((season, team, pos), set())
        best_pid, best_share = None, -1.0
        for pid in candidates:
            share, _ = self._trailing_touch_share(pid, season, week)
            if share is not None and share > best_share:
                best_share, best_pid = share, pid
        return best_pid

    def is_eligible(self, season, week, player_id):
        return len(self._touches_window(player_id, season, week)) >= MIN_PRIOR_GAMES

    def _prior_season_avg_share(self, player_id, season):
        """Average per-game team touch share across ALL of player_id's games in
        the immediately preceding LOADED season. NULL (None) if that season
        isn't loaded at all (e.g. season=2020's prior season is the missing
        2019 -- this is the same season-gap rule as everywhere else in the
        pipeline, just checked directly since there's only ever one
        candidate "prior season" here) or if the player has no games in it
        (rookies)."""
        prior_season = season - 1
        if prior_season not in self.stats_seasons:
            return None
        entries = [h for h in self.player_history.get(player_id, []) if h[0] == prior_season]
        if not entries:
            return None
        shares = []
        for (s, w, tm, _pos, t) in entries:
            team_total = self.team_week_touches.get((s, w, tm), 0)
            if team_total > 0:
                shares.append(t / team_total)
        return (sum(shares) / len(shares)) if shares else None

    def _trailing_receiving_opportunity(self, player_id, season, week, pos, window):
        """Group 1 hypothesis features: trailing_target_share,
        trailing_air_yards_share, trailing_adot -- averaged over the exact
        same last-3-eligible-game window as the touches-based features
        (caller has already confirmed len(window) >= MIN_PRIOR_GAMES).

        NULL computed directly for QB rows, not computed then overridden --
        a QB's target share is structurally meaningless the same way touch
        share is for K/P elsewhere in this file. Per-game aDOT
        (receiving_air_yards / rec_tgt) is undefined for a zero-target game
        and excluded from its own average; if none of the 3 games have a
        defined aDOT, trailing_adot is None."""
        if pos == "QB":
            return None, None, None

        last3 = window[-MIN_PRIOR_GAMES:]
        ts_vals, ays_vals, adot_vals = [], [], []
        for (s, w, _tm, _pos, _t) in last3:
            rec = self.recv_game_stats.get((player_id, s, w))
            if rec is None:
                continue
            target_share, air_yards_share, receiving_air_yards, targets = rec
            if target_share is not None:
                ts_vals.append(target_share)
            if air_yards_share is not None:
                ays_vals.append(air_yards_share)
            if targets and receiving_air_yards is not None:
                adot_vals.append(receiving_air_yards / targets)

        trailing_target_share = (sum(ts_vals) / len(ts_vals)) if ts_vals else None
        trailing_air_yards_share = (sum(ays_vals) / len(ays_vals)) if ays_vals else None
        trailing_adot = (sum(adot_vals) / len(adot_vals)) if adot_vals else None
        return trailing_target_share, trailing_air_yards_share, trailing_adot

    def compute_schedule_dependent_features(self, season, week, team, pos, player_id):
        """The 4 features that depend on which team the player is actually on
        this week (opponent, home/away, short week, presumed-starter check),
        computed for a GIVEN team rather than inferred from history. Reusable
        both by compute_features() (using the inferred/actual team) and by a
        caller correcting for a confirmed offseason team change, where the
        real 2026 schedule and historical opponent context can be computed
        correctly even though usage-based features (touches, touch share)
        cannot."""
        gl = self.game_info.get((season, week, team))
        is_short_week, is_home, opp = gl if gl else (None, None, None)

        if opp is None:
            matchup = None
        else:
            opp_weeks = [w for w in self.team_played_weeks.get((season, opp), []) if w < week]
            if not opp_weeks:
                matchup = None
            else:
                vals = [self.pos_allowed.get((season, w, opp, pos), 0.0) for w in opp_weeks]
                matchup = sum(vals) / len(vals)

        if pos in self.no_touch_signal_pos:
            starter_absent_proxy = None
        else:
            starter_id = self._presumed_starter(season, team=team, pos=pos, week=week)
            if starter_id is None:
                starter_absent_proxy = 0
            else:
                any_data_this_week = bool(self.played_this_week.get((season, week)))
                if not any_data_this_week:
                    starter_absent_proxy = None
                else:
                    starter_played = starter_id in self.played_this_week.get((season, week), set())
                    starter_absent_proxy = 1 if (not starter_played and starter_id != player_id) else 0

        return {
            "is_short_week": is_short_week,
            "is_home": is_home,
            "opponent_position_matchup": matchup,
            "starter_absent_proxy": starter_absent_proxy,
            "_opp": opp,
        }

    def compute_features(self, season, week, player_id):
        """Returns a dict of PERSISTED_FEATURE_COLS plus
        RECEIVING_OPPORTUNITY_FEATURES (the latter not yet part of any
        persisted table or production model -- see
        RECEIVING_OPPORTUNITY_FEATURES), or None if the player isn't
        eligible (<3 prior games, respecting season-gap boundaries) as of
        (season, week). Uses only data strictly before (season, week)."""
        window = self._touches_window(player_id, season, week)
        if len(window) < MIN_PRIOR_GAMES:
            return None

        last3 = window[-MIN_PRIOR_GAMES:]
        touches_list = [t for (_, _, _, _, t) in last3]
        avg_touches = sum(touches_list) / len(touches_list)
        trend = linreg_slope(touches_list)
        player_sum = sum(touches_list)
        team_sum = sum(self.team_week_touches.get((s, w, tm), 0) for (s, w, tm, _, _) in last3)
        touch_share = (player_sum / team_sum) if team_sum else None

        # Ground truth when the target week has already been played (handles a
        # trade between the player's last prior game and this week correctly);
        # otherwise infer from the most recent prior game, the best we can do
        # for a genuinely future week.
        actual = self.actual_team_pos.get((season, week, player_id))
        team, pos = actual if actual else (window[-1][2], window[-1][3])

        sched = self.compute_schedule_dependent_features(season, week, team, pos, player_id)
        is_short_week, is_home, opp, matchup = sched["is_short_week"], sched["is_home"], sched["_opp"], sched["opponent_position_matchup"]

        fs = self.first_season_of.get(player_id)
        experience = (season - fs) if fs is not None else None

        games_played_this_season = sum(
            1 for (s, w, _tm, _pos, _t) in self.player_history.get(player_id, []) if s == season and w < week
        )

        prior_sched_weeks = [w for w in self.team_scheduled_weeks.get((season, team), []) if w < week]
        if not prior_sched_weeks:
            is_bye_return = 0
        else:
            is_bye_return = 1 if (week - max(prior_sched_weeks)) > 1 else 0

        starter_absent_proxy = sched["starter_absent_proxy"]

        prior_season_avg_share = self._prior_season_avg_share(player_id, season)
        if touch_share is None or prior_season_avg_share is None:
            share_delta_vs_prior_season = None
        else:
            share_delta_vs_prior_season = touch_share - prior_season_avg_share

        trailing_target_share, trailing_air_yards_share, trailing_adot = self._trailing_receiving_opportunity(
            player_id, season, week, pos, window
        )

        return {
            "trailing_touches_avg": avg_touches,
            "trailing_touches_trend": trend,
            "trailing_team_touch_share": touch_share,
            "opponent_position_matchup": matchup,
            "experience_seasons": experience,
            "games_played_this_season": games_played_this_season,
            "is_short_week": is_short_week,
            "is_home": is_home,
            "is_bye_return": is_bye_return,
            "starter_absent_proxy": starter_absent_proxy,
            "share_delta_vs_prior_season": share_delta_vs_prior_season,
            "trailing_target_share": trailing_target_share,
            "trailing_air_yards_share": trailing_air_yards_share,
            "trailing_adot": trailing_adot,
            "_team": team,
            "_pos": pos,
            "_opp": opp,
        }
