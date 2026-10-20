#!/usr/bin/env python3
"""Tests for the pre-commit privacy scan. From the repo root:

    python -m unittest discover -s tools -v

A made-up league in a temp DB, a temp bids.csv and a fake league key -- never
the real ones, and no real name appears in this file.
"""
import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import privacy_scan

TEAMS = [("Old Team Name", "Johnny"), ("Somebody’s Gonna Win", "Johnny"),   # team 1, renamed (curly apostrophe)
         ("The Other Side", "Bo / Casimir")]                                     # co-managers, one too short to use
KEY = "470.l.987654"


def diff(*lines, path="docs/notes.md"):
    return (f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,0 +1,{len(lines)} @@\n"
            + "".join(f"+{ln}\n" for ln in lines))


class TestPrivacyScan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.db, self.bids, self.env = d / "league.db", d / "bids.csv", d / ".env"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE lg_teams (team_name TEXT, manager_nickname TEXT)")
        con.executemany("INSERT INTO lg_teams VALUES (?, ?)", TEAMS)
        con.execute("CREATE TABLE ref_players (player_id TEXT, full_name TEXT)")
        con.execute("INSERT INTO ref_players VALUES ('p1', 'Johnny Wideout')")
        con.commit()
        con.close()
        self.bids.write_text("# a comment, Old Team Name\naward_date,player,winning_team,winning_amount,losing_team,losing_amount\n"
                             "2026-10-01,Some Player,old team nam,5,The Other Side,1\n", encoding="utf-8")   # a typo, kept as typed
        self.env.write_text(f"YAHOO_CLIENT_ID=x\nYAHOO_LEAGUE_KEY={KEY}\n", encoding="utf-8")
        self.ident = mock.patch.dict("os.environ", {"USERNAME": "localperson", "COMPUTERNAME": "MYBOX"}, clear=False)
        self.ident.start()
        self.addCleanup(self.ident.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_scan(self, text):
        path = Path(self.tmp.name) / "staged.diff"
        path.write_text(text, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = privacy_scan.main(["--diff-file", str(path), "--db", str(self.db), "--bids", str(self.bids), "--env", str(self.env)])
        return rc, out.getvalue() + err.getvalue()

    def test_old_name_new_name_with_a_plain_apostrophe_and_the_league_key_are_all_caught_and_never_printed(self):
        rc, out = self.run_scan(diff("Recap: Old Team Name won again.", "now called somebody's gonna win", f"key is {KEY} here",
                                     "and the bare id 987654 too"))
        self.assertEqual(rc, 1)
        self.assertIn("docs/notes.md:1: matches a team name (13 characters)", out)
        self.assertIn("docs/notes.md:2: matches a team name (20 characters)", out)
        self.assertIn("docs/notes.md:3: matches a league key", out)
        self.assertIn("docs/notes.md:4: matches a league id", out)
        for secret in ("Old Team Name", "old team name", "Gonna Win", "gonna win", KEY, "987654"):
            self.assertNotIn(secret, out)

    def test_nicknames_bids_typos_co_managers_and_local_identifiers_are_terms(self):
        terms, _ = privacy_scan.build_terms(self.db, self.bids, self.env)
        kinds = {k: v for k, v in terms.items()}
        self.assertEqual(kinds["johnny"], "manager nickname")
        self.assertEqual(kinds["casimir"], "manager nickname")
        self.assertNotIn("bo", kinds)                                    # too short to scan for
        self.assertEqual(kinds["old team nam"], "name typed in bids.csv")
        self.assertEqual((kinds["localperson"], kinds["mybox"]), ("local identifier", "local identifier"))
        self.assertNotIn("some player", kinds)                           # the player column is not a team

    def test_a_nickname_inside_an_nfl_players_name_is_not_a_hit_on_its_own_it_is(self):
        self.assertEqual(self.run_scan(diff("| 12 | Johnny Wideout | WR | KC | 0.41 |", path="predictions/x.md"))[0], 0)
        rc, out = self.run_scan(diff("thanks Johnny for the trade", "| 3 | Johnny Wideout | WR |"))
        self.assertEqual(rc, 1)
        self.assertIn("docs/notes.md:1: matches a manager nickname", out)
        self.assertNotIn("docs/notes.md:2", out)

    def test_word_boundaries_and_line_numbers(self):
        self.assertEqual(self.run_scan(diff("an oldteamname or a mybox2 or 9876543 is not a match"))[0], 0)
        text = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -10,2 +10,3 @@\n context\n-gone\n+fine\n+The Other Side\n"
                "diff --git a/b.py b/b.py\nnew file mode 100644\n--- /dev/null\n+++ b/b.py\n@@ -0,0 +1 @@\n+all clear\n")
        rc, out = self.run_scan(text)
        self.assertEqual(rc, 1)
        self.assertIn("a.py:12: matches a team name", out)
        self.assertIn("1 line(s) name the league", out)

    def test_clean_diff_passes_and_a_missing_league_db_refuses(self):
        rc, out = self.run_scan(diff("nothing to see", "def f(x): return x"))
        self.assertEqual(rc, 0)
        self.assertIn("[PRIVACY] ok: 2 added line(s) checked", out)
        self.db.unlink()
        self.assertEqual(self.run_scan(diff("nothing to see"))[0], 2)

    def test_hook_is_written_once_and_never_overwrites_another(self):
        git = Path(self.tmp.name) / "git"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(privacy_scan.install_hook(git), 0)
            self.assertEqual(privacy_scan.install_hook(git), 0)          # same hook again: fine
            (git / "hooks" / "pre-commit").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
            self.assertEqual(privacy_scan.install_hook(git), 1)
        self.assertIn("echo mine", (git / "hooks" / "pre-commit").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
