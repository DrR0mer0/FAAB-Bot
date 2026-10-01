"""Tests for lineup_sim. From the repo root: python -m unittest discover -s startsit -v"""
import copy
import json
import math
import os
import tempfile
import unittest

import numpy as np

import lineup_sim as ls

HERE = os.path.dirname(os.path.abspath(__file__))


def load_example():
    with open(os.path.join(HERE, "example_week.json"), encoding="utf-8") as f:
        return json.load(f)


def flex_week(opp_median):
    """Tiny league: RB + flex. Steady Eddie vs Boom Bust compete for the flex.

    Same median; Boom has a much wider range. A fixed-score opponent sets
    whether we're the favorite or the underdog.
    """
    return {
        "league": {"slots": ["RB", "W/R/T"]},
        "my_team": {"players": [
            {"id": "anchor", "name": "Anchor", "pos": "RB", "p10": 10, "p50": 15, "p90": 20},
            {"id": "steady", "name": "Steady Eddie", "pos": "WR", "p10": 9, "p50": 11, "p90": 13},
            {"id": "boom", "name": "Boom Bust", "pos": "WR", "p10": 1, "p50": 11, "p90": 30},
        ]},
        "opponent": {"starters": [
            {"id": "o1", "name": "Opp", "pos": "QB", "actual": opp_median},
        ]},
    }


def starters(result):
    return {row["player"]["id"] for row in result["lineup"] if row["player"]}


class TestDecisions(unittest.TestCase):
    def test_underdog_chases_ceiling(self):
        r = ls.run_week(flex_week(opp_median=40), n_sims=20000, seed=1)
        self.assertIn("boom", starters(r))
        self.assertLess(r["win_prob"], 0.5)

    def test_favorite_protects_floor(self):
        r = ls.run_week(flex_week(opp_median=22), n_sims=20000, seed=1)
        self.assertIn("steady", starters(r))
        self.assertGreater(r["win_prob"], 0.5)

    def test_explains_when_win_prob_beats_max_points(self):
        w = flex_week(21)
        players = w["my_team"]["players"]
        players[0].update(p10=15, p50=15, p90=15)
        players[1].update(p10=10, p50=11, p90=12)
        players[2].update(p10=0, p50=10, p90=35)  # higher mean, far riskier
        r = ls.run_week(w, n_sims=20000, seed=1)
        self.assertIn("steady", starters(r))
        self.assertEqual(set(r["max_points_lineup"]), {"anchor", "boom"})
        self.assertIn("Why not the highest-projected lineup?", r["report_md"])

    def test_no_explanation_for_noise(self):
        w = flex_week(30)
        w["my_team"]["players"][2].update(p10=9, p50=11, p90=13)  # Boom now identical to Steady
        r = ls.run_week(w, n_sims=20000, seed=1)
        self.assertNotIn("Why not the highest-projected lineup?", r["report_md"])
        self.assertEqual(r["close_calls"][0]["label"], "coin flip")

    def test_example_runs_and_fills_every_slot(self):
        r = ls.run_week(load_example(), n_sims=5000, seed=3)
        self.assertTrue(all(row["player"] for row in r["lineup"]))
        self.assertEqual(len(r["lineup"]), 7)  # BN / IR slots ignored
        self.assertNotIn("jacobs", starters(r))
        self.assertIn("jacobs", {p["id"] for p in r["out"]})
        self.assertIn("Win probability", r["report_md"])

    def test_same_seed_same_answer(self):
        a = ls.run_week(load_example(), n_sims=3000, seed=11)
        b = ls.run_week(load_example(), n_sims=3000, seed=11)
        self.assertEqual(a["win_prob"], b["win_prob"])
        self.assertEqual(starters(a), starters(b))

    def test_no_opponent_maximizes_points(self):
        w = load_example()
        del w["opponent"]
        r = ls.run_week(w, n_sims=3000, seed=2)
        self.assertIsNone(r["win_prob"])
        self.assertTrue(any("no opponent" in x for x in r["warnings"]))
        self.assertIn("mccaffrey", starters(r))

    def test_short_handed_leaves_slot_empty(self):
        w = flex_week(30)
        for p in w["my_team"]["players"]:
            if p["id"] != "anchor":
                p["status"] = "OUT"
        r = ls.run_week(w, n_sims=2000, seed=0)
        self.assertEqual(starters(r), {"anchor"})
        self.assertIn("can't be filled", r["report_md"])


class TestLocks(unittest.TestCase):
    def test_locked_starter_stays_with_actual_score(self):
        w = load_example()
        for p in w["my_team"]["players"]:
            if p["id"] == "tate":  # say he played Thursday night in the lineup
                p.update(locked=True, started=True, actual=2.5)
        r = ls.run_week(w, n_sims=3000, seed=4)
        self.assertIn("tate", starters(r))

    def test_locked_bench_player_cannot_come_in(self):
        w = load_example()
        for p in w["my_team"]["players"]:
            if p["id"] == "mccaffrey":  # kicked off on the bench
                p.update(locked=True, started=False, actual=30.0)
        r = ls.run_week(w, n_sims=3000, seed=4)
        self.assertNotIn("mccaffrey", starters(r))

    def test_locked_slot_is_respected(self):
        w = load_example()
        for p in w["my_team"]["players"]:
            if p["id"] == "henry":
                p.update(locked=True, started=True, locked_slot="W/R/T", actual=12.0)
        meta, slots, mine, opp, _ = ls.parse_week(w)
        d = ls.decide(meta, slots, mine, opp, n_sims=2000, seed=5)
        flex_i = next(i for i, s in enumerate(slots) if s.name == "W/R/T")
        self.assertEqual(d["assignment"][flex_i].id, "henry")


