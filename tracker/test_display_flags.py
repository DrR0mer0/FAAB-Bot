"""Tests for the display-only "thin window" flag (model/thin_window.py) and the practice report / game-status line
(tracker/injuries.py parse_practice, model/score_week.py), both from 2026-11-12. From the repo root:

    python -m unittest discover -s tracker -v

NFL players' public game data (A.J. Brown, NE 2026) and made-up injury-report rows. Nothing here reads the DB or a league.
"""
import contextlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
for d in ("model", "tracker", "data"):
    sys.path.insert(0, str(REPO_ROOT / d))
import injuries  # noqa: E402
import league_availability  # noqa: E402
import score_week  # noqa: E402
import thin_window as tw  # noqa: E402

NE_2026 = [(2026, w) for w in range(1, 10)]


class TestThinWindow(unittest.TestCase):
    def test_a_j_brown_week_10_played_one_of_the_last_three(self):
        # window = his last 3 games played: weeks 1 (44% before an ankle injury), 6, 7; NE's last 3 games: weeks 7-9
        win = [(2026, 1, "NE", "WR", 3), (2026, 6, "NE", "WR", 3), (2026, 7, "NE", "WR", 3)]
        flag = tw.thin_window("NE", NE_2026, {(2026, 1), (2026, 6), (2026, 7)}, win, 2026)
        self.assertEqual([g["week"] for g in flag["played"]], [7])
        self.assertEqual([g["week"] for g in flag["missed"]], [8, 9])
        self.assertEqual(flag["text"], "played 1 of NE's last 3 games (missed weeks 8-9); the model's 3-game window is "
                                       "weeks 1, 6-7")

    def test_two_of_the_last_three_is_not_thin(self):
        self.assertEqual((tw.THIN_WINDOW_TEAM_GAMES, tw.THIN_WINDOW_MIN_GAMES), (3, 2))
        self.assertIsNone(tw.thin_window("NE", NE_2026, {(2026, 7), (2026, 9)}, [], 2026))

    def test_byes_are_skipped_and_last_season_reached_into(self):
        games = [(2025, 17), (2025, 18), (2026, 1), (2026, 3)]                  # a bye in week 2
        flag = tw.thin_window("NE", games, {(2026, 3)}, [(2025, 17, "NE", "WR", 1)], 2026)
        self.assertEqual([(g["season"], g["week"]) for g in flag["team_games"]], [(2025, 18), (2026, 1), (2026, 3)])
        self.assertEqual(flag["text"], "played 1 of NE's last 3 games (missed 2025 week 18 and week 1); the model's 1-game "
                                       "window is 2025 week 17")

    def test_team_games_come_from_the_regular_season_schedule_before_the_week(self):
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE nfl_games (season INT, week INT, home_team TEXT, away_team TEXT, is_playoffs INT)")
        con.executemany("INSERT INTO nfl_games VALUES (?, ?, ?, ?, ?)",
                        [(2025, 18, "OAK", "NE", 0), (2025, 19, "NE", "KC", 1), (2026, 1, "NE", "BUF", 0),
                         (2026, 2, "MIA", "NE", 0), (2026, 3, "NE", "NYJ", 0), (2024, 5, "NE", "KC", 0)])
        games = tw.team_games(con, 2026, 3)
        self.assertEqual(games["NE"], [(2025, 18), (2026, 1), (2026, 2)])      # no playoffs, nothing from week 3 on
        self.assertEqual(games["LV"], [(2025, 18)])                            # historical code crosswalked

    def test_display_only_marker_and_section_in_both_tables(self):
        flag = {"team": "NE", "team_games": [], "played": [], "missed": [], "window": [],
                "text": "played 1 of NE's last 3 games (missed weeks 8-9); the model's 3-game window is weeks 1, 6-7"}
        feats = {"brown": {"_pos": "WR", "_team": "NE"}, "p2": {"_pos": "WR", "_team": "KC"}}
        ranked = [(("brown", feats["brown"]), 0.9), (("p2", feats["p2"]), 0.4)]
        outs = []
        for thin in ({}, {"brown": flag}):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "2026_week10.json"
                score_week.write_output("m.joblib", None, None, SimpleNamespace(season=2026, week=10), {"brown": "A.J. Brown"},
                                        ranked, list(feats.items()), [0.9, 0.4], 2, 2, 0, [], [], [], [], [], [], [], {},
                                        0, 0, 0, path, [], [], {}, {}, (), None, thin)
                outs.append((json.loads(path.read_text(encoding="utf-8")), path.with_suffix(".md").read_text(encoding="utf-8")))
        (plain, plain_md), (flagged, md) = outs

        def strip(o):
            o = json.loads(json.dumps(o))
            o.pop("generated_at")
            o.pop("thin_window_rule")
            for r in o["top"] + o["scored_pool"]:
                r.pop("thin_window", None)
            return o
        self.assertEqual(strip(plain), strip(flagged))
        self.assertEqual(flagged["thin_window_rule"]["n_flagged"], 1)
        self.assertIn("| 1 | A.J. Brown *(thin window)* | WR | NE | 0.9000 |", md)
        self.assertIn(score_week.THIN_WINDOW_TITLE, md)
        self.assertIn("- A.J. Brown (#1): played 1 of NE's last 3 games (missed weeks 8-9)", md)
        self.assertIn("_He played fewer than 2 of his team's last 3 games", md)
        self.assertNotIn("thin window", plain_md.lower())
        view = {"status": "ok", "by_player": {"brown": {"status": "FA", "waiver_date": None}}, "n_matched": 1, "n_available": 1}
        rows = [dict(r, rank=r["rank"]) for r in flagged["top"]]
        text = league_availability.render_section(view, rows, 25, datetime(2026, 11, 12, 15, tzinfo=timezone.utc))
        self.assertIn("| 1 | A.J. Brown *(thin window)* | WR | NE | 0.9000 | Free agent |", text)
        self.assertIn("_Thin window (", text)
        self.assertIn("- A.J. Brown (#1): played 1 of NE's last 3 games", text)


