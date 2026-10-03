#!/usr/bin/env python3
"""Tests for the baseline tracker. From the repo root:

    python -m unittest discover -s tracker -v

Stdlib unittest only; every test uses temp dirs / in-memory SQLite, never the
real DB or predictions/.
"""
import csv
import gzip
import hashlib
import sqlite3
import tempfile
import unittest
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

import baselines
import ledger
import ownership
import report
import scoring
import tracker

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

    def test_sun_closes_at_the_nine_thirty_international_kickoff_not_one_pm(self):
        cutoff = utc("2026-10-04T13:30:00")  # 09:30 EDT
        self.assertEqual(tracker.slot_cutoff(WEEK4_2026, "sun")[0], cutoff)
        self.assertTrue(tracker.check_slot_window(WEEK4_2026, "sun", cutoff - timedelta(minutes=1))[0])
        self.assertFalse(tracker.check_slot_window(WEEK4_2026, "sun", cutoff)[0])

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

    def test_sun_run_after_first_sunday_kickoff_is_refused(self):
        self.assertEqual(self.run_log(4, "sun", "2026-10-04T13:31:00+00:00"), 2)
        self.assert_nothing_written()

    def test_thu_run_in_a_week_with_no_thursday_game_is_refused(self):
        self.assertEqual(self.run_log(5, "thu", "2026-10-07T12:00:00+00:00"), 2)
        self.assert_nothing_written()

    def test_run_already_in_the_ledger_is_refused(self):
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO tr_runs (run_id, run_timestamp, season, week, run_slot) VALUES ('x','t',2026,4,'sun')")
        con.commit()
        con.close()
        self.assertEqual(self.run_log(4, "sun", "2026-10-03T15:00:00+00:00"), 2)
        self.assertEqual(self.count("tr_runs"), 1)


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

    def test_incomplete_week_and_stale_stats_are_refused(self):
        self.con.execute("UPDATE nfl_games SET home_score = NULL")
        with self.assertRaises(tracker.Refused):
            self.score()
        self.con.execute("UPDATE nfl_games SET home_score = 20, home_team = 'ZZZ'")  # a team with no stat rows
        with self.assertRaises(tracker.Refused):
            self.score()

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