class TestSimulation(unittest.TestCase):
    def test_quantiles_reproduced(self):
        meta, slots, mine, opp, _ = ls.parse_week(load_example())
        p = next(x for x in mine if x.id == "dobbins")
        pts = ls.simulate([p], 200_000, np.random.default_rng(0))[:, 0]
        for q, target in ((10, p.p10), (50, p.p50), (90, p.p90)):
            self.assertAlmostEqual(np.percentile(pts, q), target, delta=0.15)
        self.assertGreater(pts.mean(), p.p50)  # right-skewed

    def test_play_prob_zeroes_out(self):
        w = flex_week(20)
        w["my_team"]["players"][1]["play_prob"] = 0.25
        meta, slots, mine, opp, _ = ls.parse_week(w)
        pts = ls.simulate(mine, 50_000, np.random.default_rng(1))
        self.assertAlmostEqual((pts[:, 1] == 0).mean(), 0.75, delta=0.01)

    def test_same_team_qb_wr_correlated(self):
        week = {"my_team": {"players": [
            {"name": "QB", "pos": "QB", "team": "AAA", "p10": 10, "p50": 18, "p90": 27},
            {"name": "WR", "pos": "WR", "team": "AAA", "p10": 4, "p50": 12, "p90": 22},
            {"name": "WR2", "pos": "WR", "team": "BBB", "p10": 4, "p50": 12, "p90": 22},
        ]}}
        _, _, mine, _, _ = ls.parse_week(week)
        pts = ls.simulate(mine, 100_000, np.random.default_rng(2))
        c = np.corrcoef(pts.T)
        self.assertGreater(c[0, 1], 0.25)
        self.assertLess(abs(c[0, 2]), 0.02)

    def test_correlation_matrix_always_valid(self):
        # Many same-team players: pairwise defaults alone could be invalid.
        players = [{"name": f"P{i}", "pos": pos, "team": "AAA", "p50": 10}
                   for i, pos in enumerate(["QB", "RB", "RB", "RB", "WR", "WR", "WR", "TE", "K", "DEF"])]
        _, _, mine, _, _ = ls.parse_week({"my_team": {"players": players}})
        c = ls.correlation_matrix(mine)
        self.assertTrue(np.allclose(np.diag(c), 1.0))
        np.linalg.cholesky(c)  # raises if not positive definite


class TestContract(unittest.TestCase):
    def test_slot_shorthand(self):
        problems = []
        slots = ls._parse_slots(["QB", "W/R/T", "Q/W/R/T", "BN", {"position": "WR", "count": 2}, "D/ST"], problems)
        self.assertEqual(problems, [])
        self.assertEqual([s.eligible for s in slots],
                         [("QB",), ("WR", "RB", "TE"), ("QB", "WR", "RB", "TE"), ("WR",), ("WR",), ("DEF",)])

    def test_median_only_gets_assumed_spread(self):
        w = {"my_team": {"players": [{"name": "X", "pos": "WR", "proj": 10}]}}
        _, _, mine, _, warnings = ls.parse_week(w)
        self.assertEqual((mine[0].p10, mine[0].p90), (3.0, 19.0))
        self.assertTrue(any("assumed" in x for x in warnings))

    def test_status_defaults(self):
        w = {"my_team": {"players": [
            {"name": "Q", "pos": "WR", "p50": 10, "status": "Q"},
            {"name": "D", "pos": "WR", "p50": 10, "status": "D"},
            {"name": "Qx", "pos": "WR", "p50": 10, "status": "Q", "play_prob": 0.95},
            {"name": "IR", "pos": "RB", "status": "IR"},
        ]}}
        _, _, mine, _, _ = ls.parse_week(w)
        self.assertEqual([p.play_prob for p in mine], [0.8, 0.3, 0.95, 0.0])

    def test_all_problems_reported_at_once(self):
        w = {"schema_version": 99, "my_team": {"players": [
            {"name": "", "pos": "WR", "p50": 10},
            {"name": "Bad", "pos": "RB", "p10": 20, "p50": 10, "p90": 30},
            {"name": "NoProj", "pos": "TE"},
            {"name": "Prob", "pos": "QB", "p50": 15, "play_prob": 1.5},
        ]}}
        with self.assertRaises(ls.WeekFileError) as cm:
            ls.parse_week(w)
        text = "\n".join(cm.exception.problems)
        for needle in ("schema_version", "missing name", "p10 <= p50 <= p90", "needs p50", "play_prob"):
            self.assertIn(needle, text)

    def test_cli_exit_codes(self):
        bad = os.path.join(tempfile.gettempdir(), "_faab_bad_week.json")
        with open(bad, "w") as f:
            json.dump({"my_team": {"players": []}}, f)
        try:
            self.assertEqual(ls.main([bad]), 2)
        finally:
            os.remove(bad)


if __name__ == "__main__":
    unittest.main()
