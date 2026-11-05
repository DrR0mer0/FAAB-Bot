"""Tests for the display-only "starter back" flag (model/starter_back.py, used by model/score_week.py). From the repo root:

    python -m unittest discover -s tracker -v

Snap shares are NFL players' public game data; the cases are the 2026 ones that prompted the flag (Dameon Pierce and
Saquon Barkley, PHI) plus made-up committees. Nothing here reads the DB or a league.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
for d in ("model", "tracker", "data"):
    sys.path.insert(0, str(REPO_ROOT / d))
import league_availability  # noqa: E402
import score_week  # noqa: E402
import starter_back as sb  # noqa: E402

PIERCE, BARKLEY = "00-0037258", "00-0034844"
# PHI RB offense snap shares, 2026 (tr_snap_counts): Barkley 71/16/72/7% in weeks 1-4, out weeks 5-6, back at 15% in
# week 7; Pierce (practice squad until week 5) 77/95/64% in weeks 5-7.
PHI_ROWS = ([(2026, w, BARKLEY, "RB", "PHI", p) for w, p in ((1, .71), (2, .16), (3, .72), (4, .07), (7, .15))]
            + [(2026, w, PIERCE, "RB", "PHI", p) for w, p in ((5, .77), (6, .95), (7, .64))]
            + [(2026, w, "other-rb", "RB", "PHI", p) for w, p in ((1, .29), (2, .30), (3, .28), (4, .30), (5, .23), (6, .05),
                                                                     (7, .21))])
NAMES = {PIERCE: "Dameon Pierce", BARKLEY: "Saquon Barkley"}


def window(*weeks, team="PHI"):
    """features_lib.trailing_window-style tuples (season, week, team, pos, touches)."""
    return [(s, w, t, "RB", 10) for s, w, t in ((*g, team) if len(g) == 2 else g for g in weeks)]


def flags(pid, pos, team, win, target, rows, available=lambda x: True):
    out = sb.starter_back(pid, pos, team, win, target, sb.SnapIndex(rows), available)
    for f in out:
        f["text"] = sb.describe(f, NAMES.get(pid, pid), NAMES.get(f["teammate_id"], f["teammate_id"]), target[0])
    return out


class TestPierceAndBarkley(unittest.TestCase):
    def test_week_7_barkley_missed_5_and_6_and_is_available_not_back_yet(self):
        # week 7's window reached back to his previous team's game (pool rule v3 ranked it); only the PHI games count
        fl = flags(PIERCE, "RB", "PHI", window((2025, 17, "HOU"), (2026, 5), (2026, 6)), (2026, 7), PHI_ROWS)
        self.assertEqual([f["teammate_id"] for f in fl], [BARKLEY])
        f = fl[0]
        self.assertEqual(f["weeks_missed"], [{"season": 2026, "week": 5}, {"season": 2026, "week": 6}])
        self.assertEqual(f["before_window"]["teammate_share"], 0.415)          # weeks 1-4: 71, 16, 72, 7 -- early exits
        self.assertEqual(f["before_window"]["player_share"], 0.0)
        self.assertEqual(f["returned"], "not back yet")
        self.assertEqual(f["text"], "usage built while Saquon Barkley missed weeks 5-6; Saquon Barkley not back yet, "
                                    "available this week")

    def test_week_8_barkley_back_in_week_7_at_15_percent_pierce_64(self):
        fl = flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), PHI_ROWS)
        self.assertEqual(len(fl), 1)
        self.assertEqual(fl[0]["returned"], {"season": 2026, "week": 7, "teammate_share": 0.15, "player_share": 0.64})
        self.assertEqual(fl[0]["text"], "usage built while Saquon Barkley missed weeks 5-6; Saquon Barkley back in week 7 "
                                        "at 15% of snaps (Dameon Pierce 64%)")

    def test_later_snap_rows_are_never_read(self):
        later = PHI_ROWS + [(2026, 8, BARKLEY, "RB", "PHI", .9), (2026, 8, PIERCE, "RB", "PHI", .1)]
        self.assertEqual(flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), later),
                         flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), PHI_ROWS))

    def test_the_other_back_is_not_flagged_for_barkley_he_trailed_by_less_than_the_lead(self):
        # the third PHI back (29% before the window) also played through weeks 5-7: Barkley's lead over him is 0.125
        self.assertEqual(flags("other-rb", "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), PHI_ROWS), [])


class TestCommittee(unittest.TestCase):
    # a 3-way committee on a made-up team: A 40%, B 35%, P 25% in weeks 1-4
    BASE = ([(2026, w, "A", "RB", "XYZ", .40) for w in (1, 2, 3, 4)] + [(2026, w, "B", "RB", "XYZ", .35) for w in (1, 2, 3, 4)]
            + [(2026, w, "P", "RB", "XYZ", .25) for w in (1, 2, 3, 4)])

    def test_a_teammate_with_a_light_week_is_no_flag(self):
        rows = self.BASE + [(2026, 5, "A", "RB", "XYZ", .40), (2026, 5, "B", "RB", "XYZ", .08), (2026, 5, "P", "RB", "XYZ", .52),
                            (2026, 6, "A", "RB", "XYZ", .41), (2026, 6, "B", "RB", "XYZ", .34), (2026, 6, "P", "RB", "XYZ", .25),
                            (2026, 7, "A", "RB", "XYZ", .39), (2026, 7, "B", "RB", "XYZ", .36), (2026, 7, "P", "RB", "XYZ", .25)]
        self.assertEqual(flags("P", "RB", "XYZ", window((2026, 5), (2026, 6), (2026, 7), team="XYZ"), (2026, 8), rows), [])

    def test_a_committee_members_routine_rest_is_no_flag(self):
        # A sat week 6 (no snap row): he missed a window game, but his lead over P before the window was only 0.15
        rows = self.BASE + [(2026, 5, "A", "RB", "XYZ", .40), (2026, 5, "P", "RB", "XYZ", .25),
                            (2026, 6, "B", "RB", "XYZ", .50), (2026, 6, "P", "RB", "XYZ", .45),
                            (2026, 7, "A", "RB", "XYZ", .40), (2026, 7, "P", "RB", "XYZ", .25)]
        self.assertEqual(flags("P", "RB", "XYZ", window((2026, 5), (2026, 6), (2026, 7), team="XYZ"), (2026, 8), rows), [])
        self.assertLess(0.40 - 0.25, sb.LEAD_MIN)

    def test_the_players_own_absence_before_the_window_is_not_the_teammates_lead(self):
        # two full-time receivers; P missed week 4 himself, X missed week 5 (in P's window). Counting P's week 4 as 0%
        # would give X a 0.23 lead; compared in the games both played (weeks 1-3) it is 0.03 -- no flag
        rows = ([(2026, w, "X", "WR", "XYZ", .90) for w in (1, 2, 3, 4, 6, 7)]
                + [(2026, w, "P", "WR", "XYZ", .87) for w in (1, 2, 3, 5, 6, 7)])
        self.assertEqual(flags("P", "WR", "XYZ", window((2026, 5), (2026, 6), (2026, 7), team="XYZ"), (2026, 8), rows), [])
        # and a receiver who barely played in those games is still behind him
        rows = ([(2026, w, "X", "WR", "XYZ", .90) for w in (1, 2, 3, 4, 6, 7)] + [(2026, 2, "P", "WR", "XYZ", .05)]
                + [(2026, w, "P", "WR", "XYZ", .80) for w in (5, 6, 7)])
        fl = flags("P", "WR", "XYZ", window((2026, 5), (2026, 6), (2026, 7), team="XYZ"), (2026, 8), rows)
        self.assertEqual([(f["teammate_id"], f["before_window"]["compared"]) for f in fl], [("X", "games both played")])

    def test_any_position_qb_included(self):
        rows = ([(2026, w, "starter-qb", "QB", "XYZ", 1.0) for w in (1, 2, 3, 4, 5)]
                + [(2026, w, "backup-qb", "QB", "XYZ", 1.0) for w in (6, 7, 8)])
        fl = flags("backup-qb", "QB", "XYZ", window((2026, 6), (2026, 7), (2026, 8), team="XYZ"), (2026, 9), rows)
        self.assertEqual([(f["teammate_id"], f["returned"]) for f in fl], [("starter-qb", "not back yet")])


class TestAvailability(unittest.TestCase):
    """Condition (c), score_week.starter_back_available: the same sources score_week already resolved."""

    def available(self, status="ACT", team="PHI", yahoo=None, out=(), latest_team=None):
        roster_team_of = {BARKLEY: team} if status in score_week.CONFIRMED_ROSTER_STATUSES else {}
        latest = {BARKLEY: (8, status, latest_team or team), PIERCE: (8, "ACT", "PHI")}
        return score_week.starter_back_available(BARKLEY, "PHI", 8, roster_team_of, {BARKLEY: status}, latest, {"PHI", "KC"},
                                                 {BARKLEY: yahoo} if yahoo is not None else {}, set(out))

    def test_on_the_roster_and_healthy_is_available(self):
        self.assertTrue(self.available())

    def test_a_teammate_still_on_reserve_is_no_flag(self):
        self.assertFalse(self.available("RES", yahoo="IR"))
        self.assertFalse(self.available("RES"))                          # no Yahoo status: the long-term status stands
        fl = flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), PHI_ROWS,
                   available=lambda x: self.available("RES", yahoo="IR"))
        self.assertEqual(fl, [])

    def test_yahoo_may_overrule_a_long_term_status_as_in_pool_rule_v4(self):
        self.assertTrue(self.available("EXE", yahoo=""))                 # Jacobs' week 7: exempt per nflverse, Yahoo clear

    def test_a_teammate_traded_away_is_no_flag(self):
        self.assertFalse(self.available(team="KC"))
        fl = flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), PHI_ROWS,
                   available=lambda x: self.available(team="KC"))
        self.assertEqual(fl, [])

    def test_out_or_doubtful_practice_squad_or_off_roster_is_not_available(self):
        self.assertFalse(self.available(out={BARKLEY}))
        self.assertFalse(self.available("DEV"))
        self.assertFalse(self.available("CUT"))

    def test_without_a_weekly_roster_nothing_is_confirmed(self):
        self.assertFalse(score_week.starter_back_available(BARKLEY, "PHI", 8, {}, {}, {}, set(), {}, set()))


class TestDisplayOnly(unittest.TestCase):
    FLAG = {"teammate_id": BARKLEY, "teammate": "Saquon Barkley", "position": "RB", "team": "PHI",
            "weeks_missed": [{"season": 2026, "week": 5}, {"season": 2026, "week": 6}],
            "before_window": {"games": [], "teammate_share": 0.415, "player_share": 0.0},
            "returned": {"season": 2026, "week": 7, "teammate_share": 0.15, "player_share": 0.64},
            "text": "usage built while Saquon Barkley missed weeks 5-6; Saquon Barkley back in week 7 at 15% of snaps "
                    "(Dameon Pierce 64%)"}

    def write(self, starter_back_of):
        feats = {PIERCE: {"_pos": "RB", "_team": "PHI"}, "p2": {"_pos": "WR", "_team": "KC"}}
        stable = list(feats.items())
        ranked = [((PIERCE, feats[PIERCE]), 0.9), (("p2", feats["p2"]), 0.4)]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "2026_week08.json"
            score_week.write_output("m.joblib", None, None, SimpleNamespace(season=2026, week=8), NAMES, ranked, stable,
                                    [0.9, 0.4], 2, 2, 0, [], [], [], [], [], [], [], {}, 0, 0, 0, path, [], [], {},
                                    starter_back_of)
            return json.loads(path.read_text(encoding="utf-8")), path.with_suffix(".md").read_text(encoding="utf-8")

    def test_the_flag_changes_nothing_else_in_the_output(self):
        plain, plain_md = self.write({})
        flagged, md = self.write({PIERCE: [self.FLAG]})

        def strip(o):
            o = json.loads(json.dumps(o))
            o.pop("generated_at")
            o.pop("starter_back_rule")
            for r in o["top"] + o["scored_pool"]:
                r.pop("starter_back")
            return o
        self.assertEqual(strip(plain), strip(flagged))                   # scores, ranks, pool, counts: identical
        self.assertEqual([r["starter_back"] for r in plain["top"]], [[], []])
        self.assertEqual(flagged["top"][0]["starter_back"], [self.FLAG])
        self.assertEqual(flagged["scored_pool"][0]["starter_back"], [self.FLAG])
        self.assertEqual(flagged["starter_back_rule"], {"lead_min": sb.LEAD_MIN, "pre_window_games": sb.PRE_WINDOW_GAMES,
                                                        "display_only": True, "n_flagged": 1})
        self.assertIn("| 1 | Dameon Pierce *(starter back)* | RB | PHI | 0.9000 |", md)
        self.assertIn("- Dameon Pierce (#1): usage built while Saquon Barkley missed weeks 5-6; Saquon Barkley back in week 7 "
                      "at 15% of snaps (Dameon Pierce 64%)", md)
        self.assertNotIn("starter back", plain_md)

    def test_a_file_without_the_field_renders_as_before(self):
        flagged, _md = self.write({PIERCE: [self.FLAG]})
        old = json.loads(json.dumps(flagged))
        for r in old["top"]:
            r.pop("starter_back")
        old_md = score_week.render_markdown_report(old)
        self.assertNotIn("starter back", old_md)
        self.assertNotIn(score_week.STARTER_BACK_TITLE, old_md)

    def test_the_available_in_my_league_table_gets_the_marker_and_the_words(self):
        view = {"status": "ok", "by_player": {PIERCE: {"status": "W", "waiver_date": "2026-11-05"}, "p2": {"status": "FA",
                "waiver_date": None}}, "n_matched": 2, "n_available": 2}
        from datetime import datetime, timezone
        ranked = [{"rank": 1, "player_id": PIERCE, "name": "Dameon Pierce", "pos": "RB", "team": "PHI", "score": 0.9,
                   "starter_back": [self.FLAG]},
                  {"rank": 2, "player_id": "p2", "name": "Other", "pos": "WR", "team": "KC", "score": 0.4, "starter_back": []}]
        text = league_availability.render_section(view, ranked, 25, datetime(2026, 11, 5, 14, tzinfo=timezone.utc))
        self.assertIn("| 1 | Dameon Pierce *(starter back)* | RB | PHI | 0.9000 |", text)
        self.assertIn("| 2 | Other | WR | KC | 0.4000 |", text)
        self.assertIn("- Dameon Pierce (#1): usage built while Saquon Barkley missed weeks 5-6", text)
        for r in ranked:
            r.pop("starter_back")
        self.assertNotIn("starter back", league_availability.render_section(view, ranked, 25,
                                                                             datetime(2026, 11, 5, 14, tzinfo=timezone.utc)).lower())


if __name__ == "__main__":
    unittest.main()
