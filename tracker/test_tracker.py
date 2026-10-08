#!/usr/bin/env python3
"""Tests for the baseline tracker. From the repo root:

    python -m unittest discover -s tracker -v

Stdlib unittest only; every test uses temp dirs / in-memory SQLite, never the
real DB or predictions/.
"""
import contextlib
import csv
import gzip
import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

import baselines
import injuries
import ledger
import ownership
import report
import scheduled_run
import scoring
import tracker
from score_week import render_markdown_report

CFG = scoring.load_config()
RULES = CFG["scoring"]
# nflverse's own fantasy_points_ppr formula: 4-pt passing TD, 1.0 PPR
NFLVERSE_PPR = dict(RULES, pass_td=4, reception=1.0)


def mem_db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    ledger.init_schema(con)
    return con


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


# --------------------------------------------------------------- append-only

SAMPLE_ROWS = {
    "tr_runs": dict(run_id="r1", run_timestamp="2026-10-08T12:00:00+00:00", season=2026, week=5, run_slot="thu"),
    "tr_pool": dict(run_id="r1", player_id="p1", position="RB"),
    "tr_predictions": dict(run_id="r1", model_name="odds_prod", pick_set="all_k10", rank=1, player_id="p1", position="RB"),
    "tr_scorings": dict(season=2026, week=5, scored_at="x", hit_config_hash="h", hit_config_json="{}"),
    "tr_player_results": dict(scoring_id=1, player_id="p1", position="RB", points=10.0),
    "tr_run_results": dict(scoring_id=1, run_id="r1", model_name="odds_prod", pick_set="all_k10", n_picks=10),
    "tr_percentiles": dict(scoring_id=1, run_id="r1", model_name="odds_prod", pick_set="all_k10", metric="top24_hits"),
    "tr_crowd_results": dict(run_id="r1", model_name="odds_prod", pick_set="all_k10", player_id="p1", computed_at="x"),
    "tr_ownership_snapshots": dict(taken_at="x", source="espn", ok=1),
    "tr_ownership": dict(snapshot_id=1, source="espn", external_id="1"),
}


class TestAppendOnly(unittest.TestCase):
    def setUp(self):
        self.con = mem_db()
        for table, row in SAMPLE_ROWS.items():
            ledger.insert_rows(self.con, table, [row])
        self.con.commit()

    def test_every_ledger_table_has_a_sample_row(self):
        self.assertEqual(set(SAMPLE_ROWS), set(ledger.APPEND_ONLY_TABLES))

    def test_update_is_blocked_on_every_table(self):
        for t in ledger.APPEND_ONLY_TABLES:
            with self.subTest(table=t):
                col = self.con.execute(f"PRAGMA table_info({t})").fetchall()[1]["name"]
                with self.assertRaises(sqlite3.DatabaseError) as cm:
                    self.con.execute(f"UPDATE {t} SET {col} = {col}")
                self.assertIn("append-only", str(cm.exception))

    def test_delete_is_blocked_on_every_table(self):
        for t in ledger.APPEND_ONLY_TABLES:
            with self.subTest(table=t):
                with self.assertRaises(sqlite3.DatabaseError) as cm:
                    self.con.execute(f"DELETE FROM {t}")
                self.assertIn("append-only", str(cm.exception))
                self.assertEqual(self.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0], 1)

    def test_inserts_still_work_and_slot_is_unique(self):
        ledger.insert_rows(self.con, "tr_runs", [dict(SAMPLE_ROWS["tr_runs"], run_id="r2", week=6)])
        with self.assertRaises(sqlite3.IntegrityError):  # same (season, week, slot) twice
            ledger.insert_rows(self.con, "tr_runs", [dict(SAMPLE_ROWS["tr_runs"], run_id="r3")])

    def test_snap_counts_is_a_reloadable_data_table_not_ledger(self):
        self.con.execute("INSERT INTO tr_snap_counts (season, week, player_id, offense_pct) VALUES (2026, 1, 'p', 0.5)")
        self.con.execute("UPDATE tr_snap_counts SET offense_pct = 0.6")
        self.con.execute("DELETE FROM tr_snap_counts")

    def test_schema_init_is_idempotent(self):
        ledger.init_schema(self.con)
        ledger.init_schema(self.con)


# ---------------------------------------------------------------------- dart

def make_pool(n_per_pos=(("QB", 12), ("RB", 30), ("WR", 40), ("TE", 18))):
    pool = []
    for pos, n in n_per_pos:
        for i in range(n):
            pool.append({"player_id": f"{pos}{i:02d}", "position": pos})
    return pool


class TestDart(unittest.TestCase):
    def setUp(self):
        self.pool = make_pool()
        self.sets = {"all_k10": (self.pool, {"RB": 4, "WR": 4, "TE": 2}),
                     "all_k25": (self.pool, {"QB": 3, "RB": 9, "WR": 9, "TE": 4})}

    def test_same_seed_reproduces_every_draw(self):
        a = list(baselines.dart_draws(self.sets, 42, 200))
        b = list(baselines.dart_draws(self.sets, 42, 200))
        self.assertEqual(a, b)

    def test_different_seed_differs(self):
        self.assertNotEqual(list(baselines.dart_draws(self.sets, 1, 20)), list(baselines.dart_draws(self.sets, 2, 20)))

    def test_logged_draw_zero_is_stable_regardless_of_n(self):
        self.assertEqual(next(baselines.dart_draws(self.sets, 7, 1)), next(baselines.dart_draws(self.sets, 7, 1000)))

    def test_pool_order_does_not_change_the_draws(self):
        shuffled = {k: (list(reversed(p)), m) for k, (p, m) in self.sets.items()}
        self.assertEqual(list(baselines.dart_draws(self.sets, 9, 50)), list(baselines.dart_draws(shuffled, 9, 50)))

    def test_draws_match_mix_pool_and_have_no_duplicates(self):
        pos_of = {r["player_id"]: r["position"] for r in self.pool}
        for draw in baselines.dart_draws(self.sets, 3, 100):
            for ps, (_pool, mix) in self.sets.items():
                ids = draw[ps]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(baselines.position_mix([{"position": pos_of[i]} for i in ids]), mix)

    def test_selection_is_roughly_uniform(self):
        counts = {}
        for draw in baselines.dart_draws({"s": (self.pool, {"WR": 4})}, 11, 4000):
            for i in draw["s"]:
                counts[i] = counts.get(i, 0) + 1
        expected = 4000 * 4 / 40
        self.assertTrue(all(abs(c - expected) < 0.25 * expected for c in counts.values()), counts)

    def test_seed_derivation_is_deterministic_and_slot_sensitive(self):
        self.assertEqual(baselines.derive_seed(2026, 5, "thu"), baselines.derive_seed(2026, 5, "thu"))
        self.assertNotEqual(baselines.derive_seed(2026, 5, "thu"), baselines.derive_seed(2026, 5, "sun"))
        self.assertNotEqual(baselines.derive_seed(2026, 5, "thu"), baselines.derive_seed(2026, 6, "thu"))

    def test_short_position_pool_takes_everyone_available(self):
        sets = {"s": ([{"player_id": "a", "position": "TE"}], {"TE": 3})}
        self.assertEqual(list(baselines.dart_draws(sets, 1, 1))[0]["s"], ["a"])

    def test_round_trip_through_the_ledger_reproduces_logged_dart_picks(self):
        """The pool snapshot + O.D.D.S.'s logged picks are enough to regenerate
        the exact distribution at scoring time -- nothing else is stored."""
        con = mem_db()
        rng = np.random.default_rng(0)
        pool = []
        for r in make_pool():
            pool.append(dict(r, run_id="r1", name=r["player_id"], team="T", percent_owned=float(rng.integers(0, 100)),
                             odds_prod_score=float(rng.random()), odds_shadow_score=float(rng.random()),
                             snap_delta=float(rng.normal()), target_share=float(rng.random()),
                             last_week_points=float(rng.integers(0, 30))))
        for r in pool:
            r["in_u50"] = 1 if r["percent_owned"] < 50 else 0
        seed = baselines.derive_seed(2026, 5, "thu")
        preds, sets = tracker.make_predictions("r1", pool, CFG, seed, {})
        ledger.insert_rows(con, "tr_pool", [{k: v for k, v in r.items() if k not in ("snap_games", "snap_recent", "snap_prior",
                                                                                   "last_week_label")} for r in pool])
        ledger.insert_rows(con, "tr_predictions", preds)
        rebuilt = tracker.rebuild_dart_sets(con, "r1")
        self.assertEqual({k: v[1] for k, v in rebuilt.items()}, {k: v[1] for k, v in sets.items()})
        self.assertEqual(list(baselines.dart_draws(rebuilt, seed, 60)), list(baselines.dart_draws(sets, seed, 60)))
        logged = {}
        for p in preds:
            if p["model_name"] == "dart":
                logged.setdefault(p["pick_set"], []).append(p["player_id"])
        self.assertEqual(logged, next(baselines.dart_draws(rebuilt, seed, 1)))
        self.assertTrue(all(p["random_seed"] == seed for p in preds if p["model_name"] == "dart"))

    def test_percentile_midrank(self):
        self.assertEqual(baselines.percentile_midrank(5, [1, 2, 3, 4]), 100.0)
        self.assertEqual(baselines.percentile_midrank(0, [1, 2, 3, 4]), 0.0)
        self.assertEqual(baselines.percentile_midrank(2, [1, 2, 2, 3]), 50.0)  # 1 below + half of 2 ties = 2/4


# ------------------------------------------------------------------- scoring

class TestScoringMath(unittest.TestCase):
    def test_league_settings_full_stat_line(self):
        line = dict(passing_yards=300, passing_tds=3, passing_interceptions=1, rushing_yards=20, rushing_tds=1,
                    sack_fumbles_lost=1, passing_2pt_conversions=1)
        # 12 + 15 - 2 + 2 + 6 - 2 + 2
        self.assertAlmostEqual(scoring.fantasy_points(line, RULES), 33.0)

    def test_skill_player_line_half_ppr(self):
        line = dict(rushing_yards=100, rushing_tds=1, receptions=4, receiving_yards=40, receiving_tds=1)
        self.assertAlmostEqual(scoring.fantasy_points(line, RULES), 10 + 6 + 2 + 4 + 6)

    def test_pass_td_is_five_not_four(self):
        self.assertAlmostEqual(scoring.fantasy_points(dict(passing_tds=1), RULES), 5.0)

    def test_two_point_conversions_counted_across_all_three_columns(self):
        line = dict(passing_2pt_conversions=1, rushing_2pt_conversions=1, receiving_2pt_conversions=1)
        self.assertAlmostEqual(scoring.fantasy_points(line, RULES), 6.0)

    def test_fumbles_lost_sum_the_three_sources(self):
        line = dict(sack_fumbles_lost=1, rushing_fumbles_lost=1, receiving_fumbles_lost=1)
        self.assertAlmostEqual(scoring.fantasy_points(line, RULES), -6.0)

    def test_blank_csv_cells_are_zero(self):
        self.assertEqual(scoring.fantasy_points({"passing_yards": "", "receptions": None}, RULES), 0.0)

    # Real 2025 rows from nflverse's stats_player_week with their own
    # fantasy_points_ppr; our formula, run under nflverse's rules (4-pt pass TD,
    # full PPR), must reproduce it -- a check on the formula, fumbles and 2-pt
    # handling independent of the league rules. `league` is the hand-computed
    # value under this league's settings.
    REAL_ROWS = [
        (dict(passing_yards=150, sack_fumbles_lost=1, rushing_yards=14), 5.4, 5.4),
        (dict(rushing_yards=54, rushing_2pt_conversions=1, receptions=5, receiving_yards=88), 21.2, 18.7),
        (dict(receiving_2pt_conversions=1), 2.0, 2.0),
        (dict(rushing_yards=9, receptions=2, receiving_yards=17, receiving_fumbles_lost=1), 2.6, 1.6),
        (dict(receptions=7, receiving_yards=68, receiving_tds=1), 19.8, 16.3),
    ]

    def test_reproduces_nflverse_ppr_on_real_rows(self):
        for line, ppr, _league in self.REAL_ROWS:
            self.assertAlmostEqual(scoring.fantasy_points(line, NFLVERSE_PPR), ppr, places=3)

    def test_league_values_on_the_same_real_rows(self):
        for line, _ppr, league in self.REAL_ROWS:
            self.assertAlmostEqual(scoring.fantasy_points(line, RULES), league, places=3)

    def test_config_hash_tracks_the_hit_definition_but_not_run_knobs(self):
        base = scoring.config_hash(CFG)
        import copy
        c = copy.deepcopy(CFG)
        c["report"]["bootstrap_resamples"] = 5
        c["run"]["n_draws"] = 7
        self.assertEqual(scoring.config_hash(c), base)
        for section, edit in (("hit", lambda d: d["hit"].__setitem__("top_n_per_position", 12)),
                              ("scoring", lambda d: d["scoring"].__setitem__("pass_td", 4)),
                              ("crowd_hit", lambda d: d["crowd_hit"].__setitem__("owned_pct_threshold", 40))):
            c = copy.deepcopy(CFG)
            edit(c)
            self.assertNotEqual(scoring.config_hash(c), base, section)

    def test_default_hit_definition_is_what_was_asked_for(self):
        self.assertEqual(CFG["hit"]["top_n_per_position"], 24)
        self.assertEqual(CFG["crowd_hit"], {"owned_pct_threshold": 50, "window_days": 14})
        self.assertEqual(CFG["run"]["ks"], [10, 25])
        self.assertEqual(CFG["run"]["n_draws"], 1000)

    def test_position_ranks_use_competition_ranking_across_everyone(self):
        rows = {f"w{i}": {"position": "WR", "points": 30 - i} for i in range(30)}
        rows["tieA"] = {"position": "RB", "points": 10.0}
        rows["tieB"] = {"position": "RB", "points": 10.0}
        rows["tieC"] = {"position": "RB", "points": 9.0}
        ranks = scoring.position_ranks(rows, ("QB", "RB", "WR", "TE"), 24)
        self.assertEqual(ranks["w0"], ("WR", 1, True))
        self.assertTrue(ranks["w23"][2])      # 24th
        self.assertFalse(ranks["w24"][2])     # 25th
        self.assertEqual((ranks["tieA"][1], ranks["tieB"][1], ranks["tieC"][1]), (1, 1, 3))

    def test_tie_across_the_cutoff_is_included_by_competition_rank(self):
        rows = {f"p{i}": {"position": "TE", "points": 100 - i} for i in range(23)}
        rows["t1"] = {"position": "TE", "points": 50.0}
        rows["t2"] = {"position": "TE", "points": 50.0}
        ranks = scoring.position_ranks(rows, ("TE",), 24)
        self.assertEqual((ranks["t1"][1], ranks["t2"][1]), (24, 24))
        self.assertTrue(ranks["t1"][2] and ranks["t2"][2])

    def test_spike_rule_mirrors_the_label_pipeline(self):
        cfg = CFG["hit"]["spike"]
        self.assertEqual(scoring.spike_result([8, 8, 8], 12.0, cfg)[0], True)     # 12 >= max(12, 10)
        self.assertEqual(scoring.spike_result([8, 8, 8], 11.9, cfg)[0], False)
        self.assertEqual(scoring.spike_result([1, 1, 1], 5.0, cfg)[0], False)     # 3x baseline but under the 10-pt floor
        self.assertEqual(scoring.spike_result([1, 1, 1], 10.0, cfg)[0], True)
        self.assertEqual(scoring.spike_result([20, 8, 8, 8], 12.0, cfg)[0], True)  # only the last 3 games count
        self.assertEqual(scoring.spike_result([8, 8], 30.0, cfg), (None, None, None))  # not enough history


