#!/usr/bin/env python3
"""Tests for --through-week on the stats loader and the labels generator.
From the repo root:

    python -m unittest discover -s data -v

Temp DB (built from _init_schema.sql) and temp CSVs only -- never the real DB.
"""
import contextlib
import csv
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

DATA_DIR = Path(__file__).resolve().parent
REPO_ROOT = DATA_DIR.parent


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


init_schema = load_module("init_history_db_schema", DATA_DIR / "init_history_db_schema.py")
loader = load_module("load_nflverse_into_history_SAFE_v3", DATA_DIR / "load_nflverse_into_history_SAFE_v3.py")
labels = load_module("generate_labels_and_breakouts", REPO_ROOT / "model" / "generate_labels_and_breakouts.py")


def run_main(module, argv):
    with mock.patch.object(sys, "argv", ["prog"] + argv), contextlib.redirect_stdout(io.StringIO()) as out:
        module.main()
    return out.getvalue()


class ThroughWeekCase(unittest.TestCase):
    """2025: weeks 1-3. 2026: weeks 1-3 complete, plus week 4's Thursday game
    (two players) already in the CSV -- the in-progress week."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db = str(self.dir / "t.db")
        run_main(init_schema, ["--db", self.db])
        cols = ["season", "week", "player_id", "team", "opponent_team", "position", "receptions", "receiving_yards"]
        for season, weeks in ((2025, (1, 2, 3)), (2026, (1, 2, 3, 4))):
            with open(self.dir / f"stats_player_week_{season}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                for week in weeks:
                    for pid in ("p1", "p2"):
                        w.writerow(dict(season=season, week=week, player_id=pid, team="KC", opponent_team="BAL",
                                        position="WR", receptions=4, receiving_yards=100 if week == 4 else 40))

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, *extra):
        return run_main(loader, ["--db", self.db, "--folder", str(self.dir), "--seasons", "2025", "2026", *extra])

    def label(self, *extra):
        return run_main(labels, ["--db", self.db, "--seasons", "2025", "2026", *extra])

    def weeks(self, table):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(f"SELECT season, week, COUNT(*) FROM {table} GROUP BY season, week ORDER BY season, week").fetchall()
        finally:
            con.close()

    COMPLETE = [(2025, 1, 2), (2025, 2, 2), (2025, 3, 2), (2026, 1, 2), (2026, 2, 2), (2026, 3, 2)]


class TestLoaderThroughWeek(ThroughWeekCase):
    def test_default_still_loads_every_week_in_the_file(self):
        self.load()
        self.assertEqual(self.weeks("player_week_stats"), self.COMPLETE + [(2026, 4, 2)])

    def test_cap_keeps_the_in_progress_week_out(self):
        out = self.load("--through-week", "3")
        self.assertEqual(self.weeks("player_week_stats"), self.COMPLETE)
        self.assertIn("--through-week 3 -- skipped 2 stat row(s) from later weeks", out)

    def test_cap_applies_to_the_latest_season_only(self):
        self.load("--through-week", "2")
        self.assertEqual(self.weeks("player_week_stats"), self.COMPLETE[:5])   # 2025 keeps all three weeks

    def test_cap_skips_rows_but_never_deletes_ones_already_loaded(self):
        self.load()
        self.load("--through-week", "3")
        self.assertIn((2026, 4, 2), self.weeks("player_week_stats"))


class TestLabelsThroughWeek(ThroughWeekCase):
    def test_default_still_labels_every_loaded_week(self):
        self.load()
        self.label()
        self.assertEqual(self.weeks("labels_player_week"), self.COMPLETE + [(2026, 4, 2)])

    def test_cap_leaves_the_in_progress_week_unlabelled_even_when_its_stats_are_loaded(self):
        self.load()
        out = self.label("--through-week", "3")
        self.assertEqual(self.weeks("labels_player_week"), self.COMPLETE)
        self.assertIn("left out 2 season-2026 stat row(s)", out)

    def test_cap_removes_labels_an_earlier_uncapped_run_wrote(self):
        self.load()
        self.label()
        self.label("--through-week", "3")
        self.assertEqual(self.weeks("labels_player_week"), self.COMPLETE)

    def test_labels_for_completed_weeks_are_identical_with_and_without_the_cap(self):
        self.load()
        self.label()
        con = sqlite3.connect(self.db)
        query = "SELECT * FROM labels_player_week WHERE NOT (season = 2026 AND week > 3) ORDER BY season, week, player_id"
        uncapped = con.execute(query).fetchall()
        con.close()
        self.label("--through-week", "3")
        con = sqlite3.connect(self.db)
        capped = con.execute(query).fetchall()
        con.close()
        self.assertEqual(capped, uncapped)
        self.assertEqual(len(capped), 12)


if __name__ == "__main__":
    unittest.main()
