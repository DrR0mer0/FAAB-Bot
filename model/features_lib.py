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
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "data"))
from team_crosswalk import TEAM_ALIASES, norm_team  # noqa: E402 -- shared crosswalk; see data/team_crosswalk.py

MIN_PRIOR_GAMES = 3

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
# intentional, not drift: don't merge share_delta back into
# PRODUCTION_FEATURE_COLS.
#
# trailing_target_share / trailing_air_yards_share / trailing_adot are the
# Group 1 (receiving-opportunity) features: tested against the 10-feature
# production baseline on 2024 and promoted after a per-position re-test
# showed a genuine, position-appropriate win (WR +0.10 P@10, TE +0.033),
# not a QB-reallocation artifact -- see evaluation/test_receiving_
# opportunity_features.py (kept as the historical record of that test) and
# RECEIVING_OPPORTUNITY_FEATURES below. Unlike share_delta, these ARE part
# of PRODUCTION_FEATURE_COLS.
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
    "trailing_target_share",
    "trailing_air_yards_share",
    "trailing_adot",
]

# What the live-scoring production model actually trains and scores on
# (train_production_model.py / odds_xgb_model_production.joblib) --
# PERSISTED_FEATURE_COLS minus the rejected share_delta_vs_prior_season.
# 13 features as of the Group 1 promotion (10 original + the 3 above).
PRODUCTION_FEATURE_COLS = [c for c in PERSISTED_FEATURE_COLS if c != "share_delta_vs_prior_season"]

# Group 1 (receiving-opportunity features): promoted into
# PERSISTED_FEATURE_COLS and PRODUCTION_FEATURE_COLS above. Kept as its own
# named list only because evaluation/test_receiving_opportunity_features.py
# (the historical record of the hypothesis test that got it promoted) still
# imports it -- not a separate candidate group anymore.
RECEIVING_OPPORTUNITY_FEATURES = [
    "trailing_target_share",
    "trailing_air_yards_share",
    "trailing_adot",
]

# Group 2 hypothesis: QB volume + team pass-catcher mix, computed by
# compute_features() but NOT part of PERSISTED_FEATURE_COLS or
# PRODUCTION_FEATURE_COLS -- not yet adopted into any persisted table or
# production model. See evaluation/ for the group's held-out test.
QB_VOLUME_FEATURES = [
    "trailing_pass_attempts",
    "trailing_pass_air_yards",
    "trailing_team_wr_target_rate",
]

# Group 3 hypothesis: teammate competition for touches at the same
# position -- computed by compute_features() but NOT part of
# PERSISTED_FEATURE_COLS or PRODUCTION_FEATURE_COLS -- not yet adopted
# into any persisted table or production model. See
# evaluation/test_teammate_competition_features.py for the group's
# held-out test.
TEAMMATE_COMPETITION_FEATURES = [
    "trailing_position_group_rank",
    "trailing_share_of_position_group",
]

# Group 4 hypothesis: Vegas lines describing the player's own UPCOMING
# game, sourced from team_week_stats (spread/total/implied_total,
# populated 2010-2025, mostly NULL for not-yet-posted 2026 weeks) --
# computed by compute_features() but NOT part of PERSISTED_FEATURE_COLS
# or PRODUCTION_FEATURE_COLS -- not yet adopted. Unlike every other group
# in this file, these are NOT a trailing window over prior games: they
# describe the game about to be played, known before kickoff, so using
# the CURRENT (season, week)'s own line is not leakage. See
# evaluation/test_vegas_features.py for the group's held-out test and its
# 2026-specific NULL-rate report.
VEGAS_FEATURES = [
    "implied_team_total",
    "game_total",
    "team_spread",
]

# Group 5 hypothesis: trailing per-game efficiency, computed by
# compute_features() but NOT part of PERSISTED_FEATURE_COLS or
# PRODUCTION_FEATURE_COLS -- not yet adopted into any persisted table or
# production model. See evaluation/test_efficiency_features.py for the
# group's held-out test, including its check for the specific confound
# named going in: labels_player_week.spike_flag is defined relative to a
# player's own trailing POINTS baseline (generate_labels_and_breakouts.py),
# so a depressed efficiency stretch mechanically lowers that baseline and
# can make an unrelated point total look like a "spike" without the
# player having actually gotten more efficient or more opportunity.
EFFICIENCY_FEATURES = [
    "trailing_racr",
    "trailing_receiving_epa",
    "trailing_rushing_epa",
]