REPORT = (
    "season,season_type,game_type,team,week,gsis_id,position,full_name,first_name,last_name,report_primary_injury,"
    "report_secondary_injury,report_status,practice_primary_injury,practice_secondary_injury,practice_status\n"
    "2026,REG,REG,NE,10,p1,WR,One,,,,,,Ankle,,Limited Participation in Practice\n"
    "2026,REG,REG,NYG,10,p2,RB,Two,,,Knee,,Questionable,Knee,,Limited Participation in Practice\n"
    "2026,REG,REG,NYG,10,p3,WR,Three,,,Hamstring,,Out,Hamstring,,Did Not Participate In Practice\n"
    "2026,REG,REG,ARI,10,p4,TE,Four,,,,,,Not injury related - resting player,,Full Participation in Practice\n"
    "2026,REG,REG,ARI,10,p5,TE,Five,,,,,,Toe,,Some New Wording\n"
    "2026,REG,REG,ARI,9,p1,WR,One,,,,,Out,Ankle,,Did Not Participate In Practice\n"
).encode()


class TestPracticeReport(unittest.TestCase):
    def test_parse_practice_reads_the_week_and_every_status(self):
        practice, gs = injuries.parse_practice(REPORT, 2026, 10)
        self.assertEqual(practice["p1"], {"status": "Limited", "injury": "Ankle", "game_status": ""})
        self.assertEqual(practice["p2"]["game_status"], "Questionable")
        self.assertEqual(practice["p3"]["status"], "DNP")
        self.assertEqual(practice["p4"]["status"], "Full")
        self.assertEqual(practice["p5"]["status"], "Some New Wording")          # an unknown value is shown as written
        self.assertEqual(gs, {"teams_on_report": ["ARI", "NE", "NYG"], "teams_with_status": ["NYG"], "n_with_status": 2})

    def test_it_reaches_score_week_only_from_the_nflverse_file_the_rule_used(self):
        now = datetime(2026, 11, 12, 15, tzinfo=timezone.utc)
        inj = injuries.resolve(2026, 10, now, 36, None, fetch=lambda s: {"ok": True, "raw_bytes": REPORT,
                                                                          "last_modified": now})
        rec = injuries.designations_record(inj, ["Out", "Doubtful"], "tracker")
        self.assertEqual(set(rec["practice"]), {"p1", "p2", "p3", "p4", "p5"})
        self.assertEqual(rec["excluded"], {"p3": "Out"})                        # the rule itself is unchanged
        # the season's designations so far, for the starter-back flag's 0% fallback (rule v1.1)
        self.assertEqual(rec["season_designations"], {"10": {"p2": "Questionable", "p3": "Out"}, "9": {"p1": "Out"}})
        with contextlib.redirect_stdout(io.StringIO()):                          # its stale-file banner
            stale = injuries.resolve(2026, 10, now, 36, {"p1": "OUT"}, fetch=lambda s: {"ok": True, "raw_bytes": REPORT,
                                     "last_modified": datetime(2026, 11, 1, tzinfo=timezone.utc)})
        self.assertEqual(injuries.designations_record(stale, ["Out"], "tracker")["practice"], {})   # ESPN fallback: none
        self.assertEqual(injuries.skipped_record("x", ["Out"], "t")["practice"], {})

    def test_the_section_lists_ranked_players_without_a_game_status_and_bolds_dnp_and_limited(self):
        practice, gs = injuries.parse_practice(REPORT, 2026, 10)
        top = [{"rank": i, "name": n, "practice": practice[p]} for i, (p, n) in
               enumerate((("p1", "One"), ("p2", "Two"), ("p4", "Four")), start=1)]
        lines = score_week.practice_section(top)
        self.assertEqual(lines[1], score_week.PRACTICE_TITLE)
        self.assertIn("- **One (#1): Limited** (Ankle)", lines)
        self.assertIn("- Four (#3): Full (Not injury related - resting player)", lines)
        self.assertFalse(any("Two" in ln for ln in lines))                       # has a game status: not listed
        self.assertEqual(score_week.practice_section([{"rank": 1, "name": "x"}]), [])

    def test_the_summary_line_tells_a_midweek_run_from_a_sunday_one(self):
        def line(gs, source="nflverse"):
            return score_week.game_status_line({"injury_rule": {"source": source, "game_status": gs}})
        thu = line({"teams_on_report": ["ARI", "NE", "NYG", "WAS"], "teams_with_status": ["NYG"], "n_with_status": 2})
        self.assertEqual(thu, ["- Game-status designations (Out/Doubtful/Questionable) at generation time: 2 player(s) on "
                               "1 of the 4 teams on this week's report (NYG) -- a midweek run: most teams publish game "
                               "statuses on Friday (practice report below)"])
        sun = line({"teams_on_report": ["ARI", "NE", "NYG", "WAS"], "teams_with_status": ["ARI", "NE", "NYG"],
                    "n_with_status": 9})
        self.assertTrue(sun[0].endswith("-- the week's game statuses were out"))
        self.assertEqual(line(None, "espn-fallback"),
                         ["- Game-status designations at generation time: not read (injury source: espn-fallback)"])
        self.assertEqual(score_week.game_status_line({"injury_rule": {"source": "nflverse"}}), [])   # an older file
        self.assertEqual(score_week.game_status_line({}), [])


if __name__ == "__main__":
    unittest.main()
