"""Tests for the v3 candidate's guards (2026-11-17): model/availability.py (the 2016 cutoff, GSIS team codes, the one
shared status set), starter_absent_asof before the cutoff, score_week.py's wrapped candidate step, the two data guards
in model/train_candidate_v3.py, and the labels script's write guard. From the repo root:

    python -m unittest discover -s tracker -v

Made-up player ids and roster rows; no network, no real DB.
"""
import contextlib
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
for d in ("model", "tracker", "data", "evaluation"):
    sys.path.insert(0, str(REPO_ROOT / d))
import availability as av_mod  # noqa: E402
import features_lib  # noqa: E402
import score_week  # noqa: E402
import train_candidate_v3 as tc  # noqa: E402
import generate_labels_and_breakouts as gl  # noqa: E402

ROSTER_HEADER = "season,team,position,status,gsis_id,week,game_type\n"


def availability_from(rows_by_season, injuries_by_season=None):
    """An Availability built through the real CSV loaders from made-up rows."""
    with tempfile.TemporaryDirectory() as d:
        for s, rows in rows_by_season.items():
            Path(d, f"roster_weekly_{s}.csv").write_text(ROSTER_HEADER + "".join(rows), encoding="utf-8")
            inj = (injuries_by_season or {}).get(s, [])
            Path(d, f"injuries_{s}.csv").write_text("season,game_type,team,week,gsis_id,report_status\n" + "".join(inj),
                                                   encoding="utf-8")
        return av_mod.Availability.from_nflverse(d, sorted(rows_by_season), norm_team=features_lib.norm_team)


class TestAvailabilityCutoff(unittest.TestCase):
    def test_seasons_before_2016_are_unknown_even_when_the_files_say_absent(self):
        self.assertEqual(av_mod.WEEKLY_ROSTER_FIRST_SEASON, 2016)
        av = availability_from({2015: ["2015,KC,RB,RES,s1,5,REG\n", "2015,KC,RB,ACT,b1,5,REG\n"]},
                               {2015: ["2015,REG,KC,5,s1,Out\n"]})
        self.assertFalse(av.has_week(2015, 5))
        self.assertIsNone(av.absent(2015, 5, "s1", "KC"))          # RES and Out in the files: still unknown

    def test_2016_on_reads_the_week(self):
        av = availability_from({2016: ["2016,KC,RB,RES,s1,5,REG\n", "2016,KC,RB,ACT,b1,5,REG\n"]},
                               {2016: ["2016,REG,KC,5,b1,Questionable\n"]})
        self.assertTrue(av.has_week(2016, 5))
        self.assertIs(av.absent(2016, 5, "s1", "KC"), True)
        self.assertIs(av.absent(2016, 5, "b1", "KC"), False)        # Questionable: available

    def test_starter_absent_asof_is_null_for_every_row_before_the_cutoff(self):
        av = availability_from({2014: ["2014,KC,RB,RES,s1,5,REG\n"], 2017: ["2017,KC,RB,RES,s1,5,REG\n"]})
        engine = SimpleNamespace(availability=av, no_touch_signal_pos=set(),
                                 _presumed_starter_asof=lambda season, week, team, pos: "s1")
        asof = features_lib.FeatureEngine.starter_absent_asof
        self.assertIsNone(asof(engine, 2014, 5, "b1", "KC", "RB"))   # a backup
        self.assertIsNone(asof(engine, 2014, 5, "s1", "KC", "RB"))   # the starter himself: NULL too, not 0
        self.assertEqual(asof(engine, 2017, 5, "b1", "KC", "RB"), 1)
        self.assertEqual(asof(engine, 2017, 5, "s1", "KC", "RB"), 0)


class TestTeamCodesAndStatuses(unittest.TestCase):
    def test_gsis_team_codes_are_not_another_team(self):
        self.assertEqual(av_mod.GSIS_TEAM_ALIASES, {"ARZ": "ARI", "BLT": "BAL", "CLV": "CLE", "HST": "HOU", "SL": "LA"})
        rows = [f"2016,{code},WR,ACT,p{i},3,REG\n" for i, code in enumerate(("ARZ", "BLT", "CLV", "HST", "SL", "OAK"))]
        av = availability_from({2016: rows})
        for i, team in enumerate(("ARI", "BAL", "CLE", "HOU", "LA", "LV")):
            self.assertIs(av.absent(2016, 3, f"p{i}", team), False, team)
        self.assertIs(av.absent(2016, 3, "p0", "ARZ"), False)        # the caller's code is mapped the same way

    def test_one_status_set_exempt_list_counts_trade_codes_go_to_the_team_check(self):
        self.assertIs(score_week.ABSENT_ROSTER_STATUSES, av_mod.ABSENT_ROSTER_STATUSES)
        self.assertTrue({"E01", "E14", "EXE", "RSN", "NFI", "DEV", "CUT", "RET"} <= av_mod.ABSENT_ROSTER_STATUSES)
        self.assertFalse({"TRC", "TRD", "TRT", "INA", "ACT"} & av_mod.ABSENT_ROSTER_STATUSES)
        av = availability_from({2020: ["2020,NE,WR,E14,a,4,REG\n", "2020,NE,WR,E01,b,4,REG\n",
                                       "2020,NE,WR,TRD,c,4,REG\n", "2020,BUF,WR,TRD,d,4,REG\n",
                                       "2020,NE,WR,INA,e,4,REG\n"]})
        self.assertIs(av.absent(2020, 4, "a", "NE"), True)
        self.assertIs(av.absent(2020, 4, "b", "NE"), True)
        self.assertIs(av.absent(2020, 4, "c", "NE"), False)          # trade code, still on NE: available
        self.assertIs(av.absent(2020, 4, "d", "NE"), True)           # on another team
        self.assertIs(av.absent(2020, 4, "e", "NE"), False)          # INA (game-day list) is never read