# Group 7 hypothesis: offensive/defensive line quality, sourced from
# team_week_pbp_stats (data/load_pbp_aggregates.py) -- computed by
# compute_features() but NOT part of PERSISTED_FEATURE_COLS or
# PRODUCTION_FEATURE_COLS -- not yet adopted. All four are TEAM-level, not
# player-level: every player on the same team (or facing the same
# opponent) that week shares the same value, unlike every other trailing
# feature in this file. trailing_team_* describes the player's OWN team
# over his own trailing eligibility window (same weeks his other trailing
# features are drawn from); trailing_opp_* describes the UPCOMING
# opponent's own trailing form -- not tied to the player's game history at
# all -- resolved via nfl_games the same way opponent_position_matchup is.
# See evaluation/test_line_quality_features.py for the group's held-out
# test, including whether the model's feature importances suggest it uses
# a team-level, many-players-share-one-value feature at all.
LINE_QUALITY_FEATURES = [
    "trailing_team_sack_rate_allowed",
    "trailing_team_stuff_rate_allowed",
    "trailing_opp_sack_rate_generated",
    "trailing_opp_stuff_rate_generated",
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

        # qb_game_stats[(player_id, season, week)] = (attempts, passing_air_yards)
        # for that single game -- source for the Group 2 QB-volume features.
        # Same lookup pattern as recv_game_stats.
        self.qb_game_stats = {
            (r["player_id"], r["season"], r["week"]): (r["attempts"], r["passing_air_yards"])
            for r in con.execute("SELECT season, week, player_id, attempts, passing_air_yards FROM player_week_stats")
        }

        # efficiency_game_stats[(player_id, season, week)] = (racr,
        # receiving_epa, rushing_epa) for that single game -- source for the
        # Group 5 efficiency features. Each raw column's own NULL already
        # means "no qualifying denominator that game" -- confirmed against
        # the DB: racr is NULL almost exactly when receiving_air_yards is 0
        # (an undefined ratio) despite a target existing, and
        # receiving_epa/rushing_epa are NULL almost exactly when the player
        # had no qualifying target/carry that week -- so no extra threshold
        # logic is needed on top of "is the raw value NULL."
        self.efficiency_game_stats = {
            (r["player_id"], r["season"], r["week"]): (r["racr"], r["receiving_epa"], r["rushing_epa"])
            for r in con.execute("SELECT season, week, player_id, racr, receiving_epa, rushing_epa FROM player_week_stats")
        }

        # team_week_wr_targets / team_week_rb_targets[(season,week,team)] =
        # total targets that week to players at that position on that team --
        # source for the Group 2 trailing_team_wr_target_rate feature.
        # Playoff-filtered like team_week_touches, since it's only ever
        # looked up via a window already built from player_history.
        self.team_week_wr_targets = {}
        self.team_week_rb_targets = {}
        for r in con.execute("SELECT season, week, team, pos, rec_tgt FROM player_week_stats"):
            if (r["season"], r["week"], r["team"]) in self._playoff_team_weeks:
                continue
            if r["pos"] not in ("WR", "RB"):
                continue
            k = (r["season"], r["week"], r["team"])
            bucket = self.team_week_wr_targets if r["pos"] == "WR" else self.team_week_rb_targets
            bucket[k] = bucket.get(k, 0) + (r["rec_tgt"] or 0)

        # actual_team_pos[(season,week,player_id)] = (team, pos) as recorded for
        # that exact week -- ground truth when the week has already been played
        # (e.g. after a trade). Only a future, not-yet-played week has to fall
        # back to inferring team/pos from the player's most recent prior game.
        self.actual_team_pos = {
            (r["season"], r["week"], r["player_id"]): (r["team"], r["pos"])
            for r in con.execute("SELECT season, week, player_id, team, pos FROM player_week_stats")
        }

        # team_week_vegas[(season,week,team)] = (spread, total, implied_total)
        # -- source for the Group 4 Vegas features. team_week_stats uses each
        # franchise's HISTORICAL abbreviation for every season (the same
        # convention as nfl_games), not player_week_stats' retroactive
        # current-code convention -- confirmed against the DB: team_week_stats
        # has 'OAK' rows through 2019, matching nfl_games, while
        # player_week_stats already says 'LV' for those same seasons. So
        # norm_team() is required here too, same as game_info above; without
        # it every relocated franchise's players would silently get NULL
        # Vegas features for their pre-move seasons.
        self.team_week_vegas = {
            (r["season"], r["week"], norm_team(r["team"])): (r["spread"], r["total"], r["implied_total"])
            for r in con.execute("SELECT season, week, team, spread, total, implied_total FROM team_week_stats")
        }

        # team_pbp_metrics[(season,week,team)] = (sack_rate_allowed,
        # stuff_rate_allowed, sack_rate_generated, stuff_rate_generated) --
        # source for the Group 7 line-quality features. team_week_pbp_stats
        # (data/load_pbp_aggregates.py) already uses the CURRENT franchise
        # code for every season -- confirmed empirically when that table was
        # built -- the same convention as player_week_stats/game_info's
        # norm_team()'d output, so no additional crosswalk is needed here.
        self.team_pbp_metrics = {
            (r["season"], r["week"], r["team"]): (
                r["sack_rate_allowed"], r["stuff_rate_allowed"], r["sack_rate_generated"], r["stuff_rate_generated"],
            )
            for r in con.execute(
                "SELECT season, week, team, sack_rate_allowed, stuff_rate_allowed, "
                "sack_rate_generated, stuff_rate_generated FROM team_week_pbp_stats"
            )
        }

        # team_pbp_history[team] = sorted [(season, week), ...] with a
        # team_week_pbp_stats row -- source for _team_pbp_window below,
        # which walks a TEAM's own trailing weeks (not tied to any one
        # player's game history) for the Group 7 trailing_opp_* features.
        self.team_pbp_history = {}
        for r in con.execute("SELECT season, week, team FROM team_week_pbp_stats ORDER BY team, season, week"):
            self.team_pbp_history.setdefault(r["team"], []).append((r["season"], r["week"]))

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

    def _team_pbp_window(self, team, season, week):
        """Prior team_week_pbp_stats weeks for `team` strictly before
        (season,week), same gap-reset rule as _touches_window (reuses
        _gap_after/_crosses_gap, which only look at the target season
        against the loaded-season set -- team_week_pbp_stats covers the
        identical season list as player_week_stats, so this is safe to
        share). Unlike _touches_window, this walks the TEAM's own
        schedule, not any one player's game history -- for Group 7's
        trailing_opp_* features, which describe the upcoming opponent's
        own recent form, not the scoring player's. Returns up to the last
        MIN_PRIOR_GAMES (season, week) pairs, or fewer/empty if the team
        doesn't have that many prior loaded weeks yet."""
        hist = self.team_pbp_history.get(team, [])
        prior = [h for h in hist if h < (season, week)]
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
        return window[-MIN_PRIOR_GAMES:]

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

    # Group 6 hypothesis: exponential recency weighting for the trailing-
    # window computation behind 4 EXISTING production usage features
    # (trailing_touches_avg, trailing_team_touch_share, trailing_target_share,
    # trailing_air_yards_share) -- this modifies HOW those features are
    # computed, under the SAME names, rather than adding new ones, so it's
    # tested via compute_recency_weighted_usage() below (called directly by
    # evaluation/test_recency_decay_features.py) rather than through
    # compute_features(), which must keep producing the unweighted
    # production values. Decay constant: 0.5 per game further back (a
    # 1-game half-life) for BOTH variants -- chosen because usage/role can
    # shift quickly (a new starter, a returning injury) and a flat mean
    # already responds slowly over a 3-game window; halving the weight each
    # game back means the most recent game alone carries as much weight as
    # every older game in the window combined, while still letting older
    # games break a tie or soften one flukey game rather than a hard
    # single-game cutoff would. Eligibility stays fixed at >=3 prior games
    # regardless of variant, so variant B's wider window never admits a
    # player the baseline candidate pool wouldn't already include -- only
    # the weighting inside an already-eligible window changes.
    RECENCY_DECAY = 0.5

    def compute_recency_weighted_usage(self, season, week, player_id, pos, window_size):
        """Recency-weighted variants of trailing_touches_avg,
        trailing_team_touch_share, trailing_target_share, and
        trailing_air_yards_share -- same 4 names, same >=3-prior-games
        eligibility gate and gap-aware window as the production versions,
        just weighted by RECENCY_DECAY**(games_ago) instead of averaged
        flat, over the last `window_size` eligible games (3 for variant A,
        6 for variant B -- fewer than `window_size` if the player doesn't
        have that many prior games yet; this is a widened CEILING on the
        window, not a stricter floor, so it never conflicts with the fixed
        eligibility gate above).

        trailing_touches_avg and trailing_team_touch_share generalize the
        production ratio-of-SUMS pattern (_trailing_touch_share) to a
        ratio of WEIGHTED sums: weighted_share = sum(w_i * touches_i) /
        sum(w_i * team_touches_i), which collapses to the unweighted
        formula when every weight is equal. trailing_target_share and
        trailing_air_yards_share generalize the production flat MEAN of
        per-game ratios (_trailing_receiving_opportunity) to a weighted
        mean, with the denominator counting only the weight of games where
        that game's own ratio is defined -- same "average over defined
        values, None if none qualify" rule as the unweighted version, just
        weighted. `pos` NULL-gates trailing_target_share/
        trailing_air_yards_share for QB, matching _trailing_receiving_
        opportunity exactly -- this test changes ONLY the weighting, not
        which positions the feature applies to.

        Returns None if the player isn't eligible (<3 prior games)."""
        full_window = self._touches_window(player_id, season, week)
        if len(full_window) < MIN_PRIOR_GAMES:
            return None
        window = full_window[-window_size:]
        n = len(window)
        # weights[i] pairs with window[i]; index n-1 (most recent) gets
        # weight 1, index 0 (oldest in this window) gets the smallest.
        weights = [self.RECENCY_DECAY ** (n - 1 - i) for i in range(n)]

        w_touches = sum(w * t for w, (_, _, _, _, t) in zip(weights, window))
        w_weight_sum = sum(weights)
        trailing_touches_avg = w_touches / w_weight_sum

        w_team_touches = sum(
            w * self.team_week_touches.get((s, wk, tm), 0) for w, (s, wk, tm, _, _) in zip(weights, window)
        )
        trailing_team_touch_share = (w_touches / w_team_touches) if w_team_touches else None

        if pos == "QB":
            trailing_target_share, trailing_air_yards_share = None, None
        else:
            ts_num, ts_den, ays_num, ays_den = 0.0, 0.0, 0.0, 0.0
            for w, (s, wk, _tm, _pos, _t) in zip(weights, window):
                rec = self.recv_game_stats.get((player_id, s, wk))
                if rec is None:
                    continue
                target_share, air_yards_share, _ray, _tgt = rec
                if target_share is not None:
                    ts_num += w * target_share
                    ts_den += w
                if air_yards_share is not None:
                    ays_num += w * air_yards_share
                    ays_den += w
            trailing_target_share = (ts_num / ts_den) if ts_den else None
            trailing_air_yards_share = (ays_num / ays_den) if ays_den else None

        return {
            "trailing_touches_avg": trailing_touches_avg,
            "trailing_team_touch_share": trailing_team_touch_share,
            "trailing_target_share": trailing_target_share,
            "trailing_air_yards_share": trailing_air_yards_share,
        }

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

    def _trailing_qb_volume(self, player_id, pos, last3):
        """Group 2 hypothesis features: trailing_pass_attempts,
        trailing_pass_air_yards -- averaged over the exact same
        last-3-eligible-game window as the touches-based features (caller
        has already confirmed eligibility).

        NULL computed directly for non-QB rows, not computed then
        overridden -- pass volume is structurally meaningless for a player
        who doesn't throw the ball, the same pattern used for the Group 1
        features and the K/P no-touch-signal gate elsewhere in this file."""
        if pos != "QB":
            return None, None

        att_vals, ay_vals = [], []
        for (s, w, _tm, _pos, _t) in last3:
            rec = self.qb_game_stats.get((player_id, s, w))
            if rec is None:
                continue
            attempts, passing_air_yards = rec
            if attempts is not None:
                att_vals.append(attempts)
            if passing_air_yards is not None:
                ay_vals.append(passing_air_yards)

        trailing_pass_attempts = (sum(att_vals) / len(att_vals)) if att_vals else None
        trailing_pass_air_yards = (sum(ay_vals) / len(ay_vals)) if ay_vals else None
        return trailing_pass_attempts, trailing_pass_air_yards

    def _trailing_efficiency(self, player_id, pos, last3):
        """Group 5 hypothesis features: trailing_racr,
        trailing_receiving_epa, trailing_rushing_epa -- averaged over the
        exact same last-3-eligible-game window as the touches-based
        features, using each game's own racr/receiving_epa/rushing_epa
        value straight from player_week_stats and including it only when
        non-NULL there (see efficiency_game_stats above for why that's
        already the correct "qualifying denominator" check -- same
        average-over-defined-values-else-None pattern as trailing_adot).

        trailing_racr and trailing_receiving_epa are compute-NULL-directly
        for QB -- receiving constructs; the handful of QB rows with a
        defined value are trick-play noise (190 and 188 of 9784 QB rows in
        the full DB), not a role QBs actually have. trailing_rushing_epa
        is deliberately NOT position-gated: it's meaningful for anyone who
        carries the ball, and for QB specifically it's real signal (84%
        of QB rows have a qualifying carry) that touch share can't
        express -- touch share for a QB measures only rushing VOLUME, a
        mobility proxy (see CLAUDE.md "Known model limitations"), while
        trailing_rushing_epa would measure whether those carries are any
        good. A player with no qualifying games in the window still gets
        None from the average itself, without needing a position gate."""
        racr_vals, recv_epa_vals, rush_epa_vals = [], [], []
        for (s, w, _tm, _pos, _t) in last3:
            rec = self.efficiency_game_stats.get((player_id, s, w))
            if rec is None:
                continue
            racr, receiving_epa, rushing_epa = rec
            if pos != "QB" and racr is not None:
                racr_vals.append(racr)
            if pos != "QB" and receiving_epa is not None:
                recv_epa_vals.append(receiving_epa)
            if rushing_epa is not None:
                rush_epa_vals.append(rushing_epa)

        trailing_racr = (sum(racr_vals) / len(racr_vals)) if racr_vals else None
        trailing_receiving_epa = (sum(recv_epa_vals) / len(recv_epa_vals)) if recv_epa_vals else None
        trailing_rushing_epa = (sum(rush_epa_vals) / len(rush_epa_vals)) if rush_epa_vals else None
        return trailing_racr, trailing_receiving_epa, trailing_rushing_epa

    def _trailing_team_wr_target_rate(self, last3):
        """Group 2 hypothesis feature: trailing_team_wr_target_rate --
        ratio of (summed WR targets) to (summed WR+RB targets) for the
        player's own team across the exact same last-3-eligible-game
        window as the touches-based features (ratio-of-sums, same pattern
        as _trailing_touch_share). Not position-gated: it describes the
        offense the player is part of, not the player themselves, so it
        applies at every position, including the QB throwing those
        targets."""
        wr_sum = sum(self.team_week_wr_targets.get((s, w, tm), 0) for (s, w, tm, _, _) in last3)
        rb_sum = sum(self.team_week_rb_targets.get((s, w, tm), 0) for (s, w, tm, _, _) in last3)
        denom = wr_sum + rb_sum
        return (wr_sum / denom) if denom else None

    def _position_group_rank(self, season, week, team, pos, player_id, player_avg_touches):
        """Group 3 hypothesis features: trailing_position_group_rank,
        trailing_share_of_position_group -- the player's rank and share of
        avg trailing touches among his own team's players at the same
        position, all computed with the exact same eligibility/gap-aware
        window as every other trailing feature (>=3 prior games, reset
        across season gaps). Candidates come from the season-long roster
        at (season, team, pos) -- same source _presumed_starter uses --
        but each candidate's own trailing average is recomputed fresh for
        THIS (season, week): a teammate who hasn't debuted yet by this
        week has no window and is naturally excluded, not force-included
        just because he's on the season's roster.

        Ties: competition ranking (1224-style) -- two backs both
        averaging 9.0 touches both rank 1, and the next-best back ranks 3,
        not 2. Reason: rank should say "how many teammates are strictly
        ahead of you", which is well-defined even under a tie; an
        arbitrary tiebreak (e.g. by player_id) would imply an ordering the
        data doesn't support.

        Single-player position group (no OTHER eligible teammate): ranks
        1 with share 1.0 -- there's no one to split touches with, not an
        undefined construct. Only QB is NULL-gated by the caller; every
        other position always has a defined rank/share once the player
        himself is eligible (player_avg_touches is always a real number
        by the time this is called)."""
        candidates = self.roster.get((season, team, pos), set())
        group_avgs = {player_id: player_avg_touches}
        for pid in candidates:
            if pid == player_id:
                continue
            w = self._touches_window(pid, season, week)
            if len(w) < MIN_PRIOR_GAMES:
                continue
            last3 = w[-MIN_PRIOR_GAMES:]
            group_avgs[pid] = sum(t for (_, _, _, _, t) in last3) / MIN_PRIOR_GAMES

        rank = 1 + sum(1 for pid, v in group_avgs.items() if pid != player_id and v > player_avg_touches)
        group_total = sum(group_avgs.values())
        share = (player_avg_touches / group_total) if group_total else None
        return rank, share

    def _vegas_features(self, season, week, team):
        """Group 4 hypothesis features: implied_team_total, game_total,
        team_spread -- looked up directly from team_week_stats for the
        team's own game at (season, week). Not a trailing window: this is
        the CURRENT week's own line, which is fine because a Vegas line is
        set and known well before kickoff, unlike a stat from the game
        itself. NULL when no line has been posted yet for that
        (season, week, team) -- the normal case for a future 2026 week at
        scoring time, not a bug (see evaluation/test_vegas_features.py's
        2026 NULL-rate report)."""
        rec = self.team_week_vegas.get((season, week, team))
        if rec is None:
            return None, None, None
        spread, total, implied_total = rec
        return implied_total, total, spread

    def _trailing_line_quality(self, opp, season, week, last3):
        """Group 7 hypothesis features: trailing_team_sack_rate_allowed,
        trailing_team_stuff_rate_allowed, trailing_opp_sack_rate_generated,
        trailing_opp_stuff_rate_generated -- all four TEAM-level, so every
        player sharing a team (or facing the same opponent) that week gets
        the same value, unlike every other trailing feature in this file.

        trailing_team_* uses the exact same last-3-eligible-game window as
        the player's other trailing features, looked up against
        team_week_pbp_stats for whichever team the player was actually ON
        at each of those specific weeks (last3's own tm, not a single
        fixed team -- correct across a mid-window trade the same way
        _trailing_touch_share already is). It describes the O-line/run-
        blocking context during the games the player's usage features are
        already drawn from.

        trailing_opp_* is different in kind: it describes the UPCOMING
        game, so it's the opponent's own trailing 3 team-weeks (via
        _team_pbp_window), strictly before the CURRENT (season, week) --
        not tied to the scoring player's game history at all. None/None
        if there's no resolved opponent (bye/unscheduled) for this week.

        Average-over-defined-values-else-None throughout, same pattern as
        every ratio-averaging trailing feature elsewhere in this file."""
        team_sack_vals, team_stuff_vals = [], []
        for (s, w, tm, _pos, _t) in last3:
            rec = self.team_pbp_metrics.get((s, w, tm))
            if rec is None:
                continue
            sack_allowed, stuff_allowed, _sack_gen, _stuff_gen = rec
            if sack_allowed is not None:
                team_sack_vals.append(sack_allowed)
            if stuff_allowed is not None:
                team_stuff_vals.append(stuff_allowed)
        trailing_team_sack_rate_allowed = (sum(team_sack_vals) / len(team_sack_vals)) if team_sack_vals else None
        trailing_team_stuff_rate_allowed = (sum(team_stuff_vals) / len(team_stuff_vals)) if team_stuff_vals else None

        if opp is None:
            trailing_opp_sack_rate_generated, trailing_opp_stuff_rate_generated = None, None
        else:
            opp_sack_vals, opp_stuff_vals = [], []
            for (s, w) in self._team_pbp_window(opp, season, week):
                rec = self.team_pbp_metrics.get((s, w, opp))
                if rec is None:
                    continue
                _sack_allowed, _stuff_allowed, sack_gen, stuff_gen = rec
                if sack_gen is not None:
                    opp_sack_vals.append(sack_gen)
                if stuff_gen is not None:
                    opp_stuff_vals.append(stuff_gen)
            trailing_opp_sack_rate_generated = (sum(opp_sack_vals) / len(opp_sack_vals)) if opp_sack_vals else None
            trailing_opp_stuff_rate_generated = (sum(opp_stuff_vals) / len(opp_stuff_vals)) if opp_stuff_vals else None

        return (
            trailing_team_sack_rate_allowed, trailing_team_stuff_rate_allowed,
            trailing_opp_sack_rate_generated, trailing_opp_stuff_rate_generated,
        )

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
        RECEIVING_OPPORTUNITY_FEATURES, QB_VOLUME_FEATURES,
        TEAMMATE_COMPETITION_FEATURES, VEGAS_FEATURES, EFFICIENCY_FEATURES,
        and LINE_QUALITY_FEATURES (none of these six groups is yet part of
        any persisted table or production model -- see their definitions
        above), or None if the player isn't eligible
        (<3 prior games, respecting season-gap boundaries) as of (season,
        week). Uses only data strictly before (season, week)."""
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

        trailing_pass_attempts, trailing_pass_air_yards = self._trailing_qb_volume(player_id, pos, last3)
        trailing_team_wr_target_rate = self._trailing_team_wr_target_rate(last3)

        if pos in ("RB", "WR", "TE"):
            trailing_position_group_rank, trailing_share_of_position_group = self._position_group_rank(
                season, week, team, pos, player_id, avg_touches
            )
        else:
            trailing_position_group_rank, trailing_share_of_position_group = None, None

        implied_team_total, game_total, team_spread = self._vegas_features(season, week, team)

        trailing_racr, trailing_receiving_epa, trailing_rushing_epa = self._trailing_efficiency(player_id, pos, last3)

        (
            trailing_team_sack_rate_allowed, trailing_team_stuff_rate_allowed,
            trailing_opp_sack_rate_generated, trailing_opp_stuff_rate_generated,
        ) = self._trailing_line_quality(opp, season, week, last3)

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
            "trailing_pass_attempts": trailing_pass_attempts,
            "trailing_pass_air_yards": trailing_pass_air_yards,
            "trailing_team_wr_target_rate": trailing_team_wr_target_rate,
            "trailing_position_group_rank": trailing_position_group_rank,
            "trailing_share_of_position_group": trailing_share_of_position_group,
            "implied_team_total": implied_team_total,
            "game_total": game_total,
            "team_spread": team_spread,
            "trailing_racr": trailing_racr,
            "trailing_receiving_epa": trailing_receiving_epa,
            "trailing_rushing_epa": trailing_rushing_epa,
            "trailing_team_sack_rate_allowed": trailing_team_sack_rate_allowed,
            "trailing_team_stuff_rate_allowed": trailing_team_stuff_rate_allowed,
            "trailing_opp_sack_rate_generated": trailing_opp_sack_rate_generated,
            "trailing_opp_stuff_rate_generated": trailing_opp_stuff_rate_generated,
            "_team": team,
            "_pos": pos,
            "_opp": opp,
        }