class TestStatsStore(unittest.TestCase):
    def write_season(self, d, season, rows):
        cols = ["player_id", "season", "week", "season_type", "position", "team", "passing_yards", "receptions",
                "receiving_yards", "target_share"]
        with open(Path(d) / f"stats_player_week_{season}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow({c: r.get(c, "") for c in cols} | {"season": season, "season_type": r.get("season_type", "REG")})

    def test_history_walks_back_across_seasons_and_stops_at_a_gap(self):
        with tempfile.TemporaryDirectory() as d:
            self.write_season(d, 2018, [dict(player_id="p", week=17, position="WR", team="X", receptions=10)])
            self.write_season(d, 2020, [dict(player_id="p", week=1, position="WR", team="X", receptions=2),
                                        dict(player_id="p", week=2, position="WR", team="X", receptions=4)])
            self.write_season(d, 2021, [dict(player_id="p", week=1, position="WR", team="X", receptions=6)])
            store = scoring.StatsStore(d, RULES)
            h = store.history_before("p", 2021, 2, 5)
            self.assertEqual([(s, w) for s, w, *_ in h], [(2020, 1), (2020, 2), (2021, 1)])  # 2019 missing: stop, don't bridge to 2018
            self.assertEqual([g[2] for g in h], [1.0, 2.0, 3.0])

    def test_only_regular_season_and_strictly_prior_weeks(self):
        with tempfile.TemporaryDirectory() as d:
            self.write_season(d, 2026, [dict(player_id="p", week=1, position="WR", team="X", receptions=2),
                                        dict(player_id="p", week=2, position="WR", team="X", receptions=2, season_type="POST"),
                                        dict(player_id="p", week=3, position="WR", team="X", receptions=2)])
            store = scoring.StatsStore(d, RULES)
            self.assertEqual([(s, w) for s, w, *_ in store.history_before("p", 2026, 3, 5)], [(2026, 1)])
            self.assertEqual(store.max_week(2026), 3)


# ---------------------------------------------------------------- slot guard

def kick(s):
    return tracker.parse_kickoff_utc(s)


WEEK4_2026 = [kick(s) for s in ("2026-10-01T20:15:00", "2026-10-04T09:30:00", "2026-10-04T13:00:00", "2026-10-04T16:25:00",
                                "2026-10-04T20:20:00", "2026-10-05T20:15:00")]


class TestSlotWindow(unittest.TestCase):
    def test_thu_closes_at_thursday_kickoff_converted_from_eastern(self):
        thu = utc("2026-10-02T00:15:00")  # 20:15 EDT = 00:15 UTC next day
        self.assertEqual(tracker.slot_cutoff(WEEK4_2026, "thu")[0], thu)
        self.assertTrue(tracker.check_slot_window(WEEK4_2026, "thu", thu - timedelta(seconds=1))[0])
        ok, _c, msg = tracker.check_slot_window(WEEK4_2026, "thu", thu)
        self.assertFalse(ok)
        self.assertIn("REFUSED", msg)

    def test_sun_closes_at_the_first_one_pm_kickoff_not_the_international_game(self):
        cutoff = utc("2026-10-04T17:00:00")  # 13:00 EDT; the 09:30 EDT (13:30 UTC) London game doesn't close it
        self.assertEqual(tracker.slot_cutoff(WEEK4_2026, "sun")[0], cutoff)
        self.assertTrue(tracker.check_slot_window(WEEK4_2026, "sun", utc("2026-10-04T13:31:00"))[0])  # London game under way
        self.assertTrue(tracker.check_slot_window(WEEK4_2026, "sun", cutoff - timedelta(minutes=1))[0])
        ok, _c, msg = tracker.check_slot_window(WEEK4_2026, "sun", cutoff)
        self.assertFalse(ok)
        self.assertIn("REFUSED", msg)

    def test_international_game_is_treated_like_a_thursday_game(self):
        """No Thursday game, but a 9:30am ET international game: it precedes the
        main slate, so the week HAS a thu slot (closing at that kickoff) and the
        sun slot still closes at 1 PM ET."""
        intl = [kick(s) for s in ("2026-10-18T09:30:00", "2026-10-18T13:00:00", "2026-10-18T16:25:00", "2026-10-19T20:15:00")]
        self.assertEqual(tracker.slot_cutoff(intl, "thu")[0], utc("2026-10-18T13:30:00"))
        self.assertEqual(tracker.slot_cutoff(intl, "sun")[0], utc("2026-10-18T17:00:00"))
        self.assertEqual(tracker.main_slate_start(intl), utc("2026-10-18T17:00:00"))

    def test_main_slate_is_defined_by_eastern_wall_clock_across_dst(self):
        nov = [kick(s) for s in ("2026-11-08T09:30:00", "2026-11-08T13:00:00")]  # EST: 1 PM ET = 18:00 UTC
        self.assertEqual(tracker.slot_cutoff(nov, "sun")[0], utc("2026-11-08T18:00:00"))
        self.assertEqual(tracker.slot_cutoff(nov, "thu")[0], utc("2026-11-08T14:30:00"))

    def test_sunday_with_no_one_pm_or_later_game_falls_back_to_its_first_kickoff(self):
        odd = [kick(s) for s in ("2026-10-15T20:15:00", "2026-10-18T09:30:00")]
        self.assertEqual(tracker.slot_cutoff(odd, "sun")[0], utc("2026-10-18T13:30:00"))
        self.assertEqual(tracker.slot_cutoff(odd, "thu")[0], utc("2026-10-16T00:15:00"))

    def test_sun_run_is_allowed_after_the_thursday_game_started(self):
        self.assertTrue(tracker.check_slot_window(WEEK4_2026, "sun", utc("2026-10-03T15:00:00"))[0])

    def test_week_without_a_thursday_game_has_no_thu_slot(self):
        no_thu = [kick(s) for s in ("2026-11-15T13:00:00", "2026-11-15T16:25:00", "2026-11-16T20:15:00")]
        ok, cutoff, msg = tracker.check_slot_window(no_thu, "thu", utc("2026-11-10T12:00:00"))
        self.assertFalse(ok)
        self.assertIsNone(cutoff)
        self.assertIn("no Thursday game", msg)
        self.assertTrue(tracker.check_slot_window(no_thu, "sun", utc("2026-11-10T12:00:00"))[0])

    def test_saturday_game_week_caught_by_timestamp_not_day_of_week(self):
        late = [kick(s) for s in ("2026-12-19T16:30:00", "2026-12-19T20:00:00", "2026-12-20T13:00:00", "2026-12-21T20:15:00")]
        self.assertEqual(tracker.slot_cutoff(late, "thu")[0], kick("2026-12-19T16:30:00"))  # EST: 21:30 UTC
        self.assertEqual(tracker.slot_cutoff(late, "thu")[0], utc("2026-12-19T21:30:00"))
        self.assertEqual(tracker.slot_cutoff(late, "sun")[0], utc("2026-12-20T18:00:00"))

    def test_thanksgiving_triple_header_uses_the_first_thursday_game(self):
        tg = [kick(s) for s in ("2026-11-26T12:30:00", "2026-11-26T16:30:00", "2026-11-26T20:20:00", "2026-11-29T13:00:00")]
        self.assertEqual(tracker.slot_cutoff(tg, "thu")[0], utc("2026-11-26T17:30:00"))

    def test_dst_boundary_uses_est_in_january(self):
        jan = [kick("2027-01-03T13:00:00")]
        self.assertEqual(tracker.slot_cutoff(jan, "sun")[0], utc("2027-01-03T18:00:00"))

    def test_prediction_path_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            self.assertEqual(tracker.choose_prediction_path(d, 2026, 5, "thu").name, "2026_week05.json")
            (d / "2026_week05.json").write_text("{}")
            self.assertEqual(tracker.choose_prediction_path(d, 2026, 5, "sun").name, "2026_week05_sunday.json")
            with self.assertRaises(tracker.Refused):
                tracker.choose_prediction_path(d, 2026, 5, "thu")
            (d / "2026_week05_sunday.json").write_text("{}")
            with self.assertRaises(tracker.Refused):
                tracker.choose_prediction_path(d, 2026, 5, "sun")
            # a week with no Thursday game: the sun run IS the first snapshot and keeps the bare name
            self.assertEqual(tracker.choose_prediction_path(d, 2026, 6, "sun").name, "2026_week06.json")


class TestLogRefusesAfterKickoff(unittest.TestCase):
    """CLI-level: a refused `log` must write NOTHING and never invoke O.D.D.S."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "t.db"
        con = ledger.connect(self.db)
        con.execute("CREATE TABLE nfl_games (season INTEGER, week INTEGER, game_id TEXT, kickoff_utc TEXT, home_team TEXT, "
                    "away_team TEXT, home_score INTEGER, away_score INTEGER, is_playoffs INTEGER DEFAULT 0)")
        games = [("2026_04_PIT_CLE", "2026-10-01T20:15:00"), ("2026_04_IND_WAS", "2026-10-04T09:30:00"),
                 ("2026_04_TEN_BAL", "2026-10-04T13:00:00")]
        con.executemany("INSERT INTO nfl_games (season, week, game_id, kickoff_utc, home_team, away_team) VALUES (2026, 4, ?, ?, 'A', 'B')", games)
        con.executemany("INSERT INTO nfl_games (season, week, game_id, kickoff_utc, home_team, away_team) VALUES (2026, 5, ?, ?, 'A', 'B')",
                        [("2026_05_X", "2026-10-11T13:00:00")])  # week 5: no Thursday game
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def run_log(self, week, slot, as_of):
        argv = ["log", "--season", "2026", "--week", str(week), "--slot", slot, "--as-of", as_of, "--db", str(self.db),
                "--predictions-dir", str(self.dir / "preds"), "--export-dir", str(self.dir / "export"),
                "--raw-dir", str(self.dir / "raw")]
        with mock.patch.object(tracker.subprocess, "run", side_effect=AssertionError("O.D.D.S. must not run on a refusal")):
            return tracker.main(argv)

    def count(self, table):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            con.close()

    def assert_nothing_written(self):
        for t in ("tr_runs", "tr_pool", "tr_predictions"):
            self.assertEqual(self.count(t), 0)
        self.assertFalse((self.dir / "preds").exists())
        self.assertFalse((self.dir / "export").exists())

    def test_thu_run_after_thursday_kickoff_is_refused(self):
        self.assertEqual(self.run_log(4, "thu", "2026-10-02T00:15:00+00:00"), 2)
        self.assert_nothing_written()

    def test_sun_run_after_the_one_pm_kickoff_is_refused(self):
        self.assertEqual(self.run_log(4, "sun", "2026-10-04T17:00:00+00:00"), 2)
        self.assert_nothing_written()

    def test_thu_run_in_a_week_with_no_thursday_game_is_refused(self):
        self.assertEqual(self.run_log(5, "thu", "2026-10-07T12:00:00+00:00"), 2)
        self.assert_nothing_written()

    def insert_run(self, week, slot):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO tr_runs (run_id, run_timestamp, season, week, run_slot) VALUES ('x','t',2026,?,?)", (week, slot))
        con.commit()
        con.close()

    def test_run_already_in_the_ledger_is_refused(self):
        self.insert_run(4, "sun")
        self.assertEqual(self.run_log(4, "sun", "2026-10-03T15:00:00+00:00"), 2)
        self.assertEqual(self.count("tr_runs"), 1)

    # ---- `log --auto`, the scheduled-task form: "nothing to do" exits 0, a closed window still refuses

    def run_auto(self, slot, as_of):
        argv = ["log", "--auto", "--slot", slot, "--as-of", as_of, "--db", str(self.db),
                "--predictions-dir", str(self.dir / "preds"), "--export-dir", str(self.dir / "export"),
                "--raw-dir", str(self.dir / "raw")]
        out = io.StringIO()
        with mock.patch.object(tracker.subprocess, "run", side_effect=AssertionError("O.D.D.S. must not run")), \
                contextlib.redirect_stdout(out):
            return tracker.main(argv), out.getvalue()

    def test_current_week_is_the_earliest_week_with_a_game_still_to_play(self):
        con = ledger.connect(self.db)
        try:
            self.assertEqual(tracker.current_week(con, 2026, utc("2026-10-01T12:00:00")), 4)   # Thursday morning
            self.assertEqual(tracker.current_week(con, 2026, utc("2026-10-04T14:00:00")), 4)   # Sunday, 1 PM game to come
            self.assertEqual(tracker.current_week(con, 2026, utc("2026-10-04T17:00:00")), 5)   # week 4's last game kicked off
            self.assertIsNone(tracker.current_week(con, 2026, utc("2026-10-11T17:00:00")))
        finally:
            con.close()

    def test_auto_skips_quietly_when_the_week_has_no_such_slot(self):
        rc, out = self.run_auto("thu", "2026-10-08T14:00:00+00:00")  # week 5 here: one Sunday 1 PM game only
        self.assertEqual(rc, 0)
        self.assertIn("[SKIP] 2026 week 5 has no thu slot", out)
        self.assert_nothing_written()

    def test_auto_skips_quietly_when_the_slot_is_already_logged(self):
        self.insert_run(4, "sun")
        rc, out = self.run_auto("sun", "2026-10-04T14:00:00+00:00")
        self.assertEqual(rc, 0)
        self.assertIn("already in the ledger", out)
        self.assertEqual(self.count("tr_runs"), 1)

    def test_auto_still_refuses_loudly_once_the_window_has_closed(self):
        rc, _out = self.run_auto("thu", "2026-10-02T14:00:00+00:00")  # Friday: week 4's Thursday game has been played
        self.assertEqual(rc, 2)
        self.assert_nothing_written()

    def test_auto_never_logs_a_slot_more_than_a_week_out(self):
        rc, out = self.run_auto("sun", "2026-09-01T14:00:00+00:00")
        self.assertEqual(rc, 0)
        self.assertIn("too early to log", out)
        self.assert_nothing_written()

    def test_auto_with_no_games_left_is_a_quiet_skip(self):
        rc, out = self.run_auto("sun", "2027-02-01T14:00:00+00:00")
        self.assertEqual(rc, 0)
        self.assertIn("[SKIP] no upcoming", out)
        self.assert_nothing_written()


# ------------------------------------- prior weeks must be loaded in the DB

class TestPriorWeeksLoaded(unittest.TestCase):
    """`log` for week W refuses unless every already-played game of weeks < W
    is in the DB the model scores from (player_week_stats + labels)."""

    AS_OF = "2026-10-03T15:00:00+00:00"   # Saturday of week 4

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "t.db"
        (self.dir / "raw").mkdir()
        con = ledger.connect(self.db)
        con.execute("CREATE TABLE nfl_games (season INTEGER, week INTEGER, game_id TEXT, kickoff_utc TEXT, home_team TEXT, "
                    "away_team TEXT, home_score INTEGER, away_score INTEGER, is_playoffs INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE player_week_stats (season INTEGER, week INTEGER, player_id TEXT, team TEXT)")
        con.execute("CREATE TABLE labels_player_week (season INTEGER, week INTEGER, player_id TEXT)")
        games = [(2, "w2a", "2026-09-20T13:00:00", "BAL", "TEN", 24, 10), (2, "w2b", "2026-09-21T20:15:00", "LV", "KC", 17, 27),
                 (3, "w3a", "2026-09-27T13:00:00", "TEN", "KC", 13, 30), (3, "w3b", "2026-09-28T20:15:00", "BAL", "LV", 20, 17),
                 (4, "w4a", "2026-10-01T20:15:00", "KC", "BAL", None, None), (4, "w4b", "2026-10-04T13:00:00", "LV", "TEN", None, None)]
        con.executemany("INSERT INTO nfl_games (season, week, game_id, kickoff_utc, home_team, away_team, home_score, away_score) "
                        "VALUES (2026, ?, ?, ?, ?, ?, ?, ?)", games)
        for wk in (2, 3):
            for team in ("BAL", "TEN", "KC", "LV"):
                self.add_player(con, wk, team)
        con.commit()
        con.close()
        with open(self.dir / "raw" / "stats_player_week_2026.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["player_id", "season", "week", "season_type", "position", "team"])
            w.writeheader()
            w.writerow(dict(player_id="x", season=2026, week=3, season_type="REG", position="WR", team="KC"))

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def add_player(con, week, team, label=True):
        pid = f"{team}-w{week}"
        con.execute("INSERT INTO player_week_stats VALUES (2026, ?, ?, ?)", (week, pid, team))
        if label:
            con.execute("INSERT INTO labels_player_week VALUES (2026, ?, ?)", (week, pid))

    def sql(self, *statements):
        con = sqlite3.connect(self.db)
        for s in statements:
            con.execute(s)
        con.commit()
        con.close()

    def problems(self, week=4, as_of=None):
        con = ledger.connect(self.db)
        try:
            return tracker.played_games_not_loaded(con, 2026, week, utc((as_of or self.AS_OF)[:19]))
        finally:
            con.close()

    def run_log(self):
        """-> (exit code, stderr, O.D.D.S. was started)."""
        started = []

        def fake_run(cmd, **_kw):
            started.append(cmd)
            return mock.Mock(returncode=1, stdout="", stderr="stub")  # never gets far enough to write anything
        argv = ["log", "--season", "2026", "--week", "4", "--slot", "sun", "--as-of", self.AS_OF, "--db", str(self.db),
                "--predictions-dir", str(self.dir / "preds"), "--export-dir", str(self.dir / "export"),
                "--raw-dir", str(self.dir / "raw"), "--skip-ownership"]
        err = io.StringIO()
        with mock.patch.object(tracker.subprocess, "run", side_effect=fake_run), contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                rc = tracker.main(argv)
            except RuntimeError:  # the stubbed score_week "failing" = the freshness checks all passed
                rc = "reached O.D.D.S."
        return rc, err.getvalue(), bool(started)

    def assert_refused_with_nothing_written(self, expect):
        rc, err, started = self.run_log()
        self.assertEqual(rc, 2)
        self.assertFalse(started)                      # refused BEFORE O.D.D.S. ran
        self.assertIn("[REFUSED] the DB is missing data", err)
        self.assertIn(expect, err)
        self.assert_points_at_the_capped_load(err)
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tr_runs").fetchone()[0], 0)
        finally:
            con.close()
        self.assertFalse((self.dir / "preds").exists())

    def assert_points_at_the_capped_load(self, err):
        """The fix a refusal recommends must not itself pull week 4 into the DB."""
        self.assertIn("load_nflverse_into_history_SAFE_v3.py --seasons 2026 --through-week 3", err)
        self.assertIn("generate_labels_and_breakouts.py --seasons <all loaded seasons incl. 2026> --through-week 3", err)

    def test_prior_week_not_final_in_nfl_games_points_at_the_capped_load(self):
        self.sql("UPDATE nfl_games SET home_score = NULL WHERE game_id = 'w3b'")
        rc, err, started = self.run_log()
        self.assertEqual((rc, started), (2, False))
        self.assertIn("week 3 isn't fully final in nfl_games", err)
        self.assert_points_at_the_capped_load(err)

    def test_complete_db_passes_the_check(self):
        self.assertEqual(self.problems(), [])
        self.assertEqual(self.run_log()[0], "reached O.D.D.S.")

    def test_week_being_logged_is_not_required_even_though_its_thursday_game_was_played(self):
        self.assertEqual(self.problems(), [])          # w4a kicked off Thursday; no week-4 rows in the DB
        self.assertEqual(self.problems(week=5, as_of="2026-10-05T15:00:00+00:00"),
                         ["week 4 BAL@KC: no player stats for BAL/KC", "week 4 TEN@LV: no player stats for TEN/LV"])

    def test_prior_week_game_with_no_stats_is_refused(self):
        """The Monday-night game: its score is in nfl_games and the stats CSV
        reaches week 3, so the older guards pass -- but its players never got loaded."""
        self.sql("DELETE FROM player_week_stats WHERE week = 3 AND team IN ('BAL', 'LV')")
        self.assertEqual(self.problems(), ["week 3 LV@BAL: no player stats for LV/BAL"])
        self.assert_refused_with_nothing_written("week 3 LV@BAL: no player stats for LV/BAL")

    def test_hole_in_an_earlier_week_and_a_half_loaded_game_are_both_caught(self):
        self.sql("DELETE FROM player_week_stats WHERE week = 2 AND team = 'KC'")
        self.assertEqual(self.problems(), ["week 2 KC@LV: no player stats for KC"])
        self.assert_refused_with_nothing_written("week 2 KC@LV: no player stats for KC")

    def test_stats_loaded_without_labels_is_refused(self):
        self.sql("DELETE FROM labels_player_week WHERE week = 3")
        self.assertEqual(self.problems(), ["week 3: 4 loaded player-week(s) have no labels_player_week row"])
        self.assert_refused_with_nothing_written("have no labels_player_week row")

    # ---- and the DB must hold NOTHING for the week being logged

    def load_thursday_game(self, labels=True):
        """What the loader does when run after week 4's Thursday game (BAL@KC)."""
        con = sqlite3.connect(self.db)
        for team in ("KC", "BAL"):
            for i in range(2):
                con.execute("INSERT INTO player_week_stats VALUES (2026, 4, ?, ?)", (f"{team}-w4-{i}", team))
                if labels:
                    con.execute("INSERT INTO labels_player_week VALUES (2026, 4, ?)", (f"{team}-w4-{i}",))
        con.commit()
        con.close()

    def target_rows(self, week=4):
        con = ledger.connect(self.db)
        try:
            return tracker.target_week_rows_in_db(con, 2026, week)
        finally:
            con.close()

    def assert_target_week_refusal(self, expect):
        rc, err, started = self.run_log()
        self.assertEqual(rc, 2)
        self.assertFalse(started)                      # refused BEFORE O.D.D.S. ran
        self.assertIn("[REFUSED] the DB already holds rows for the week being logged (2026 week 4)", err)
        self.assertIn(expect, err)
        self.assertIn("load with --through-week 3", err)
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tr_runs").fetchone()[0], 0)
        finally:
            con.close()
        self.assertFalse((self.dir / "preds").exists())

    def test_clean_target_week_passes(self):
        self.assertEqual(self.target_rows(), [])

    def test_stats_and_labels_for_the_week_being_logged_are_refused_naming_the_game(self):
        self.load_thursday_game()
        expect = "week 4 BAL@KC: 4 player_week_stats row(s), 4 labels_player_week row(s)"
        self.assertEqual(self.target_rows(), [expect])   # only the played game is named, not TEN@LV
        self.assert_target_week_refusal(expect)

    def test_stats_without_labels_for_the_week_being_logged_are_refused(self):
        self.load_thursday_game(labels=False)
        self.assert_target_week_refusal("week 4 BAL@KC: 4 player_week_stats row(s), 0 labels_player_week row(s)")

    def test_labels_without_stats_for_the_week_being_logged_are_refused(self):
        self.sql("INSERT INTO labels_player_week VALUES (2026, 4, 'orphan')")
        self.assert_target_week_refusal("week 4, no matching game: 0 player_week_stats row(s), 1 labels_player_week row(s)")

    def test_week_one_is_guarded_too(self):
        self.sql("INSERT INTO nfl_games (season, week, game_id, kickoff_utc, home_team, away_team) "
                 "VALUES (2026, 1, 'w1', '2026-09-10T20:15:00', 'KC', 'BAL')",
                 "INSERT INTO player_week_stats VALUES (2026, 1, 'kc1', 'KC')")
        self.assertEqual(self.target_rows(week=1), ["week 1 BAL@KC: 1 player_week_stats row(s), 0 labels_player_week row(s)"])
        con = ledger.connect(self.db)
        try:
            with self.assertRaises(tracker.Refused):
                tracker.check_data_fresh(con, None, 2026, 1, utc("2026-09-09T12:00:00"))
        finally:
            con.close()

    def test_postponed_game_that_has_not_kicked_off_is_not_required(self):
        self.sql("DELETE FROM player_week_stats WHERE week = 3 AND team IN ('BAL', 'LV')",
                 "UPDATE nfl_games SET kickoff_utc = '2026-10-06T20:15:00', home_score = NULL, away_score = NULL WHERE game_id = 'w3b'")
        self.assertEqual(self.problems(), [])

    def test_historical_team_codes_are_normalized_on_both_sides(self):
        self.sql("UPDATE nfl_games SET home_team = 'OAK' WHERE home_team = 'LV'", "UPDATE nfl_games SET away_team = 'OAK' WHERE away_team = 'LV'")
        self.assertEqual(tracker.norm_team("OAK"), "LV")
        self.assertEqual(self.problems(), [])


# ------------------------------------------- international week: slot pool

class TestSlotPool(unittest.TestCase):
    """The sun pool only holds players whose game kicks off at or after the
    slot's cutoff -- Thursday AND international/early-Sunday players are out,
    whenever the run happens."""

    # 2026 week 4: Thursday PIT@CLE, 9:30am ET London IND@WAS, then the 1 PM slate; DEN is on a bye here
    KICKOFFS = {"PIT": (kick("2026-10-01T20:15:00"), "CLE"), "CLE": (kick("2026-10-01T20:15:00"), "PIT"),
                "IND": (kick("2026-10-04T09:30:00"), "WAS"), "WAS": (kick("2026-10-04T09:30:00"), "IND"),
                "TEN": (kick("2026-10-04T13:00:00"), "BAL"), "BAL": (kick("2026-10-04T13:00:00"), "TEN"),
                "KC": (kick("2026-10-04T16:25:00"), "LV"), "LV": (kick("2026-10-04T16:25:00"), "KC")}
    SUN = utc("2026-10-04T17:00:00")
    THU = utc("2026-10-02T00:15:00")

    def pool(self, *teams):
        return [{"player_id": f"{t}-{i}", "team": t} for t in teams for i in range(2)]

    def test_sun_pool_excludes_thursday_and_international_players(self):
        kept, out = tracker.split_pool_at_cutoff(self.pool("PIT", "CLE", "IND", "WAS", "TEN", "BAL", "KC", "DEN"),
                                                 self.KICKOFFS, {}, self.SUN)
        self.assertEqual({r["team"] for r in out}, {"PIT", "CLE", "IND", "WAS"})
        self.assertEqual({r["team"] for r in kept}, {"TEN", "BAL", "KC", "DEN"})  # 1 PM game itself is in; a bye team is untouched

    def test_players_whose_team_has_no_game_are_split_off_first(self):
        playing = set(self.KICKOFFS)
        pool = self.pool("PIT", "TEN", "DEN", "KC") + [{"player_id": "traded-off-the-bye-team", "team": "DEN"},
                                                        {"player_id": "traded-onto-the-bye-team", "team": "KC"}]
        kept, idle = tracker.split_pool_no_game(pool, playing, {"traded-off-the-bye-team": "TEN", "traded-onto-the-bye-team": "DEN"})
        self.assertEqual(sorted(r["player_id"] for r in idle), ["DEN-0", "DEN-1", "traded-onto-the-bye-team"])
        self.assertEqual(len(kept), 7)                             # Thursday players are still here: that's the cutoff's job
        self.assertEqual(tracker.split_pool_no_game(pool[:4], playing, {}), (pool[:4], []))

    def test_teams_with_a_game_does_not_depend_on_a_usable_kickoff_time(self):
        con = mem_db()
        con.execute("CREATE TABLE nfl_games (season INTEGER, week INTEGER, game_id TEXT, kickoff_utc TEXT, home_team TEXT, "
                    "away_team TEXT, is_playoffs INTEGER DEFAULT 0)")
        con.executemany("INSERT INTO nfl_games VALUES (2026, 5, ?, ?, ?, ?, 0)",
                        [("a", "2026-10-11T13:00:00", "TEN", "BAL"), ("b", "not a time", "OAK", "DEN"), ("c", "", "KC", "PIT")])
        con.execute("INSERT INTO nfl_games VALUES (2026, 6, 'd', '2026-10-18T13:00:00', 'CAR', 'NO', 0)")
        self.assertEqual(tracker.teams_with_a_game(con, 2026, 5), {"TEN", "BAL", "LV", "DEN", "KC", "PIT"})

    def test_thu_pool_excludes_nobody(self):
        pool = self.pool("PIT", "IND", "TEN", "KC")
        kept, out = tracker.split_pool_at_cutoff(pool, self.KICKOFFS, {}, self.THU)
        self.assertEqual((kept, out), (pool, []))

    def test_team_is_resolved_like_the_kickoff_guard_roster_team_first(self):
        pool = [{"player_id": "traded-to-london", "team": "TEN"}, {"player_id": "traded-away", "team": "IND"}]
        kept, out = tracker.split_pool_at_cutoff(pool, self.KICKOFFS, {"traded-to-london": "WAS", "traded-away": "BAL"}, self.SUN)
        self.assertEqual([r["player_id"] for r in out], ["traded-to-london"])
        self.assertEqual([r["player_id"] for r in kept], ["traded-away"])


class TestLogInternationalWeek(unittest.TestCase):
    """CLI-level `log` for a week with an international game, O.D.D.S. itself
    replaced by a stub that writes a scored pool covering EVERY team -- which is
    what score_week.py does on a run made before any of the week's games."""

    TEAMS = ("PIT", "CLE", "IND", "WAS", "TEN", "BAL", "KC", "LV")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "t.db"
        (self.dir / "raw").mkdir()
        con = ledger.connect(self.db)
        con.execute("CREATE TABLE nfl_games (season INTEGER, week INTEGER, game_id TEXT, kickoff_utc TEXT, home_team TEXT, "
                    "away_team TEXT, home_score INTEGER, away_score INTEGER, is_playoffs INTEGER DEFAULT 0)")
        con.execute("INSERT INTO nfl_games VALUES (2026, 3, 'w3', '2026-09-27T13:00:00', 'PIT', 'CLE', 20, 17, 0)")
        con.execute("CREATE TABLE player_week_stats (season INTEGER, week INTEGER, player_id TEXT, team TEXT)")
        con.execute("CREATE TABLE labels_player_week (season INTEGER, week INTEGER, player_id TEXT)")
        for pid, team in (("pit1", "PIT"), ("cle1", "CLE")):
            con.execute("INSERT INTO player_week_stats VALUES (2026, 3, ?, ?)", (pid, team))
            con.execute("INSERT INTO labels_player_week VALUES (2026, 3, ?)", (pid,))
        con.executemany("INSERT INTO nfl_games (season, week, game_id, kickoff_utc, home_team, away_team) VALUES (2026, 4, ?, ?, ?, ?)",
                        [("thu", "2026-10-01T20:15:00", "CLE", "PIT"), ("london", "2026-10-04T09:30:00", "WAS", "IND"),
                         ("early", "2026-10-04T13:00:00", "BAL", "TEN"), ("late", "2026-10-04T16:25:00", "LV", "KC")])
        con.commit()
        con.close()
        cols = ["player_id", "season", "week", "season_type", "position", "team", "receptions", "receiving_yards", "target_share"]
        self.scored_pool = []
        with open(self.dir / "raw" / "stats_player_week_2026.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for t in self.TEAMS:
                for i, pos in enumerate(("RB", "RB", "WR", "WR", "WR", "TE")):
                    pid = f"{t}-{pos}{i}"
                    w.writerow(dict(player_id=pid, season=2026, week=3, season_type="REG", position=pos, team=t,
                                    receptions=i, receiving_yards=10 * i, target_share=0.1))
                    self.scored_pool.append({"player_id": pid, "name": pid, "pos": pos, "team": t,
                                             "score": round(0.9 - 0.01 * len(self.scored_pool), 4)})

    def tearDown(self):
        self.tmp.cleanup()

    def fake_run(self, cmd, **_kw):
        if "rev-parse" in cmd:
            return mock.Mock(returncode=0, stdout="deadbeef\n", stderr="")
        self.score_week_cmd = cmd
        out = Path(cmd[cmd.index("--json-out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"scored_pool": self.scored_pool, "top": [], "model": {"git_commit": "m1"}})
        out.write_text(payload, encoding="utf-8")
        out.with_name(out.stem + "_shadow.json").write_text(payload, encoding="utf-8")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def log(self, slot, as_of):
        argv = ["log", "--season", "2026", "--week", "4", "--slot", slot, "--as-of", as_of, "--db", str(self.db),
                "--predictions-dir", str(self.dir / "preds"), "--export-dir", str(self.dir / "export"),
                "--raw-dir", str(self.dir / "raw"), "--skip-ownership"]
        with mock.patch.object(tracker.subprocess, "run", side_effect=self.fake_run), \
                contextlib.redirect_stdout(io.StringIO()):
            return tracker.main(argv)

    def rows(self, sql):
        con = sqlite3.connect(self.db)
        con.row_factory = sqlite3.Row
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def test_sun_run_made_before_any_game_still_leaves_out_thursday_and_international_players(self):
        self.assertEqual(self.log("sun", "2026-09-30T15:00:00+00:00"), 0)   # Wednesday: nothing has kicked off
        run = self.rows("SELECT * FROM tr_runs")[0]
        self.assertEqual(run["kickoff_cutoff_utc"], "2026-10-04T17:00:00+00:00")   # 1 PM ET, not the 9:30 London game
        self.assertEqual({r["team"] for r in self.rows("SELECT team FROM tr_pool")}, {"TEN", "BAL", "KC", "LV"})
        self.assertEqual(run["pool_size"], 24)
        early_teams = ("PIT-", "CLE-", "IND-", "WAS-")
        picked = [r["player_id"] for r in self.rows("SELECT player_id FROM tr_predictions")]
        self.assertTrue(picked)
        self.assertFalse([p for p in picked if p.startswith(early_teams)])  # no model, baseline or dart pick from those games
        cfg = json.loads(run["run_config_json"])
        self.assertEqual(cfg["slot_rule"], tracker.SLOT_RULE)
        self.assertEqual(len(cfg["excluded_before_cutoff"]), 24)
        self.assertTrue(all(p.startswith(early_teams) for p in cfg["excluded_before_cutoff"]))
        self.assertIn("--weekly-roster", self.score_week_cmd)

    def test_sun_run_is_accepted_between_the_international_kickoff_and_one_pm(self):
        self.assertEqual(self.log("sun", "2026-10-04T16:59:00+00:00"), 0)
        self.assertEqual(self.log("sun", "2026-10-04T16:59:30+00:00"), 2)   # and only once

    def test_thu_run_keeps_the_whole_pool_international_players_included(self):
        self.assertEqual(self.log("thu", "2026-09-30T15:00:00+00:00"), 0)
        self.assertEqual({r["team"] for r in self.rows("SELECT team FROM tr_pool")}, set(self.TEAMS))
        self.assertEqual(json.loads(self.rows("SELECT run_config_json FROM tr_runs")[0][0])["excluded_before_cutoff"], [])


# ------------------------------------------------- injury designations: pool rule

INJURY_COLS = ["season", "season_type", "game_type", "team", "week", "gsis_id", "position", "full_name", "report_status",
               "practice_status"]


def injury_csv(rows):
    """rows: (week, gsis_id, report_status[, game_type]) -> the nflverse file's bytes."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=INJURY_COLS)
    w.writeheader()
    for week, pid, status, *rest in rows:
        w.writerow(dict(season=2026, season_type="REG", game_type=rest[0] if rest else "REG", team="X", week=week, gsis_id=pid,
                        position="WR", full_name=pid, report_status=status, practice_status="Limited Participation in Practice"))
    return buf.getvalue().encode("utf-8")


NOW = utc("2026-10-11T14:00:00")   # a Sunday, 07:00 Pacific
WEEK5_REPORT = injury_csv([(5, "out1", "Out"), (5, "dbt1", "Doubtful"), (5, "q1", "Questionable"), (5, "practice_only", ""),
                           (4, "last_week_out", "Out"), (5, "playoff_row", "Out", "WC")])
EXCLUDE = ("Out", "Doubtful")


def fetched(raw=WEEK5_REPORT, age_hours=8, ok=True, error=None):
    return lambda season: {"ok": ok, "raw_bytes": raw if ok else None, "error": error, "url": "u",
                           "last_modified": (NOW - timedelta(hours=age_hours)) if (ok and age_hours is not None) else None}


# what score_week.py writes as JSON, cut down to what its markdown renderer reads
PREDICTION_OUTPUT = {"season": 2026, "week": 5, "generated_at": "t", "model": {"filename": "m.joblib", "git_commit": "abc"},
          "counts": dict(total_eligible=3, after_position_filter=3, stable_scored=3, released_from_offseason_suppression=0,
                         unavailable_suppressed=0, already_played=0, team_changed_suppressed=0, new_competitor_suppressed=0,
                         new_competitor_via_production_only=0, new_competitor_via_draft_only=0, new_competitor_via_both=0),
          "top": [{"rank": 1, "player_id": "a", "name": "Alpha One", "pos": "WR", "team": "MIN", "score": 0.6134},
                  {"rank": 2, "player_id": "b", "name": "Bravo Two", "pos": "RB", "team": "KC", "score": 0.5},
                  {"rank": 3, "player_id": "c", "name": "Charlie Three", "pos": "TE", "team": "TB", "score": 0.4}]}


class TestInjuryDesignations(unittest.TestCase):
    def resolve(self, fetch, espn=None):
        with mock.patch("sys.stderr"):
            return injuries.resolve(2026, 5, NOW, CFG["run"]["injury_report_max_age_hours"], espn, fetch=fetch)

    def test_rule_is_out_plus_doubtful_and_the_nightly_update_fits_the_age_limit(self):
        self.assertEqual(CFG["run"]["pool_exclude_injury_statuses"], ["Out", "Doubtful"])
        self.assertGreater(CFG["run"]["injury_report_max_age_hours"], 8)    # 06:00 UTC update, 14:00 UTC run
        self.assertLess(CFG["run"]["injury_report_max_age_hours"], 24)      # yesterday's file must count as missing

    def test_parse_week_keeps_only_this_weeks_regular_season_designations(self):
        status, n = injuries.parse_week(WEEK5_REPORT, 2026, 5)
        self.assertEqual(status, {"out1": "Out", "dbt1": "Doubtful", "q1": "Questionable"})
        self.assertEqual(n, 4)   # practice-only row is listed, has no designation; week 4 and the playoff row are not this report

    def test_fresh_official_report_decides_and_espn_is_only_a_cross_check(self):
        r = self.resolve(fetched(), espn={"out1": "OUT", "espn_only": "OUT", "q1": "QUESTIONABLE", "x": "ACTIVE", "ir": "INJURY_RESERVE"})
        self.assertEqual((r["source"], r["reason"]), ("nflverse", None))
        self.assertEqual(r["status"], {"out1": "Out", "dbt1": "Doubtful", "q1": "Questionable"})
        self.assertEqual(r["espn"], {"out1": "Out", "espn_only": "Out", "q1": "Questionable"})
        self.assertEqual(r["sha256"], hashlib.sha256(WEEK5_REPORT).hexdigest())

    def test_missing_nightly_update_falls_back_to_espn(self):
        for fetch, why in ((fetched(age_hours=26), "nightly update is missing"),
                           (fetched(ok=False, error="HTTP 503"), "fetch failed (HTTP 503)"),
                           (fetched(raw=injury_csv([(4, "a", "Out")])), "no rows for 2026 week 5"),
                           (fetched(age_hours=None), "age is unknown"),
                           (fetched(raw=b"\xff\xfe not a csv"), "could not be parsed")):
            with self.subTest(why=why):
                r = self.resolve(fetch, espn={"espn_only": "OUT", "d": "DOUBTFUL", "x": "ACTIVE"})
                self.assertEqual(r["source"], "espn-fallback")
                self.assertIn(why, r["reason"])
                self.assertEqual(r["status"], {"espn_only": "Out", "d": "Doubtful"})

    def test_no_source_at_all_is_recorded_as_unavailable_and_excludes_nobody(self):
        for espn in (None, {}):   # no snapshot / a snapshot from before the status was parsed
            r = self.resolve(fetched(ok=False, error="ConnectionError"), espn=espn)
            self.assertEqual((r["source"], r["status"]), ("unavailable", {}))

    def test_fetch_never_raises(self):
        session = mock.Mock()
        session.get.side_effect = ConnectionError("down")
        got = injuries.fetch_nflverse(2026, session=session)
        self.assertFalse(got["ok"])
        self.assertIn("ConnectionError", got["error"])
        session.get.side_effect = None
        session.get.return_value = mock.Mock(status_code=200, content=b"x", headers={"Last-Modified": "Sun, 04 Oct 2026 13:08:58 GMT"})
        self.assertEqual(injuries.fetch_nflverse(2026, session=session)["last_modified"], utc("2026-10-04T13:08:58"))

    def test_split_pool_and_disagreements(self):
        pool = [{"player_id": p, "name": p} for p in ("out1", "dbt1", "q1", "healthy", "espn_only")]
        kept, held = injuries.split_pool(pool, {"out1": "Out", "dbt1": "Doubtful", "q1": "Questionable"}, EXCLUDE)
        self.assertEqual([r["player_id"] for r in kept], ["q1", "healthy", "espn_only"])   # Questionable stays in
        self.assertEqual(held, {"out1": "Out", "dbt1": "Doubtful"})
        d = injuries.disagreements(pool, {"out1": "Out", "dbt1": "Doubtful", "q1": "Questionable"},
                                   {"out1": "Out", "espn_only": "Out", "q1": "Questionable"}, EXCLUDE)
        self.assertEqual(d, [{"player_id": "dbt1", "name": "dbt1", "nflverse": "Doubtful", "espn": None},
                             {"player_id": "espn_only", "name": "espn_only", "nflverse": None, "espn": "Out"}])
        self.assertEqual(injuries.disagreements(pool, None, {"out1": "Out"}, EXCLUDE), [])


class TestEspnInjuryStatus(unittest.TestCase):
    def test_status_is_parsed_stored_and_optional(self):
        payload = espn_payload()
        payload["players"][0]["player"]["injuryStatus"] = "OUT"
        payload["players"][1]["player"]["injuryStatus"] = "ACTIVE"
        ok, _reason, rows = ownership.validate_espn(payload)
        self.assertTrue(ok)                                      # players with no injuryStatus don't fail validation
        self.assertEqual([r["injury_status"] for r in rows[:3]], ["OUT", "ACTIVE", None])
        con = mem_db()
        fetch = lambda season: {"ok": True, "error": None, "http_status": 200, "url": "u", "raw_bytes": b"{}", "rows": rows}
        with tempfile.TemporaryDirectory() as d:
            s = ownership.take_snapshot(con, 2026, d, crosswalk={"1000": "g0", "1001": "g1", "1002": "g2"}, espn_fetch=fetch,
                                        include_sleeper=False)
        self.assertEqual(ownership.injury_status_by_player(con, s["espn_snapshot_id"]), {"g0": "OUT", "g1": "ACTIVE"})

    def test_existing_db_gets_the_column_and_old_snapshots_read_as_no_status(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE tr_ownership (snapshot_id INTEGER NOT NULL, source TEXT NOT NULL, external_id TEXT NOT NULL, "
                    "player_id TEXT, name TEXT, position TEXT, team TEXT, percent_owned REAL, percent_change REAL, "
                    "trending_count INTEGER, PRIMARY KEY (snapshot_id, source, external_id))")
        con.execute("INSERT INTO tr_ownership (snapshot_id, source, external_id, player_id, percent_owned) VALUES (7, 'espn', '1', 'g1', 99.0)")
        ledger.init_schema(con)
        self.assertIn("injury_status", ledger.table_columns(con, "tr_ownership"))
        self.assertEqual(ownership.injury_status_by_player(con, 7), {})
        self.assertEqual(ownership.ownership_by_player(con, 7), {"g1": 99.0})


class TestPredictionMarkdownFlags(unittest.TestCase):
    def test_flags_are_display_only(self):
        plain = render_markdown_report(PREDICTION_OUTPUT)
        self.assertEqual(render_markdown_report(PREDICTION_OUTPUT, None, None), plain)
        self.assertEqual(render_markdown_report(PREDICTION_OUTPUT, {}, None), plain)       # nobody flagged, no note: unchanged
        flagged = render_markdown_report(PREDICTION_OUTPUT, {"a": "OUT", "c": "DOUBTFUL", "not_in_top": "OUT"}, "Injury flags: note.")
        self.assertIn("| 1 | Alpha One **(OUT)** | WR | MIN | 0.6134 |", flagged)
        self.assertIn("| 2 | Bravo Two | RB | KC | 0.5000 |", flagged)
        self.assertIn("| 3 | Charlie Three **(DOUBTFUL)** | TE | TB | 0.4000 |", flagged)
        self.assertIn("Injury flags: note.", flagged)
        # taking the flags and the note back out gives the original report: same rows, order, ranks and scores
        undone = flagged.replace(" **(OUT)**", "").replace(" **(DOUBTFUL)**", "").replace("Injury flags: note.\n\n", "")
        self.assertEqual(undone, plain)


class TestLogInjuryPoolRule(TestLogInternationalWeek):
    """CLI-level `log` on a live (no --as-of) run with the injury report stubbed:
    Out/Doubtful players are out of the pool for every model, the run records
    what was excluded and why, and the prediction JSON is untouched."""

    # the parent's own tests run once, there
    test_sun_run_made_before_any_game_still_leaves_out_thursday_and_international_players = None
    test_sun_run_is_accepted_between_the_international_kickoff_and_one_pm = None
    test_thu_run_keeps_the_whole_pool_international_players_included = None

    NOW = utc("2026-10-04T14:00:00")   # Sunday 07:00 Pacific, before the 1 PM ET cutoff
    REPORT = injury_csv([(4, "TEN-RB0", "Out"), (4, "BAL-WR2", "Doubtful"), (4, "KC-TE5", "Questionable"),
                         (4, "IND-RB0", "Out"), (3, "LV-RB0", "Out")])

    def fake_run(self, cmd, **_kw):
        if "rev-parse" in cmd:
            return mock.Mock(returncode=0, stdout="deadbeef\n", stderr="")
        out = Path(cmd[cmd.index("--json-out") + 1])
        out.parent.mkdir(parents=True, exist_ok=True)
        top = [dict(r, rank=i) for i, r in enumerate(self.scored_pool, start=1)]
        payload = json.dumps(dict(PREDICTION_OUTPUT, week=4, scored_pool=self.scored_pool, top=top))
        for path in (out, out.with_name(out.stem + "_shadow.json")):
            path.write_text(payload, encoding="utf-8")
            path.with_suffix(".md").write_text("as score_week.py wrote it", encoding="utf-8")
        self.json_as_written = payload
        return mock.Mock(returncode=0, stdout="", stderr="")

    def add_espn_snapshot(self, statuses, owned=None, no_row=()):
        """One ESPN snapshot row per scored player: 10% owned unless `owned` says otherwise; none at all for `no_row`."""
        con = ledger.connect(self.db)
        sid = con.execute("INSERT INTO tr_ownership_snapshots (taken_at, source, ok) VALUES ('2026-10-04T13:30:00+00:00', 'espn', 1)").lastrowid
        for r in self.scored_pool:
            if r["player_id"] in no_row:
                continue
            con.execute("INSERT INTO tr_ownership (snapshot_id, source, external_id, player_id, percent_owned, injury_status) "
                        "VALUES (?, 'espn', ?, ?, ?, ?)", (sid, r["player_id"], r["player_id"], (owned or {}).get(r["player_id"], 10.0),
                                                           statuses.get(r["player_id"], "ACTIVE")))
        con.commit()
        con.close()

    # ---- bye weeks: a team with no game

    def add_bye_team(self):
        """Six CAR players near the top of what O.D.D.S. scored; CAR has no game in this fixture's week 4."""
        self.scored_pool[2:2] = [{"player_id": f"CAR-{pos}{i}", "name": f"CAR-{pos}{i}", "pos": pos, "team": "CAR", "score": 0.95}
                                 for i, pos in enumerate(("RB", "RB", "WR", "WR", "WR", "TE"))]

    def test_bye_week_players_are_out_of_the_pool_for_every_model_recorded_and_flagged(self):
        self.add_bye_team()
        rc, out = self.log_live(self.fresh, "--skip-ownership")
        self.assertEqual(rc, 0)
        self.assertIn("6 scored player(s) left out of the pool: their team has no game in week 4 -- teams ['CAR']", out)
        self.assertEqual({r["team"] for r in self.rows("SELECT team FROM tr_pool")}, {"TEN", "BAL", "KC", "LV"})
        picks = self.rows("SELECT DISTINCT model_name, player_id FROM tr_predictions")
        self.assertEqual({r["model_name"] for r in picks}, {"odds_prod", "odds_shadow", "heuristic", "last_week", "dart"})
        self.assertFalse([r for r in picks if r["player_id"].startswith("CAR-")])
        cfg = self.run_config()
        self.assertEqual(cfg["pool_rule"], tracker.POOL_RULE)
        self.assertEqual(cfg["excluded_no_game"], sorted(f"CAR-{pos}{i}" for i, pos in enumerate(("RB", "RB", "WR", "WR", "WR", "TE"))))
        self.assertEqual(cfg["injury"]["excluded"], {"TEN-RB0": "Out", "BAL-WR2": "Doubtful"})    # the injury rule still runs
        for stem in ("2026_week04", "2026_week04_shadow"):
            self.assertEqual((self.dir / "preds" / f"{stem}.json").read_text(encoding="utf-8"), self.json_as_written)   # JSON untouched
            md = (self.dir / "preds" / f"{stem}.md").read_text(encoding="utf-8")
            self.assertIn("| 3 | CAR-RB0 **(BYE)** | RB | CAR | 0.9500 |", md)    # rank and score exactly as scored
            self.assertIn("**(BYE)** = his team has no game this week.", md)
            self.assertIn("TEN-RB0 **(OUT)**", md)
            self.assertIn("Injury flags:", md)

    def test_bye_flag_does_not_need_an_injury_report(self):
        """An --as-of run fetches no injury report, but the schedule alone says who is on a bye."""
        self.add_bye_team()
        self.assertEqual(self.log("sun", "2026-10-04T14:00:00+00:00"), 0)
        self.assertEqual(len(self.run_config()["excluded_no_game"]), 6)
        md = (self.dir / "preds" / "2026_week04.md").read_text(encoding="utf-8")
        self.assertIn("CAR-TE5 **(BYE)**", md)
        self.assertNotIn("Injury flags:", md)
        self.assertNotIn("**(OUT)**", md)

    def test_roster_team_decides_who_is_on_a_bye(self):
        self.add_bye_team()
        with open(self.dir / "raw" / "roster_weekly_2026.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["season", "week", "gsis_id", "team", "position", "status", "rookie_year", "draft_number"])
            w.writeheader()
            w.writerow(dict(season=2026, week=4, gsis_id="CAR-RB0", team="LV", position="RB", status="ACT"))    # traded to a team that plays
            w.writerow(dict(season=2026, week=4, gsis_id="KC-RB0", team="CAR", position="RB", status="ACT"))    # traded onto the bye team
        self.assertEqual(self.log_live(self.fresh, "--skip-ownership")[0], 0)
        cfg = self.run_config()
        self.assertIn("KC-RB0", cfg["excluded_no_game"])
        self.assertNotIn("CAR-RB0", cfg["excluded_no_game"])
        pool = {r["player_id"] for r in self.rows("SELECT player_id FROM tr_pool")}
        self.assertIn("CAR-RB0", pool)
        self.assertNotIn("KC-RB0", pool)

    def test_pool_player_with_no_ownership_row_stays_in_the_sleeper_pool_flagged_as_unknown(self):
        """A player skipped by the ESPN parser, or simply absent from ESPN's list: not dropped, not treated as 0%."""
        self.add_espn_snapshot({}, owned={"KC-RB0": 80.0, "KC-RB1": 49.9, "KC-WR2": 50.0}, no_row=("LV-WR3", "TEN-TE5"))
        rc, out = self.log_live(self.fresh)
        self.assertEqual(rc, 0)
        pool = {r["player_id"]: (r["percent_owned"], r["in_u50"]) for r in self.rows("SELECT * FROM tr_pool")}
        self.assertEqual(pool["LV-WR3"], (None, 1))        # unknown: in, with NULL ownership as the flag
        self.assertEqual(pool["TEN-TE5"], (None, 1))
        self.assertEqual(pool["KC-RB1"], (49.9, 1))
        self.assertEqual(pool["KC-RB0"], (80.0, 0))        # known to be over the line: still out
        self.assertEqual(pool["KC-WR2"], (50.0, 0))        # the line itself is strict, as before
        cfg = self.run_config()
        self.assertEqual(cfg["sleeper_pool_rule"], tracker.SLEEPER_POOL_RULE)
        self.assertEqual(cfg["ownership_unknown_in_sleeper_pool"], ["LV-WR3", "TEN-TE5"])
        self.assertIn("2 of them ownership unknown", out)
        self.assertIn("TEN-TE5 (ownership unknown)", out)  # flagged where the picks are listed
        run = self.rows("SELECT * FROM tr_runs")[0]
        self.assertEqual((run["pool_size"], run["ownership_matched"]), (22, 20))
        u50_picks = {r["player_id"] for r in self.rows("SELECT player_id FROM tr_predictions WHERE pick_set LIKE 'u50%'")}
        self.assertTrue(u50_picks <= {p for p, (_o, in_u50) in pool.items() if in_u50})
        self.assertFalse(u50_picks & {"KC-RB0", "KC-WR2"})
        con = ledger.connect(self.db)
        try:
            lines = report.sleeper_pool_lines(con, [run])
            self.assertIn("week 4 `sun` run:** 20 players, 2 of them with **ownership unknown**", lines[0])
            self.assertEqual({r["player_id"] for r in tracker.rebuild_dart_sets(con, run["run_id"])["u50_k10"][0]},
                             {p for p, (_o, in_u50) in pool.items() if in_u50})   # dart draws see the same pool at scoring time
        finally:
            con.close()

    def test_no_snapshot_at_all_still_means_no_sleeper_pool_not_everyone_unknown(self):
        self.assertEqual(self.log_live(self.fresh, "--skip-ownership")[0], 0)
        self.assertEqual(self.rows("SELECT COALESCE(SUM(in_u50), 0) FROM tr_pool")[0][0], 0)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM tr_predictions WHERE pick_set LIKE 'u50%'")[0][0], 0)
        self.assertEqual(self.run_config()["ownership_unknown_in_sleeper_pool"], [])

    def log_live(self, fetch, *extra):
        argv = ["log", "--season", "2026", "--week", "4", "--slot", "sun", "--db", str(self.db),
                "--predictions-dir", str(self.dir / "preds"), "--export-dir", str(self.dir / "export"),
                "--raw-dir", str(self.dir / "raw"), "--injuries-raw-dir", str(self.dir / "raw_injuries"), *extra]
        out = io.StringIO()
        with mock.patch.object(tracker.subprocess, "run", side_effect=self.fake_run), \
                mock.patch.object(tracker, "utcnow", return_value=self.NOW), \
                mock.patch.object(tracker.injuries, "fetch_nflverse", side_effect=fetch), \
                mock.patch("sys.stderr"), contextlib.redirect_stdout(out):
            rc = tracker.main(argv)
        return rc, out.getvalue()

    def fresh(self, season):
        return {"ok": True, "raw_bytes": self.REPORT, "error": None, "url": "u", "last_modified": self.NOW - timedelta(hours=8)}

    def run_config(self):
        return json.loads(self.rows("SELECT run_config_json FROM tr_runs")[0][0])

    def test_out_and_doubtful_are_left_out_for_every_model_and_recorded(self):
        self.add_espn_snapshot({"TEN-RB0": "OUT", "KC-TE5": "QUESTIONABLE", "LV-WR3": "OUT"})
        rc, out = self.log_live(self.fresh)
        self.assertEqual(rc, 0)
        pool = {r["player_id"] for r in self.rows("SELECT player_id FROM tr_pool")}
        self.assertEqual(len(pool), 22)                                    # 24 after the slot cutoff, minus Out and Doubtful
        self.assertFalse(pool & {"TEN-RB0", "BAL-WR2"})
        self.assertTrue({"KC-TE5", "LV-WR3"} <= pool)                      # Questionable stays; ESPN alone doesn't exclude
        picks = self.rows("SELECT DISTINCT model_name, player_id FROM tr_predictions")
        self.assertEqual({r["model_name"] for r in picks}, {"odds_prod", "odds_shadow", "heuristic", "last_week", "dart"})
        self.assertFalse([r for r in picks if r["player_id"] in ("TEN-RB0", "BAL-WR2")])
        cfg = self.run_config()
        self.assertEqual(cfg["pool_rule"], tracker.POOL_RULE)
        self.assertEqual(cfg["excluded_no_game"], [])              # all eight teams in this fixture play
        inj = cfg["injury"]
        self.assertEqual((inj["source"], inj["reason"], inj["exclude_statuses"]), ("nflverse", None, ["Out", "Doubtful"]))
        self.assertEqual(inj["excluded"], {"TEN-RB0": "Out", "BAL-WR2": "Doubtful"})   # IND-RB0 was already out via the cutoff
        self.assertEqual(inj["nflverse_sha256"], hashlib.sha256(self.REPORT).hexdigest())
        self.assertEqual(sorted((d["player_id"], d["nflverse"], d["espn"]) for d in inj["disagreements"]),
                         [("BAL-WR2", "Doubtful", None), ("LV-WR3", None, "Out")])
        self.assertIn("sources disagree on LV-WR3", out)
        # the report that decided the pool is kept, outside the DB, under the recorded name
        self.assertEqual(gzip.decompress((self.dir / "raw_injuries" / inj["nflverse_file"]).read_bytes()), self.REPORT)

    def test_prediction_json_is_untouched_and_the_markdown_only_gains_flags(self):
        self.assertEqual(self.log_live(self.fresh, "--skip-ownership")[0], 0)
        for stem in ("2026_week04", "2026_week04_shadow"):
            self.assertEqual((self.dir / "preds" / f"{stem}.json").read_text(encoding="utf-8"), self.json_as_written)
            md = (self.dir / "preds" / f"{stem}.md").read_text(encoding="utf-8")
            self.assertIn("TEN-RB0 **(OUT)**", md)
            self.assertIn("BAL-WR2 **(DOUBTFUL)**", md)
            self.assertIn("IND-RB0 **(OUT)**", md)                 # flagged wherever he appears, in the pool or not
            self.assertNotIn("KC-TE5 **", md)
            self.assertIn("Injury flags:", md)
            self.assertIn("| 1 | PIT-RB0 | RB | PIT | 0.9000 |", md)   # rank and score exactly as scored

    def test_stale_report_falls_back_to_espn_and_says_so(self):
        self.add_espn_snapshot({"LV-WR3": "OUT", "KC-RB1": "DOUBTFUL", "TEN-RB0": "QUESTIONABLE"})
        stale = lambda season: dict(self.fresh(season), last_modified=self.NOW - timedelta(hours=26))
        self.assertEqual(self.log_live(stale)[0], 0)
        inj = self.run_config()["injury"]
        self.assertEqual(inj["source"], "espn-fallback")
        self.assertIn("nightly update is missing", inj["reason"])
        self.assertEqual(inj["excluded"], {"LV-WR3": "Out", "KC-RB1": "Doubtful"})
        self.assertEqual(inj["disagreements"], [])   # ESPN decided this pool; there is nothing to cross-check it against
        self.assertEqual(self.rows("SELECT COUNT(*) FROM tr_pool")[0][0], 22)
        self.assertIn("ESPN's injury status", (self.dir / "preds" / "2026_week04.md").read_text(encoding="utf-8"))

    def test_no_source_still_logs_with_the_rule_recorded_as_not_applied(self):
        down = lambda season: {"ok": False, "raw_bytes": None, "error": "ConnectionError", "url": "u", "last_modified": None}
        self.assertEqual(self.log_live(down, "--skip-ownership")[0], 0)
        inj = self.run_config()["injury"]
        self.assertEqual((inj["source"], inj["excluded"]), ("unavailable", {}))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM tr_pool")[0][0], 24)
        self.assertIn("NOBODY is flagged", (self.dir / "preds" / "2026_week04.md").read_text(encoding="utf-8"))

    def test_as_of_run_never_fetches_and_leaves_the_markdown_alone(self):
        with mock.patch.object(tracker.injuries, "fetch_nflverse", side_effect=AssertionError("no live report on an --as-of run")):
            self.assertEqual(self.log("sun", "2026-10-04T14:00:00+00:00"), 0)
        self.assertEqual(self.run_config()["injury"]["source"], "skipped")
        self.assertEqual((self.dir / "preds" / "2026_week04.md").read_text(encoding="utf-8"), "as score_week.py wrote it")


# ----------------------------------------------------------------- baselines

class TestBaselines(unittest.TestCase):
    def test_heuristic_orders_by_delta_then_target_share_then_id_with_undefined_last(self):
        pool = [dict(player_id="d", snap_delta=None, target_share=0.9),
                dict(player_id="c", snap_delta=0.10, target_share=0.1),
                dict(player_id="b", snap_delta=0.10, target_share=0.3),
                dict(player_id="a", snap_delta=0.10, target_share=0.3),
                dict(player_id="e", snap_delta=0.30, target_share=0.0),
                dict(player_id="f", snap_delta=None, target_share=None)]
        self.assertEqual([r["player_id"] for r in baselines.rank_heuristic(pool)], ["e", "a", "b", "c", "d", "f"])

    def test_last_week_orders_by_points_with_no_game_last(self):
        pool = [dict(player_id="x", last_week_points=None), dict(player_id="y", last_week_points=12.0),
                dict(player_id="z", last_week_points=20.5), dict(player_id="w", last_week_points=12.0)]
        self.assertEqual([r["player_id"] for r in baselines.rank_last_week(pool)], ["z", "w", "y", "x"])

    def test_matched_selection_respects_mix_and_reports_shortfall(self):
        ranked = [dict(player_id=f"r{i}", position="RB") for i in range(5)] + [dict(player_id="t0", position="TE")]
        picks, short = baselines.select_matched(ranked, {"RB": 2, "TE": 1, "WR": 2})
        self.assertEqual([p["player_id"] for p in picks], ["r0", "r1", "t0"])
        self.assertEqual(short, {"WR": 2})

    def test_snap_delta_uses_last_four_games_strictly_before_the_week(self):
        con = mem_db()
        for wk, pct in ((1, 0.20), (2, 0.40), (3, 0.60), (4, 0.80), (5, 1.00)):
            con.execute("INSERT INTO tr_snap_counts (season, week, player_id, offense_pct) VALUES (2026, ?, 'p', ?)", (wk, pct))
        f = baselines.snap_features(con, ["p", "q"], 2026, 5)["p"]  # week 5 itself must be excluded
        self.assertAlmostEqual(f["snap_recent"], 0.70)
        self.assertAlmostEqual(f["snap_prior"], 0.30)
        self.assertAlmostEqual(f["snap_delta"], 0.40)
        self.assertEqual(f["snap_games"], 4)
        self.assertIsNone(baselines.snap_features(con, ["p"], 2026, 4)["p"]["snap_delta"])  # only 3 prior games
        self.assertIsNone(baselines.snap_features(con, ["q"], 2026, 5)["q"]["snap_delta"])  # unknown player
        self.assertEqual(baselines.latest_snap_week(con, 2026, 5), (2026, 4))

    def test_baselines_pick_the_same_count_and_mix_from_the_same_pool(self):
        pool = []
        for i, (pos, n) in enumerate((("QB", 8), ("RB", 20), ("WR", 30), ("TE", 12))):
            for j in range(n):
                pool.append(dict(player_id=f"{pos}{j:02d}", position=pos, odds_prod_score=1 - 0.001 * (len(pool)),
                                 odds_shadow_score=0.5, in_u50=j % 2, snap_delta=(j % 5) / 10.0, target_share=j / 100,
                                 last_week_points=float(j)))
        rows, _ = tracker.make_predictions("r", pool, CFG, 1, {})
        by = {}
        for r in rows:
            by.setdefault((r["pick_set"], r["model_name"]), []).append(r)
        pool_ids = {p["player_id"] for p in pool}
        for ps in ("all_k10", "all_k25", "u50_k10", "u50_k25"):
            mix = baselines.position_mix(by[(ps, "odds_prod")])
            for m in ("odds_shadow", "heuristic", "last_week", "dart"):
                if m == "odds_shadow":
                    continue  # shadow is its own model with its own picks, not a matched baseline
                self.assertEqual(len(by[(ps, m)]), len(by[(ps, "odds_prod")]), (ps, m))
                self.assertEqual(baselines.position_mix(by[(ps, m)]), mix, (ps, m))
            for m in ("odds_prod", "odds_shadow", "heuristic", "last_week", "dart"):
                self.assertTrue({r["player_id"] for r in by[(ps, m)]} <= pool_ids)
        u50 = {p["player_id"] for p in pool if p["in_u50"]}
        for m in ("odds_prod", "odds_shadow", "heuristic", "last_week", "dart"):
            self.assertTrue({r["player_id"] for r in by[("u50_k25", m)]} <= u50, m)  # sleeper scope never leaves the sleeper pool


# ----------------------------------------------------------------- ownership

def espn_payload(n=400, high=10):
    players = [{"player": {"id": 1000 + i, "fullName": f"P{i}", "defaultPositionId": 3,
                           "ownership": {"percentOwned": 99.0 if i < high else 5.0, "percentChange": 0.1}}} for i in range(n)]
    return {"players": players}


class TestOwnership(unittest.TestCase):
    def test_validation_accepts_a_healthy_payload(self):
        ok, reason, rows = ownership.validate_espn(espn_payload())
        self.assertTrue(ok, reason)
        self.assertEqual(len(rows), 400)

    def test_validation_rejects_shape_changes_and_garbage(self):
        bad = [{"players": "nope"}, {"data": []}, {"players": [{"player": {"id": 1}}]}, espn_payload(n=10),
               espn_payload(high=0)]
        out_of_range = espn_payload()
        out_of_range["players"][3]["player"]["ownership"]["percentOwned"] = 140
        bad.append(out_of_range)
        for payload in bad:
            ok, reason, rows = ownership.validate_espn(payload)
            self.assertFalse(ok)
            self.assertEqual(rows, [])

    @staticmethod
    def without_ownership(n_missing, n=400):
        """A healthy payload in which the last n_missing players are listed with no `ownership` block yet."""
        payload = espn_payload(n=n)
        for e in payload["players"][n - n_missing:]:
            del e["player"]["ownership"]
        return payload

    def test_record_without_ownership_is_skipped_and_counted_not_fatal(self):
        """2026-10-05: one newly added player (of 961) had no `ownership` block and the whole snapshot failed."""
        ok, reason, rows, skipped = ownership.parse_espn(self.without_ownership(1))
        self.assertTrue(ok, reason)
        self.assertEqual(len(rows), 399)
        self.assertEqual(skipped, [{"external_id": "1399", "name": "P399"}])
        self.assertNotIn("1399", {r["external_id"] for r in rows})
        null_block = espn_payload()
        null_block["players"][5]["player"]["ownership"] = None                 # present but empty: same thing
        null_block["players"][6]["player"]["ownership"] = {"percentChange": 0.1}
        self.assertEqual([s["name"] for s in ownership.parse_espn(null_block)[3]], ["P5", "P6"])

    def test_snapshot_fails_only_when_too_few_records_carry_ownership(self):
        self.assertEqual(ownership.MIN_ESPN_VALID_SHARE, 0.98)
        self.assertTrue(ownership.parse_espn(self.without_ownership(8))[0])    # 392/400 = 98.0%: still a snapshot
        ok, reason, rows, skipped = ownership.parse_espn(self.without_ownership(9))   # 97.75%
        self.assertFalse(ok)
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 9)
        self.assertIn("only 391 of 400 player records carry ownership data", reason)
        ok, reason, rows, _ = ownership.parse_espn(self.without_ownership(400))       # the field moved for everyone
        self.assertFalse(ok)
        self.assertIn("only 0 of 400", reason)
        self.assertIn("the field has probably moved", reason)

    def test_skipped_records_are_stored_as_a_count_and_reported(self):
        con = mem_db()
        payload = self.without_ownership(2)

        def fetch(season):
            ok, reason, rows, skipped = ownership.parse_espn(payload)
            return {"ok": ok, "error": None if ok else reason, "http_status": 200, "url": "u", "raw_bytes": b"{}",
                    "rows": rows, "skipped": skipped}
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(out):
            s = ownership.take_snapshot(con, 2026, d, crosswalk={}, espn_fetch=fetch, include_sleeper=False)
        self.assertEqual((s["espn_ok"], s["espn_rows"], s["espn_skipped"]), (True, 398, 2))
        snap = con.execute("SELECT ok, n_rows, n_skipped FROM tr_ownership_snapshots").fetchone()
        self.assertEqual(tuple(snap), (1, 398, 2))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tr_ownership").fetchone()[0], 398)
        self.assertIn("[OWNERSHIP NOTE] ESPN: skipped 2 of 400 player record(s)", out.getvalue())
        self.assertIn("P398, P399", out.getvalue())
        self.assertIsNotNone(ownership.latest_ok_espn(con, datetime.now(timezone.utc) + timedelta(seconds=5), 36))  # usable by `log`

    def test_existing_snapshots_table_gets_the_skip_count_column(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute("CREATE TABLE tr_ownership_snapshots (snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT NOT NULL, "
                    "source TEXT NOT NULL, url TEXT, ok INTEGER NOT NULL, error TEXT, http_status INTEGER, n_rows INTEGER, "
                    "raw_sha256 TEXT, raw_file TEXT)")
        con.execute("INSERT INTO tr_ownership_snapshots (taken_at, source, ok, n_rows) VALUES ('t', 'espn', 1, 960)")
        ledger.init_schema(con)
        self.assertIsNone(con.execute("SELECT n_skipped FROM tr_ownership_snapshots").fetchone()[0])

    def test_failure_is_recorded_loudly_and_never_raises(self):
        con = mem_db()
        failing = lambda season: {"ok": False, "error": "HTTP 500", "rows": [], "raw_bytes": b"oops", "http_status": 500, "url": "u"}
        with mock.patch("sys.stderr"), tempfile.TemporaryDirectory() as d:
            s = ownership.take_snapshot(con, 2026, d, crosswalk={}, espn_fetch=failing, include_sleeper=False)
            row = con.execute("SELECT ok, error, http_status, raw_file FROM tr_ownership_snapshots").fetchone()
            self.assertEqual(gzip.decompress((Path(d) / row["raw_file"]).read_bytes()), b"oops")  # a failed response is kept too
        self.assertFalse(s["espn_ok"])
        self.assertTrue(s["errors"])
        self.assertEqual((row["ok"], row["error"], row["http_status"]), (0, "HTTP 500", 500))
        self.assertIsNone(ownership.latest_ok_espn(con, datetime.now(timezone.utc), 36))  # failed snapshots are never used

    def test_fetch_catches_network_errors(self):
        session = mock.Mock()
        session.get.side_effect = ConnectionError("down")
        out = ownership.fetch_espn(2026, session=session)
        self.assertFalse(out["ok"])
        self.assertIn("ConnectionError", out["error"])

    def test_success_stores_raw_and_parsed_rows_with_gsis_mapping(self):
        con = mem_db()
        ok_fetch = lambda season: {"ok": True, "error": None, "http_status": 200, "url": "u", "raw_bytes": b'{"x":1}',
                                   "rows": ownership.validate_espn(espn_payload())[2]}
        with tempfile.TemporaryDirectory() as d:
            s = ownership.take_snapshot(con, 2026, d, now=utc("2026-10-03T17:00:26"), crosswalk={"1000": "00-0000001"},
                                        espn_fetch=ok_fetch, include_sleeper=False)
            files = [f.name for f in Path(d).iterdir()]
            snap = con.execute("SELECT * FROM tr_ownership_snapshots").fetchone()
            raw = gzip.decompress((Path(d) / snap["raw_file"]).read_bytes())
        self.assertTrue(s["espn_ok"])
        self.assertEqual(s["espn_matched"], 1)
        # the raw response lives in a file OUTSIDE the DB; the DB keeps its SHA-256 and the file's name
        sha = hashlib.sha256(b'{"x":1}').hexdigest()
        self.assertEqual(raw, b'{"x":1}')
        self.assertEqual(snap["raw_sha256"], sha)
        self.assertEqual(files, [f"20261003T170026Z_espn_{sha[:12]}.json.gz"])
        self.assertEqual(snap["raw_file"], files[0])
        self.assertNotIn("raw_zlib", snap.keys())
        got = ownership.latest_ok_espn(con, utc("2026-10-03T17:00:31"), 36)
        self.assertEqual(ownership.ownership_by_player(con, got[0]), {"00-0000001": 99.0})
        parsed = con.execute("SELECT o.player_id, o.percent_owned, o.percent_change, s.taken_at FROM tr_ownership o "
                             "JOIN tr_ownership_snapshots s USING (snapshot_id) WHERE o.player_id IS NOT NULL").fetchone()
        self.assertEqual(tuple(parsed), ("00-0000001", 99.0, 0.1, "2026-10-03T17:00:26+00:00"))

    def test_unwritable_raw_dir_is_loud_but_keeps_the_snapshot(self):
        con = mem_db()
        ok_fetch = lambda season: {"ok": True, "error": None, "http_status": 200, "url": "u", "raw_bytes": b'{"x":1}',
                                   "rows": ownership.validate_espn(espn_payload())[2]}
        with tempfile.TemporaryDirectory() as d, mock.patch("sys.stderr") as err:
            blocker = Path(d) / "not_a_dir"
            blocker.write_text("x")
            s = ownership.take_snapshot(con, 2026, blocker, crosswalk={}, espn_fetch=ok_fetch, include_sleeper=False)
        self.assertTrue(s["espn_ok"])
        self.assertIn("could not write the raw", "".join(c.args[0] for c in err.write.call_args_list))
        snap = con.execute("SELECT raw_sha256, raw_file, n_rows FROM tr_ownership_snapshots").fetchone()
        self.assertEqual((snap["raw_sha256"], snap["raw_file"], snap["n_rows"]), (hashlib.sha256(b'{"x":1}').hexdigest(), None, 400))

    def test_raw_file_is_never_overwritten_with_different_bytes(self):
        with tempfile.TemporaryDirectory() as d:
            name = ownership.write_raw(d, "2026-10-03T17:00:26+00:00", "espn", b"abc")
            self.assertEqual(ownership.write_raw(d, "2026-10-03T17:00:26+00:00", "espn", b"abc"), name)  # same bytes: fine
            (Path(d) / name).write_bytes(gzip.compress(b"tampered"))
            with self.assertRaises(FileExistsError):
                ownership.write_raw(d, "2026-10-03T17:00:26+00:00", "espn", b"abc")


    def test_name_fallback_is_unique_match_only(self):
        con = mem_db()
        con.execute("CREATE TABLE ref_players (player_id TEXT PRIMARY KEY, full_name TEXT, pos TEXT, first_season INTEGER, last_season INTEGER)")
        con.executemany("INSERT INTO ref_players VALUES (?,?,?,2020,2026)", [
            ("g1", "Marvin Harrison Jr.", "WR"), ("g2", "Josh Allen", "QB"), ("g3", "Josh Allen", "QB"), ("g4", "Josh Allen", "LB")])
        idx = ownership.build_name_index(con)
        self.assertEqual(idx[(ownership.norm_name("Marvin Harrison"), "WR")], "g1")   # suffix-insensitive
        self.assertNotIn((ownership.norm_name("Josh Allen"), "QB"), idx)              # two QBs share the name: no guess
        self.assertEqual(idx[(ownership.norm_name("Josh Allen"), "LB")], "g4")

    def test_snapshot_age_limit(self):
        con = mem_db()
        con.execute("INSERT INTO tr_ownership_snapshots (taken_at, source, ok) VALUES ('2026-10-01T00:00:00+00:00','espn',1)")
        self.assertIsNotNone(ownership.latest_ok_espn(con, utc("2026-10-02T00:00:00"), 36))
        self.assertIsNone(ownership.latest_ok_espn(con, utc("2026-10-04T00:00:00"), 36))


LEGACY_SNAPSHOTS_DDL = """CREATE TABLE tr_ownership_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, taken_at TEXT NOT NULL, source TEXT NOT NULL, url TEXT,
  ok INTEGER NOT NULL, error TEXT, http_status INTEGER, n_rows INTEGER, raw_sha256 TEXT, raw_zlib BLOB)"""


class TestOwnershipRawMigration(unittest.TestCase):
    """A DB from before raw responses moved out to files: blobs in raw_zlib."""

    RAWS = {1: b'{"players": ["espn"]}', 2: b'[{"player_id": "1", "count": 2}]'}

    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute(LEGACY_SNAPSHOTS_DDL)
        ledger.init_schema(self.con)  # what any command does on connect: adds raw_file + the triggers, moves nothing
        for sid, source in ((1, "espn"), (2, "sleeper_trending_add")):
            raw = self.RAWS[sid]
            self.con.execute("INSERT INTO tr_ownership_snapshots (snapshot_id, taken_at, source, url, ok, http_status, n_rows, "
                             "raw_sha256, raw_zlib) VALUES (?, '2026-10-03T17:00:26.074714+00:00', ?, 'u', 1, 200, 1, ?, ?)",
                             (sid, source, hashlib.sha256(raw).hexdigest(), zlib.compress(raw, 6)))
        self.con.execute("INSERT INTO tr_ownership_snapshots (snapshot_id, taken_at, source, ok, error) "
                         "VALUES (3, '2026-10-03T18:00:00+00:00', 'espn', 0, 'ConnectionError')")  # failed fetch, no body
        self.con.execute("INSERT INTO tr_ownership (snapshot_id, source, external_id, player_id, percent_owned, percent_change) "
                         "VALUES (1, 'espn', '1000', '00-0000001', 42.5, -1.5)")
        self.con.commit()
        self.tmp = tempfile.TemporaryDirectory()
        self.raw_dir = Path(self.tmp.name) / "raw" / "ownership"

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def snapshot_rows(self, cols):
        return [tuple(r) for r in self.con.execute(f"SELECT {cols} FROM tr_ownership_snapshots ORDER BY snapshot_id")]

    def test_blobs_move_to_verified_files_and_the_column_is_dropped(self):
        keep = "snapshot_id, taken_at, source, url, ok, error, http_status, n_rows, raw_sha256"
        before = self.snapshot_rows(keep)
        files = ownership.migrate_raw_to_files(self.con, self.raw_dir)
        self.assertEqual(len(files), 2)
        self.assertEqual(sorted(f.name for f in self.raw_dir.iterdir()), sorted(files))
        self.assertEqual(self.snapshot_rows(keep), before)                      # nothing else about the rows changed
        self.assertNotIn("raw_zlib", ledger.table_columns(self.con, "tr_ownership_snapshots"))
        for sid, sha, name in self.snapshot_rows("snapshot_id, raw_sha256, raw_file"):
            if sid == 3:
                self.assertIsNone(name)
                continue
            raw = gzip.decompress((self.raw_dir / name).read_bytes())
            self.assertEqual(raw, self.RAWS[sid])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
            self.assertIn(sha[:12], name)
        self.assertEqual(tuple(self.con.execute("SELECT player_id, percent_owned, percent_change FROM tr_ownership").fetchone()),
                         ("00-0000001", 42.5, -1.5))

    def test_table_is_append_only_again_afterwards_and_rerun_is_a_no_op(self):
        ownership.migrate_raw_to_files(self.con, self.raw_dir)
        for sql in ("UPDATE tr_ownership_snapshots SET ok = 0", "DELETE FROM tr_ownership_snapshots"):
            with self.assertRaises(sqlite3.DatabaseError) as cm:
                self.con.execute(sql)
            self.assertIn("append-only", str(cm.exception))
        self.assertEqual(ownership.migrate_raw_to_files(self.con, self.raw_dir), [])
        with tempfile.TemporaryDirectory() as d:  # and new snapshots land in the migrated table
            ok_fetch = lambda season: {"ok": True, "error": None, "http_status": 200, "url": "u", "raw_bytes": b"new",
                                       "rows": ownership.validate_espn(espn_payload())[2]}
            ownership.take_snapshot(self.con, 2026, d, crosswalk={}, espn_fetch=ok_fetch, include_sleeper=False)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tr_ownership_snapshots").fetchone()[0], 4)

    def test_blob_that_does_not_match_its_hash_aborts_before_anything_changes(self):
        self.con.execute("DROP TRIGGER tr_ownership_snapshots_no_update")
        self.con.execute("UPDATE tr_ownership_snapshots SET raw_sha256 = 'not-the-hash' WHERE snapshot_id = 2")
        ledger.init_schema(self.con)
        with self.assertRaises(RuntimeError):
            ownership.migrate_raw_to_files(self.con, self.raw_dir)
        self.assertIn("raw_zlib", ledger.table_columns(self.con, "tr_ownership_snapshots"))
        self.assertEqual(self.con.execute("SELECT COUNT(raw_zlib), COUNT(raw_file) FROM tr_ownership_snapshots").fetchone()[:], (2, 0))
        with self.assertRaises(sqlite3.DatabaseError):
            self.con.execute("DELETE FROM tr_ownership_snapshots")


# ----------------------------------------------------------- scheduled runner

class TestScheduledRun(unittest.TestCase):
    THURSDAY = datetime(2026, 10, 8, 7, 0, 5)
    SUNDAY = datetime(2026, 10, 11, 7, 0, 5)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.logs = Path(self.tmp.name) / "logs"
        self.alerts, self.calls = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def run_job(self, job, now, results, **kw):
        """results: one (exit code, output) per step, in order."""
        it = iter(results)

        def fake(argv):
            self.calls.append(argv)
            return next(it)
        return scheduled_run.run_job(job, now=now, log_dir=self.logs, run=fake, alert=lambda *a: self.alerts.append(a), **kw)

    def summary(self):
        return (self.logs / scheduled_run.SUMMARY_LOG).read_text(encoding="utf-8").splitlines()

    # ---- the Thursday job's advisory check-stats step

    STATS_OK = (0, "[STATS] 2026 week 4 (scoring 2, scored 2026-10-06 14:46 UTC): stats unchanged\n")
    STATS_CHANGED = (1, "\n" + "!" * 78 + "\n[STATS CHANGED] 2026 week 4 (scoring 2, scored 2026-10-06 14:46 UTC): STATS CHANGED "
                        "SINCE IT WAS SCORED -- 3 of 358 week-4 player scores differ. Its results are from the earlier stats; "
                        "append a corrected scoring with: score --season 2026 --week 4 --force\n" + "!" * 78 + "\n")
    LOGGED = (0, "[LOGGED] 2026w05-thu-20261008T140003Z: pool 380 players\n")

    def test_thursday_job_runs_check_stats_after_the_log_step_and_sunday_does_not(self):
        self.assertEqual(self.run_job("log-thu", self.THURSDAY, [(0, ""), self.LOGGED, self.STATS_OK]), 0)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[1][-4:], ["log", "--auto", "--slot", "thu"])
        self.assertEqual(self.calls[2][-3:], ["check-stats", "--season", "2026"])
        self.assertEqual(self.alerts, [])
        self.assertNotIn("note:", self.summary()[-1])
        self.calls.clear()
        self.run_job("log-sun", self.SUNDAY, [(0, ""), (0, "[LOGGED] 2026w05-sun\n")])
        self.assertEqual(len(self.calls), 2)

    def test_changed_stats_raise_a_message_box_without_touching_the_log_result(self):
        rc = self.run_job("log-thu", self.THURSDAY, [(0, ""), self.LOGGED, self.STATS_CHANGED])
        self.assertEqual(rc, 0)                                     # the log run's own exit status
        line = self.summary()[-1]
        self.assertIn("OK", line)
        self.assertNotIn("FAILED", line)
        self.assertIn("[LOGGED] 2026w05-thu", line)                 # still the log step's headline
        self.assertIn("note: stats changed for an already-scored week", line)
        (title, text), = self.alerts
        self.assertEqual(title, "FAAB tracker: stats corrected for an already-scored week")
        self.assertIn("3 of 358 week-4 player scores differ", text)
        self.assertIn("score --season 2026 --week 4 --force", text)
        self.assertIn("did not affect today's log-thu run (OK)", text)
        self.assertNotIn("Nothing was written to the ledger", text)  # that's the failure box's wording, not this one's

    def test_changed_stats_do_not_hide_or_alter_a_refused_log(self):
        rc = self.run_job("log-thu", self.THURSDAY, [(0, ""), (2, "[REFUSED] REFUSED: the thu slot closed at ...\n"), self.STATS_CHANGED])
        self.assertEqual(rc, 2)
        self.assertIn("FAILED", self.summary()[-1])
        self.assertEqual([t for t, _ in self.alerts], ["FAAB tracker: log-thu FAILED", "FAAB tracker: stats corrected for an already-scored week"])

    def test_check_stats_that_cannot_run_is_only_a_note(self):
        crash = (1, "Traceback (most recent call last):\n  ...\nsqlite3.OperationalError: database is locked\n")   # exit 1, like "changed"
        rc = self.run_job("log-thu", self.THURSDAY, [(0, ""), self.LOGGED, crash])
        self.assertEqual((rc, self.alerts), (0, []))
        self.assertIn("OK", self.summary()[-1])
        self.assertIn("note: check-stats could not run (exit 1)", self.summary()[-1])

    def test_log_job_refreshes_then_logs_in_auto_mode_and_writes_both_logs(self):
        rc = self.run_job("log-sun", self.SUNDAY, [(0, "[DONE] refreshed\n"), (0, "noise\n[LOGGED] 2026w05-sun: pool 300\n  all_k10:\n")])
        self.assertEqual(rc, 0)
        self.assertEqual(self.alerts, [])
        self.assertIn("fetch_weekly_update.py", self.calls[0][1])
        self.assertEqual(self.calls[0][-2:], ["--season", "2026"])
        self.assertEqual(self.calls[1][-4:], ["log", "--auto", "--slot", "sun"])
        line = self.summary()[-1]
        self.assertIn("log-sun", line)
        self.assertIn("OK", line)
        self.assertIn("[LOGGED] 2026w05-sun", line)
        detail = (self.logs / "20261011_070005_log-sun.log").read_text(encoding="utf-8")
        self.assertIn("[DONE] refreshed", detail)
        self.assertIn("[exit 0]", detail)

    def test_quiet_skip_is_not_a_failure(self):
        rc = self.run_job("log-thu", self.THURSDAY, [(0, ""), (0, "[SKIP] 2026 week 18 has no thu slot: ...\n"), self.STATS_OK])
        self.assertEqual((rc, self.alerts), (0, []))
        self.assertIn("[SKIP]", self.summary()[-1])

    def test_closed_window_refusal_is_loud_and_says_nothing_was_written(self):
        rc = self.run_job("log-sun", self.SUNDAY, [(0, ""), (2, "[REFUSED] REFUSED: the sun slot closed at 2026-10-11T17:00:00+00:00\n")])
        self.assertEqual(rc, 2)
        self.assertIn("FAILED", self.summary()[-1])
        (title, text), = self.alerts
        self.assertIn("log-sun FAILED", title)
        self.assertIn("the sun slot closed", text)
        self.assertIn("Nothing was written to the ledger", text)

    def test_db_missing_prior_games_refusal_gets_the_failed_line_and_the_message_box(self):
        refusal = "[REFUSED] the DB is missing data for 1 already-played game(s)/week(s) before week 5, so O.D.D.S. would ...\n"
        rc = self.run_job("log-thu", self.THURSDAY, [(0, ""), (2, refusal), self.STATS_OK])
        self.assertEqual(rc, 2)
        self.assertIn("FAILED", self.summary()[-1])
        self.assertIn("the DB is missing data", self.summary()[-1])
        self.assertIn("the DB is missing data", self.alerts[0][1])

    def test_target_week_rows_refusal_gets_the_failed_line_and_the_message_box(self):
        refusal = ("[REFUSED] the DB already holds rows for the week being logged (2026 week 5): week 5 PHI@NYG: "
                   "41 player_week_stats row(s), 41 labels_player_week row(s). With week-5 stats loaded, ...\n")
        rc = self.run_job("log-sun", self.SUNDAY, [(0, ""), (2, refusal)])
        self.assertEqual(rc, 2)
        self.assertIn("FAILED", self.summary()[-1])
        self.assertIn("week 5 PHI@NYG", self.summary()[-1])
        (title, text), = self.alerts
        self.assertIn("log-sun FAILED", title)
        self.assertIn("already holds rows for the week being logged", text)
        self.assertIn("Nothing was written to the ledger", text)

    def test_log_job_woken_on_the_wrong_day_runs_nothing(self):
        """Missed on Sunday, PC back on Tuesday: --auto would now resolve to NEXT
        week and log its sun slot five days early. The job must not run at all."""
        rc = self.run_job("log-sun", datetime(2026, 10, 13, 9, 0, 0), [])
        self.assertEqual(rc, 3)
        self.assertEqual(self.calls, [])
        self.assertIn("MISSED", self.summary()[-1])
        self.assertIn("running on a Tuesday", self.alerts[0][1])

    def test_snapshot_runs_any_day_and_its_failure_is_loud(self):
        self.assertEqual(self.run_job("snapshot", datetime(2026, 10, 13, 9, 0, 0), [(0, "[OWNERSHIP] ESPN ok: 960 players\n")]), 0)
        self.assertEqual(self.calls[0][-1], "snapshot-ownership")
        self.assertEqual(self.run_job("snapshot", datetime(2026, 10, 14, 9, 0, 0), [(1, "[OWNERSHIP] ESPN FAILED: 0 players\n")]), 1)
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(len(self.summary()), 2)

    def test_failed_refresh_is_noted_but_the_log_step_still_decides(self):
        rc = self.run_job("log-thu", self.THURSDAY, [(1, "[WARN] 1 file(s) failed to refresh\n"), (0, "[LOGGED] 2026w05-thu\n"),
                                                     self.STATS_OK])
        self.assertEqual((rc, self.alerts), (0, []))
        self.assertIn("refresh nflverse files FAILED (exit 1)", self.summary()[-1])

    def test_step_that_cannot_start_is_reported_not_raised(self):
        code, out = scheduled_run.run_step([str(Path(self.tmp.name) / "no_such_python.exe")])
        self.assertEqual(code, 127)
        self.assertIn("could not start", out)

    def test_task_definitions_run_missed_starts_and_never_wake_the_pc(self):
        now = datetime(2026, 10, 3, 10, 0, 0)  # a Saturday
        starts = {}
        for job in scheduled_run.JOBS:
            xml = scheduled_run.task_xml(job, "PC\\me", now)
            self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>", xml)
            self.assertIn("<WakeToRun>false</WakeToRun>", xml)
            self.assertIn("<LogonType>InteractiveToken</LogonType>", xml)
            self.assertIn(f'scheduled_run.py" run {job}', xml)
            starts[job] = xml.split("<StartBoundary>")[1].split("<")[0]
        self.assertEqual(starts, {"snapshot": "2026-10-04T06:30:00", "log-thu": "2026-10-08T07:00:00",
                                  "log-sun": "2026-10-04T07:00:00"})
        self.assertIn("<Thursday />", scheduled_run.task_xml("log-thu", "u", now))
        self.assertIn("<Sunday />", scheduled_run.task_xml("log-sun", "u", now))
        self.assertIn("<ScheduleByDay>", scheduled_run.task_xml("snapshot", "u", now))


# -------------------------------------------------------------- crowd + score

class TestCrowd(unittest.TestCase):
    def test_crowd_hit_rules(self):
        con = mem_db()
        run_ts = "2026-10-01T12:00:00+00:00"
        ledger.insert_rows(con, "tr_runs", [dict(run_id="r1", run_timestamp=run_ts, season=2026, week=4, run_slot="sun")])
        for pid, own in (("rise", 20.0), ("flat", 20.0), ("star", 80.0), ("unknown", None), ("nosnap", 10.0)):
            ledger.insert_rows(con, "tr_pool", [dict(run_id="r1", player_id=pid, position="WR", percent_owned=own)])
            ledger.insert_rows(con, "tr_predictions", [dict(run_id="r1", model_name="odds_prod", pick_set="all_k10",
                                                            rank=len(con.execute("SELECT * FROM tr_predictions").fetchall()) + 1,
                                                            player_id=pid, position="WR")])
        for taken, rows in (("2026-10-05T00:00:00+00:00", {"rise": 40.0, "flat": 30.0, "star": 85.0}),
                            ("2026-10-10T00:00:00+00:00", {"rise": 61.0, "flat": 35.0, "star": 90.0}),
                            ("2026-09-30T00:00:00+00:00", {"flat": 99.0})):  # before the pick: ignored
            sid = con.execute("INSERT INTO tr_ownership_snapshots (taken_at, source, ok) VALUES (?, 'espn', 1)", (taken,)).lastrowid
            for pid, pct in rows.items():
                con.execute("INSERT INTO tr_ownership (snapshot_id, source, external_id, player_id, percent_owned) VALUES (?, 'espn', ?, ?, ?)",
                            (sid, pid, pid, pct))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tracker.compute_crowd(con, CFG, utc("2026-10-10T00:00:00"), export_dir=d), 0)  # window (14d) still open
            n = tracker.compute_crowd(con, CFG, utc("2026-10-20T00:00:00"), export_dir=d)
            self.assertEqual(n, 5)
            self.assertEqual(tracker.compute_crowd(con, CFG, utc("2026-10-21T00:00:00"), export_dir=d), 0)  # never recomputed
        got = {r["player_id"]: dict(r) for r in con.execute("SELECT * FROM tr_crowd_results")}
        self.assertEqual(got["rise"]["crowd_hit"], 1)
        self.assertEqual(got["rise"]["max_owned_in_window"], 61.0)
        self.assertEqual(got["flat"]["crowd_hit"], 0)
        self.assertEqual(got["flat"]["max_owned_in_window"], 35.0)  # the pre-pick 99% snapshot is not in the window
        self.assertIsNone(got["star"]["crowd_hit"])                 # already over 50% at pick time: can't "rise"
        self.assertIsNone(got["unknown"]["crowd_hit"])
        self.assertIsNone(got["nosnap"]["crowd_hit"])
        self.assertEqual(got["nosnap"]["snapshots_in_window"], 0)


class TestScoreEndToEnd(unittest.TestCase):
    """A full synthetic week through make_predictions -> score_one_week -> report."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.raw, self.export = d / "raw", d / "export"
        self.raw.mkdir()
        self.con = mem_db()
        self.con.execute("CREATE TABLE nfl_games (season INTEGER, week INTEGER, game_id TEXT, kickoff_utc TEXT, home_team TEXT, "
                         "away_team TEXT, home_score INTEGER, away_score INTEGER, is_playoffs INTEGER DEFAULT 0)")
        self.con.execute("INSERT INTO nfl_games VALUES (2026, 5, 'g', '2026-10-11T13:00:00', 'AAA', 'BBB', 20, 17, 0)")
        # 40 WRs/RBs; player i scores i points in week 5 and 8 points in each of weeks 2-4 (so spike needs >= 12)
        cols = ["player_id", "season", "week", "season_type", "position", "team", "receptions", "receiving_yards", "target_share"]
        rows = []
        self.pool = []
        for i in range(40):
            pos = "WR" if i % 2 == 0 else "RB"
            pid = f"p{i:02d}"
            for wk in (2, 3, 4):
                rows.append(dict(player_id=pid, season=2026, week=wk, season_type="REG", position=pos, team="AAA", receptions=0, receiving_yards=80, target_share=0.2))
            if i != 39:  # p39 DNPs
                rows.append(dict(player_id=pid, season=2026, week=5, season_type="REG", position=pos, team="AAA" if i % 2 else "BBB",
                                 receptions=0, receiving_yards=10 * i, target_share=0.2))  # i points
            self.pool.append(dict(run_id="r1", player_id=pid, name=pid, position=pos, team="AAA", percent_owned=float(i * 2),
                                  in_u50=1 if i * 2 < 50 else 0, odds_prod_score=i / 40, odds_shadow_score=(40 - i) / 40,
                                  snap_delta=(i % 7) / 10, target_share=0.1, last_week_points=8.0,
                                  last_week_label="2026w04", snap_recent=None, snap_prior=None, snap_games=4))
        with open(self.raw / "stats_player_week_2026.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        self.seed = 5
        preds, _ = tracker.make_predictions("r1", self.pool, CFG, self.seed, {})
        ledger.insert_rows(self.con, "tr_runs", [dict(run_id="r1", run_timestamp="2026-10-08T12:00:00+00:00", season=2026, week=5,
                                                      run_slot="thu", n_draws=300, dart_seed=self.seed, pool_size=40)])
        ledger.insert_rows(self.con, "tr_pool", [{k: v for k, v in r.items()} for r in self.pool])
        ledger.insert_rows(self.con, "tr_predictions", preds)
        self.con.commit()
        self.store = scoring.StatsStore(self.raw, RULES)

    def tearDown(self):
        self.tmp.cleanup()

    def score(self, **kw):
        return tracker.score_one_week(self.con, CFG, self.store, 2026, 5, datetime(2026, 10, 13, tzinfo=timezone.utc),
                                      export_dir=self.export, **kw)

    def test_scoring_hits_percentiles_and_dnp_handling(self):
        out = self.score()
        rr = {(r["model_name"], r["pick_set"]): r for r in out["run_results"]}
        prod = rr[("odds_prod", "all_k10")]
        # top-10 by score are p30..p39; p39 DNPs (0 pts, a miss); the rest score 30..38 -> the 24 best per position are everyone here (20 each)
        self.assertEqual(prod["n_picks"], 10)
        self.assertEqual(prod["n_played"], 9)
        self.assertAlmostEqual(prod["total_points"], sum(range(30, 39)))
        self.assertEqual(prod["top24_hits"], 9)  # 20 players per position < 24, so every player who played is "top-24"
        # spike: baseline 8 -> threshold 12; all of p30..p38 clear it
        self.assertEqual(prod["spike_hits"], 9)
        self.assertEqual(rr[("dart", "all_k10")]["n_draws"], 300)
        pct = {(p["model_name"], p["pick_set"], p["metric"]): p for p in out["percentiles"]}
        top_pts = pct[("odds_prod", "all_k10", "total_points")]
        self.assertGreater(top_pts["percentile"], 95)  # picked the highest scorers: beats nearly every random draw
        self.assertEqual(top_pts["n_draws"], 300)
        self.assertLessEqual(top_pts["dart_p05"], top_pts["dart_mean"])
        shadow_pts = pct[("odds_shadow", "all_k10", "total_points")]
        self.assertLess(shadow_pts["percentile"], 10)  # shadow picked the lowest scorers
        self.assertIn(("odds_prod", "u50_k10"), rr)    # the sleeper scoreboard exists as its own pick set

    def test_hit_definition_hash_is_stored_and_rescoring_needs_force(self):
        first = self.score()
        row = self.con.execute("SELECT * FROM tr_scorings").fetchone()
        self.assertEqual(row["hit_config_hash"], scoring.config_hash(CFG))
        self.assertEqual(row["scoring_id"], first["scoring_id"])
        self.assertIsNone(self.score())                      # same definition: skipped
        self.assertIsNotNone(self.score(force=True))        # appended, never overwritten
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tr_scorings").fetchone()[0], 2)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM tr_run_results WHERE scoring_id=?", (first["scoring_id"],)).fetchone()[0] > 0, True)

    # ---- stat corrections after a week was scored

    COLS = ["player_id", "season", "week", "season_type", "position", "team", "receptions", "receiving_yards", "target_share"]

    def rewrite_stats(self, change):
        """Rewrite the season file as nflverse would after a correction; `change(row)` edits or drops (returns None) each row."""
        path = self.raw / "stats_player_week_2026.csv"
        with open(path, encoding="utf-8", newline="") as f:
            rows = [r for r in (change(dict(r)) for r in csv.DictReader(f)) if r is not None]
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.COLS)
            w.writeheader()
            w.writerows(rows)
        return scoring.StatsStore(self.raw, RULES)   # a fresh reader, as every command gets

    def test_scoring_stores_the_file_hash_and_a_fingerprint_of_what_it_read(self):
        self.score()
        row = self.con.execute("SELECT stats_sha256, stats_fingerprint FROM tr_scorings").fetchone()
        self.assertEqual(row["stats_sha256"], hashlib.sha256((self.raw / "stats_player_week_2026.csv").read_bytes()).hexdigest())
        self.assertEqual(row["stats_fingerprint"], scoring.stats_fingerprint(self.store, 2026, 5))
        self.assertEqual([c["status"] for c in tracker.stats_changes(self.con, self.store, 2026)], ["unchanged"])

    def test_fingerprint_ignores_what_scoring_never_reads_and_catches_what_it_does(self):
        base = scoring.stats_fingerprint(self.store, 2026, 5)

        def fp(change):
            return scoring.stats_fingerprint(self.rewrite_stats(change), 2026, 5)
        # next Tuesday the file gains a week: must NOT look like a correction to week 5
        self.rewrite_stats(lambda r: r)
        with open(self.raw / "stats_player_week_2026.csv", "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.COLS).writerow(dict(player_id="p00", season=2026, week=6, season_type="REG",
                                                                  position="WR", team="AAA", receptions=9, receiving_yards=90))
        self.assertEqual(scoring.stats_fingerprint(scoring.StatsStore(self.raw, RULES), 2026, 5), base)
        self.assertEqual(fp(lambda r: dict(r, target_share=0.99)), base)              # a column scoring doesn't use
        self.assertNotEqual(fp(lambda r: dict(r, receiving_yards=11) if (r["player_id"], r["week"]) == ("p07", "5") else r), base)
        self.assertNotEqual(fp(lambda r: dict(r, position="TE") if (r["player_id"], r["week"]) == ("p07", "5") else r), base)
        self.assertNotEqual(fp(lambda r: dict(r, receiving_yards=81) if (r["player_id"], r["week"]) == ("p07", "3") else r), base)
        self.assertNotEqual(fp(lambda r: None if (r["player_id"], r["week"]) == ("p07", "5") else r), base)   # a row withdrawn

    def test_correction_to_the_scored_week_is_noticed_counted_and_fixed_by_a_forced_rescore(self):
        first = self.score()
        store = self.rewrite_stats(lambda r: dict(r, receiving_yards=int(r["receiving_yards"]) + 30)
                                   if r["week"] == "5" and r["player_id"] in ("p30", "p31") else r)
        (c,) = tracker.stats_changes(self.con, store, 2026)
        self.assertEqual((c["status"], c["n_differ"], c["scoring_id"]), ("changed", 2, first["scoring_id"]))
        self.assertIn("STATS CHANGED SINCE IT WAS SCORED -- 2 of 39 week-5 player scores differ", c["message"])
        self.assertIn("score --season 2026 --week 5 --force", c["message"])
        # the old scoring is left exactly as it was; a forced re-score appends the corrected one, and the check clears
        before = self.con.execute("SELECT * FROM tr_run_results WHERE scoring_id=? ORDER BY model_name, pick_set", (first["scoring_id"],)).fetchall()
        again = tracker.score_one_week(self.con, CFG, store, 2026, 5, datetime(2026, 10, 15, tzinfo=timezone.utc), force=True,
                                       export_dir=self.export)
        self.assertEqual(self.con.execute("SELECT * FROM tr_run_results WHERE scoring_id=? ORDER BY model_name, pick_set",
                                          (first["scoring_id"],)).fetchall(), before)
        rr = {(r["model_name"], r["pick_set"]): r for r in again["run_results"]}
        self.assertAlmostEqual(rr[("odds_prod", "all_k10")]["total_points"], sum(range(30, 39)) + 6.0)   # +30 yds each = +3.0 pts each
        (c,) = tracker.stats_changes(self.con, store, 2026)
        self.assertEqual((c["status"], c["scoring_id"]), ("unchanged", again["scoring_id"]))

    def test_correction_to_an_earlier_week_is_noticed_as_a_baseline_change(self):
        self.score()
        store = self.rewrite_stats(lambda r: dict(r, receiving_yards=120) if (r["player_id"], r["week"]) == ("p10", "3") else r)
        (c,) = tracker.stats_changes(self.con, store, 2026)
        self.assertEqual((c["status"], c["n_differ"]), ("changed", 0))
        self.assertIn("the correction is in an earlier week, which feeds the spike baselines", c["message"])

    def test_scoring_from_before_fingerprints_is_unknown_not_unchanged(self):
        self.con.execute("INSERT INTO tr_scorings (season, week, scored_at, hit_config_hash, hit_config_json) VALUES (2026, 5, 't', 'h', '{}')")
        (c,) = tracker.stats_changes(self.con, self.store, 2026)
        self.assertEqual(c["status"], "unknown")
        self.assertIn("re-score once to set a baseline", c["message"])

    def test_report_carries_the_warning_and_a_clean_report_does_not(self):
        self.score()
        self.assertNotIn("stats corrected after scoring", report.build_weekly_report(self.con, 2026, 5))
        note = "2026 week 5 (scoring 1, ...): STATS CHANGED SINCE IT WAS SCORED -- 2 of 39 week-5 player scores differ."
        for md in (report.build_weekly_report(self.con, 2026, 5, stats_notes=[note]),
                   report.build_season_report(self.con, 2026, CFG["report"], stats_notes=[note])):
            self.assertIn("> **Warning -- stats corrected after scoring.** 2026 week 5", md)
            self.assertLess(md.index("stats corrected after scoring"), md.index("## All eligible players"))   # above the numbers

    def test_score_report_and_check_stats_commands_all_raise_the_flag(self):
        db = Path(self.tmp.name) / "cli.db"
        disk = sqlite3.connect(db)
        self.con.backup(disk)
        disk.close()
        common = ["--db", str(db), "--raw-dir", str(self.raw), "--export-dir", str(self.export)]

        def run(*argv):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = tracker.main(list(argv) + common)
            return rc, out.getvalue(), err.getvalue()
        self.assertEqual(run("score", "--season", "2026", "--week", "5")[0], 0)
        rc, out, err = run("check-stats", "--season", "2026")
        self.assertEqual((rc, err), (0, ""))
        self.assertIn("stats unchanged", out)
        self.rewrite_stats(lambda r: dict(r, receiving_yards=400) if (r["player_id"], r["week"]) == ("p30", "5") else r)
        rc, _out, err = run("check-stats", "--season", "2026")
        self.assertEqual(rc, 1)                                            # non-zero, so a script or task notices
        self.assertIn("[STATS CHANGED] 2026 week 5", err)
        rc, out, err = run("score", "--season", "2026", "--week", "5")     # same hit definition: skipped, but not silently
        self.assertEqual(rc, 0)
        self.assertIn("[SKIP]", out)
        self.assertIn("[STATS CHANGED] 2026 week 5", err)
        rc, out, err = run("report", "--season", "2026", "--week", "5", "--out-dir", str(Path(self.tmp.name) / "reports"))
        self.assertIn("[STATS CHANGED]", err)
        self.assertIn("> **Warning -- stats corrected after scoring.**", out)
        rc, out, err = run("score", "--season", "2026", "--week", "5", "--force")
        self.assertIn("[SCORED]", out)
        self.assertNotIn("[STATS CHANGED]", err)
        self.assertEqual(run("check-stats", "--season", "2026")[0], 0)

    def test_incomplete_week_and_stale_stats_are_refused(self):
        self.con.execute("UPDATE nfl_games SET home_score = NULL")
        with self.assertRaises(tracker.Refused):
            self.score()
        self.con.execute("UPDATE nfl_games SET home_score = 20, home_team = 'ZZZ'")  # a team with no stat rows
        with self.assertRaises(tracker.Refused):
            self.score()

    def test_run_logged_before_the_pool_rule_is_footnoted_and_a_run_under_it_is_not(self):
        self.score()
        legacy = "was logged before the Out/Doubtful pool rule existed"
        self.assertIn(legacy, report.build_weekly_report(self.con, 2026, 5))
        self.assertIn(legacy, report.build_season_report(self.con, 2026, CFG["report"]))
        row = {k: self.con.execute("SELECT * FROM tr_runs").fetchone()[k] for k in ("run_id", "week", "run_slot")}
        for source, expect in (("nflverse", None), ("espn-fallback", "using ESPN's injury status instead"),
                               ("unavailable", "exclusion was NOT applied")):
            cfg = json.dumps({"pool_rule": tracker.POOL_RULE, "injury": {"source": source}})
            notes = report.pool_rule_notes([dict(row, run_config_json=cfg)])
            self.assertEqual(len(notes), 0 if expect is None else 1)
            if expect:
                self.assertIn(expect, notes[0])
                self.assertIn("week 5 `thu` run", notes[0])

    def test_run_logged_with_bye_week_players_in_its_pool_is_footnoted_from_the_ledger(self):
        run = dict(self.con.execute("SELECT * FROM tr_runs").fetchone())
        self.assertEqual(report.no_game_notes(self.con, [run]), [])          # everyone in this pool plays (AAA vs BBB)
        for i, model in enumerate(("odds_prod", "last_week", "last_week")):   # three bye-week players, three top-10 picks among them
            ledger.insert_rows(self.con, "tr_pool", [dict(run_id="r1", player_id=f"bye{i}", position="WR", team="ZZZ" if i else "YYY")])
            ledger.insert_rows(self.con, "tr_predictions", [dict(run_id="r1", model_name=model, pick_set="all_k10", rank=90 + i,
                                                                 player_id=f"bye{i}", position="WR")])
        (note,) = report.no_game_notes(self.con, [run])
        self.assertIn("week 5 `thu` run was logged before players whose team has no game that week were excluded", note)
        self.assertIn("3 of its 43 pool players were on a bye (YYY, ZZZ)", note)
        self.assertIn("O.D.D.S. 1, shadow 0, snap-share heuristic 0, last week's points 2", note)
        self.assertIn("about 7% of every dart draw", note)
        with contextlib.redirect_stderr(io.StringIO()):   # the pool was edited after logging, so the dart self-check complains
            self.score()
        self.assertIn("were on a bye (YYY, ZZZ)", report.build_weekly_report(self.con, 2026, 5))
        self.assertIn("were on a bye (YYY, ZZZ)", report.build_season_report(self.con, 2026, CFG["report"]))
        v2 = dict(run, run_config_json=json.dumps({"pool_rule": tracker.POOL_RULE, "excluded_no_game": []}))
        self.assertEqual(report.no_game_notes(self.con, [v2]), [])            # a run under the rule can't have any

    def test_report_shows_the_unknown_ownership_count_and_says_when_an_old_run_dropped_them(self):
        self.con.execute("INSERT INTO tr_ownership_snapshots (snapshot_id, taken_at, source, ok) VALUES (1, '2026-10-08T11:30:00+00:00', 'espn', 1)")
        run = dict(self.con.execute("SELECT * FROM tr_runs").fetchone(), ownership_snapshot_id=1)
        v2 = dict(run, run_config_json=json.dumps({"sleeper_pool_rule": tracker.SLEEPER_POOL_RULE}))
        # the fixture pool: 40 players, the 25 under 50% in the sleeper pool, everyone's ownership known
        self.assertIn("25 players, 0 of them with **ownership unknown**", report.sleeper_pool_lines(self.con, [v2])[0])
        self.assertEqual(report.sleeper_pool_lines(self.con, [v2], only_if_unknown=True), [])
        self.assertIn("0 pool player(s) with unknown ownership were left OUT", report.sleeper_pool_lines(self.con, [run])[0])
        for pid, in_u50 in (("kept_unknown", 1), ("dropped_unknown", 0)):
            ledger.insert_rows(self.con, "tr_pool", [dict(run_id="r1", player_id=pid, position="WR", percent_owned=None, in_u50=in_u50)])
        self.assertIn("26 players, 1 of them with **ownership unknown**", report.sleeper_pool_lines(self.con, [v2], only_if_unknown=True)[0])
        legacy = report.sleeper_pool_lines(self.con, [run], only_if_unknown=True)[0]
        self.assertIn("1 pool player(s) with unknown ownership were left OUT of it", legacy)
        self.assertEqual(report.sleeper_pool_lines(self.con, [dict(v2, ownership_snapshot_id=None)]), [])   # no snapshot, no sleeper pool

    def test_run_on_a_roster_snapshot_older_than_twelve_hours_is_footnoted(self):
        self.assertEqual(CFG["run"]["ownership_stale_note_hours"], 12)
        self.assertLess(CFG["run"]["ownership_stale_note_hours"], CFG["run"]["ownership_max_age_hours"])
        run = dict(self.con.execute("SELECT * FROM tr_runs").fetchone())      # logged 2026-10-08T12:00 UTC
        for sid, taken in ((1, "2026-10-08T11:30:00+00:00"), (2, "2026-10-07T11:30:00+00:00")):
            self.con.execute("INSERT INTO tr_ownership_snapshots (snapshot_id, taken_at, source, ok) VALUES (?, ?, 'espn', 1)", (sid, taken))
        self.assertEqual(report.ownership_notes(self.con, [dict(run, ownership_snapshot_id=None)]), [])   # no snapshot: nothing to say here
        self.assertEqual(report.ownership_notes(self.con, [dict(run, ownership_snapshot_id=1)]), [])      # that morning's
        notes = report.ownership_notes(self.con, [dict(run, ownership_snapshot_id=2)])                    # yesterday's
        self.assertEqual(len(notes), 1)
        self.assertIn("week 5 `thu` run used a roster-% snapshot that was 24 hours old", notes[0])
        self.assertIn("taken 2026-10-07 11:30 UTC", notes[0])
        # a run's own limit, stored with it at log time, wins over today's default
        strict = dict(run, ownership_snapshot_id=1, run_config_json=json.dumps({"run": {"ownership_stale_note_hours": 0.25}}))
        self.assertEqual(len(report.ownership_notes(self.con, [strict])), 1)

    def test_exports_are_written_and_report_renders(self):
        self.score()
        self.assertTrue(list(self.export.glob("2026_week05_scoring_*.jsonl")))
        md = report.build_weekly_report(self.con, 2026, 5)
        self.assertIn("Sleeper pool", md)
        self.assertIn("Known leak", md)
        self.assertIn("Dart pctile", md)
        season = report.build_season_report(self.con, 2026, CFG["report"])
        self.assertIn("Too early to call a winner", season)
        self.assertIn("CI needs 3+ weeks", season)        # one week must not show a fake zero-width interval
        self.assertNotIn("(50.0-50.0)", season)


class TestReportMath(unittest.TestCase):
    def test_bootstrap_ci_is_deterministic_and_brackets_the_estimate(self):
        a = report.bootstrap_ratio_ci([3, 2, 4, 1, 3], [10] * 5, 2000, 1)
        b = report.bootstrap_ratio_ci([3, 2, 4, 1, 3], [10] * 5, 2000, 1)
        self.assertEqual(a, b)
        est, lo, hi = a
        self.assertAlmostEqual(est, 0.26)
        self.assertLessEqual(lo, est)
        self.assertGreaterEqual(hi, est)

    def test_constant_results_have_a_degenerate_interval(self):
        est, lo, hi = report.bootstrap_ratio_ci([2, 2, 2], [10, 10, 10], 500, 1)
        self.assertEqual((est, lo, hi), (0.2, 0.2, 0.2))

    def test_more_weeks_narrow_the_interval(self):
        few = report.bootstrap_ratio_ci([3, 1, 2], [10] * 3, 3000, 1)
        many = report.bootstrap_ratio_ci([3, 1, 2] * 8, [10] * 24, 3000, 1)
        self.assertLess(many[2] - many[1], few[2] - few[1])

    def test_paired_difference_and_mean_ci(self):
        est, lo, hi = report.bootstrap_diff_ci([5, 5, 5], [10] * 3, [2, 2, 2], [10] * 3, 500, 1)
        self.assertAlmostEqual(est, 0.3)
        self.assertEqual((round(lo, 6), round(hi, 6)), (0.3, 0.3))
        m, lo, hi = report.bootstrap_mean_ci([60.0, 80.0, None], 500, 1)
        self.assertAlmostEqual(m, 70.0)

    def test_hit_formatting(self):
        self.assertEqual(report.fmt_hits(3, 10), "3/10 (30%)")
        self.assertEqual(report.fmt_hits(2.14, 10), "2.1/10 (21%)")
        self.assertEqual(report.fmt_hits(None, 0), "-")


if __name__ == "__main__":
    unittest.main()