class TestCandidateStepIsolation(unittest.TestCase):
    def _outputs(self, d):
        prod = Path(d) / "2026_week11.json"
        files = [prod, prod.with_suffix(".md"), prod.with_name("2026_week11_shadow.json"),
                 prod.with_name("2026_week11_shadow.md")]
        for i, f in enumerate(files):
            f.write_text(f"production/shadow output {i}\n", encoding="utf-8")
        return prod, {f: f.read_bytes() for f in files}

    def test_a_failing_candidate_step_is_skipped_loudly_and_leaves_production_and_shadow_alone(self):
        with tempfile.TemporaryDirectory() as d:
            prod, before = self._outputs(d)
            cand_json, cand_md = score_week.candidate_outputs(prod)
            self.assertEqual((cand_json.name, cand_md.name), ("2026_week11_candidate.json", "2026_week11_candidate.md"))

            def step():
                cand_json.write_text("{partial", encoding="utf-8")
                cand_md.write_text("partial", encoding="utf-8")
                raise RuntimeError("forced failure inside the candidate step")

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                ok = score_week.run_candidate_step(step, prod)
            self.assertFalse(ok)
            self.assertIn(score_week.CANDIDATE_SKIPPED_TAG, err.getvalue())
            self.assertIn("forced failure", err.getvalue())
            self.assertFalse(cand_json.exists() or cand_md.exists())
            self.assertEqual({f: f.read_bytes() for f in before}, before)

    def test_a_working_step_runs_and_reports_true(self):
        with tempfile.TemporaryDirectory() as d:
            prod, before = self._outputs(d)
            ran = []
            self.assertTrue(score_week.run_candidate_step(lambda: ran.append(1), prod))
            self.assertEqual(ran, [1])
            self.assertEqual({f: f.read_bytes() for f in before}, before)


class TestTrainingDataGuards(unittest.TestCase):
    def _df(self, season, n, n_null=0):
        import pandas as pd
        return pd.DataFrame({"season": [season] * n, "week": [3] * n, "player_id": [f"p{i}" for i in range(n)],
                             "pos": ["RB"] * n, "team": ["KC"] * n,
                             "starter_absent_asof": [None] * n_null + [0] * (n - n_null)})

    def test_rates_per_season_and_the_cutoff(self):
        import pandas as pd
        df = pd.concat([self._df(2014, 100, n_null=100), self._df(2017, 200, n_null=10)], ignore_index=True)
        fake = SimpleNamespace(absent=lambda s, w, pid, team: None if s < 2016 else (pid in ("p0",)))
        with contextlib.redirect_stdout(io.StringIO()):
            g = tc.data_guards(df, fake)
        self.assertEqual(g[2014]["asof_null_rate"], 1.0)
        self.assertFalse(g[2014]["guarded"])
        self.assertEqual(g[2014]["availability_unknown_rate"], 1.0)
        self.assertAlmostEqual(g[2017]["asof_null_rate"], 0.05)
        self.assertAlmostEqual(g[2017]["played_absent_rate"], 0.005)
        tc.check_guards(g)                                           # 2014 is all NULL by design: no stop

    def test_played_but_absent_over_1_percent_stops(self):
        self.assertEqual(tc.MAX_PLAYED_ABSENT_RATE, 0.01)
        g = {2018: {"guarded": True, "asof_null_rate": 0.01, "played_absent_rate": 0.012}}
        with self.assertRaises(SystemExit) as cm:
            tc.check_guards(g)
        self.assertIn("2018 1.20%", str(cm.exception))
        tc.check_guards({2012: {"guarded": False, "asof_null_rate": 1.0, "played_absent_rate": 0.25}})

    def test_null_over_20_percent_from_2016_stops(self):
        with self.assertRaises(SystemExit):
            tc.check_guards({2016: {"guarded": True, "asof_null_rate": 0.21, "played_absent_rate": 0.0}})


class TestLabelsWriteGuard(unittest.TestCase):
    def test_a_non_half12_profile_into_labels_player_week_is_refused_before_any_write(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "x.db"
            sqlite3.connect(db).close()
            argv = ["generate_labels_and_breakouts.py", "--db", str(db), "--profile", "league_v1", "--seasons", "2024"]
            with mock.patch.object(sys, "argv", argv), self.assertRaises(SystemExit) as cm:
                gl.main()
            self.assertIn("must stay half12", str(cm.exception))
            con = sqlite3.connect(db)
            self.assertEqual(con.execute("SELECT name FROM sqlite_master").fetchall(), [])   # nothing written
            con.close()

    def test_only_the_requested_profile_is_ensured(self):
        self.assertEqual(set(gl.BUILTIN_PROFILES), {gl.HALF12_PROFILE_ID, gl.LEAGUE_PROFILE_ID})
        self.assertEqual(gl.LEAGUE_RULES["pass_td"], 5)
        self.assertEqual({k: v for k, v in gl.LEAGUE_RULES.items() if k != "pass_td"},
                         {k: v for k, v in gl.HALF12_RULES.items() if k != "pass_td"})


if __name__ == "__main__":
    unittest.main()
