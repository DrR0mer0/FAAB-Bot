"""Tests for the display-only "starter back" flag (model/starter_back.py, rule v1.1 from 2026-11-12, used by
model/score_week.py). From the repo root:

    python -m unittest discover -s tracker -v

Snap shares and roster statuses are NFL players' public data (Dameon Pierce and Saquon Barkley, PHI 2026; A.J. Brown and
Mack Hollins, NE 2025-26) plus made-up teams. Nothing here reads the DB or a league.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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
# week 7; Pierce (practice squad, DEV, in weeks 1-4) 77/95/64% in weeks 5-7.
PHI_ROWS = ([(2026, w, BARKLEY, "RB", "PHI", p) for w, p in ((1, .71), (2, .16), (3, .72), (4, .07), (7, .15))]
            + [(2026, w, PIERCE, "RB", "PHI", p) for w, p in ((5, .77), (6, .95), (7, .64))]
            + [(2026, w, "other-rb", "RB", "PHI", p) for w, p in ((1, .29), (2, .30), (3, .28), (4, .30), (5, .23), (6, .05),
                                                                     (7, .21))])
NAMES = {PIERCE: "Dameon Pierce", BARKLEY: "Saquon Barkley"}
# Pierce on PHI's practice squad in weeks 1-4 (nflverse weekly roster 2026: DEV P07); an injury report for 2026 exists
PHI_PRESENCE = sb.Presence(roster={(2026, w, PIERCE): ("PHI", "DEV") for w in (1, 2, 3, 4)}, roster_seasons={2026},
                           injury_seasons={2026})


def window(*weeks, team="PHI"):
    """features_lib.trailing_window-style tuples (season, week, team, pos, touches)."""
    return [(s, w, t, "RB", 10) for s, w, t in ((*g, team) if len(g) == 2 else g for g in weeks)]


def flags(pid, pos, team, win, target, rows, available=lambda x: True, presence=PHI_PRESENCE):
    out = sb.starter_back(pid, pos, team, win, target, sb.SnapIndex(rows), available, presence)
    for f in out:
        f["text"] = sb.describe(f, NAMES.get(pid, pid), NAMES.get(f["teammate_id"], f["teammate_id"]), target[0])
    return out


class TestPierceAndBarkley(unittest.TestCase):
    def test_week_7_pierce_is_flagged_his_practice_squad_weeks_count_as_0_percent(self):
        # week 7's window reached back to his previous team's game (pool rule v3 ranked it); only the PHI games count
        fl = flags(PIERCE, "RB", "PHI", window((2025, 17, "HOU"), (2026, 5), (2026, 6)), (2026, 7), PHI_ROWS)
        self.assertEqual([f["teammate_id"] for f in fl], [BARKLEY])
        f = fl[0]
        self.assertEqual(f["weeks_missed"], [{"season": 2026, "week": 5}, {"season": 2026, "week": 6}])
        self.assertEqual(f["before_window"]["teammate_share"], 0.415)          # weeks 1-4: 71, 16, 72, 7 -- early exits
        self.assertEqual(f["before_window"]["player_share"], 0.0)
        self.assertEqual(f["before_window"]["compared"], "player had no snaps in them")
        self.assertEqual({(g["week"], g["counts"], g["why"]) for g in f["before_window"]["fallback"]},
                         {(w, True, "practice squad") for w in (1, 2, 3, 4)})
        self.assertEqual(f["returned"], "not back yet")
        self.assertEqual(f["text"], "usage built while Saquon Barkley missed weeks 5-6; Saquon Barkley not back yet, "
                                    "available this week")

    def test_week_8_barkley_back_in_week_7_at_15_percent_pierce_64(self):
        fl = flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), PHI_ROWS)
        self.assertEqual(len(fl), 1)
        self.assertEqual(fl[0]["returned"], {"season": 2026, "week": 7, "teammate_share": 0.15, "player_played": True,
                                             "player_share": 0.64})
        self.assertEqual(fl[0]["text"], "usage built while Saquon Barkley missed weeks 5-6; Saquon Barkley back in week 7 "
                                        "at 15% of snaps (Dameon Pierce 64%)")

    def test_a_player_who_did_not_play_the_comparison_week_says_so_not_zero(self):
        # had Pierce sat week 7, the line must not read "Dameon Pierce 0%" (A.J. Brown's week 10 line did)
        rows = [r for r in PHI_ROWS if not (r[1] == 7 and r[2] == PIERCE)]
        fl = flags(PIERCE, "RB", "PHI", window((2026, 4, "HOU"), (2026, 5), (2026, 6)), (2026, 8), rows)
        self.assertFalse(fl[0]["returned"]["player_played"])
        self.assertTrue(fl[0]["text"].endswith("Saquon Barkley back in week 7 at 15% of snaps (Dameon Pierce did not play)"))
        self.assertNotIn("0%)", fl[0]["text"])

    def test_later_snap_rows_are_never_read(self):
        later = PHI_ROWS + [(2026, 8, BARKLEY, "RB", "PHI", .9), (2026, 8, PIERCE, "RB", "PHI", .1)]
        self.assertEqual(flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), later),
                         flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), PHI_ROWS))

    def test_the_other_back_is_not_flagged_for_barkley_he_trailed_by_less_than_the_lead(self):
        # the third PHI back (29% before the window) also played through weeks 5-7: Barkley's lead over him is 0.125
        self.assertEqual(flags("other-rb", "RB", "PHI", window((2026, 5), (2026, 6), (2026, 7)), (2026, 8), PHI_ROWS), [])

    def test_documented_case_rule_v2_said_nothing_without_shared_games_v1_1_flags_pierce(self):
        # rule v2 (2026-11-12, not adopted) needed 2 games both players took snaps in; Pierce and Barkley had none before
        # week 7, so v2 was silent on the very case the flag was built for. v1.1 keeps v1's premise and counts Pierce's
        # practice-squad weeks as 0% -- he is flagged.
        snaps = sb.SnapIndex(PHI_ROWS)
        shared = [g for g in snaps.team_games["PHI"] if snaps.active(PIERCE, g, "PHI") and snaps.active(BARKLEY, g, "PHI")
                  and g < (2026, 7)]
        self.assertEqual(shared, [])
        self.assertEqual(len(flags(PIERCE, "RB", "PHI", window((2026, 5), (2026, 6)), (2026, 7), PHI_ROWS)), 1)


class TestABrownWeek10(unittest.TestCase):
    """2026 week 10, NE: v1 flagged A.J. Brown because Mack Hollins out-snapped "him" in NE's 2025 games -- when Brown
    was on PHI. v1.1 excludes a game he played for another team; with nothing left, no flag."""

    ROWS = ([(2025, w, "hollins", "WR", "NE", p) for w, p in ((12, .80), (13, .75), (15, .79), (16, .66))]
            + [(2025, w, "brown", "WR", "PHI", p) for w, p in ((12, .97), (13, .95), (15, .72), (16, .87))]
            + [(2026, w, "hollins", "WR", "NE", p) for w, p in ((1, .70), (2, .71), (3, .76), (4, .41), (9, .68))]
            + [(2026, w, "brown", "WR", "NE", p) for w, p in ((1, .44), (6, .76), (7, .91))])
    WIN = [(2026, 1, "NE", "WR", 3), (2026, 6, "NE", "WR", 3), (2026, 7, "NE", "WR", 3)]

    def presence(self):
        return sb.Presence(dressed={(2025, w, "brown"): "PHI" for w in (12, 13, 15, 16)},
                           roster={(2025, w, "brown"): ("PHI", "ACT") for w in (12, 13, 15, 16)}, roster_seasons={2025, 2026},
                           injury_seasons={2025, 2026})

    def test_brown_is_not_flagged(self):
        self.assertEqual(sb.starter_back("brown", "WR", "NE", self.WIN, (2026, 10), sb.SnapIndex(self.ROWS), lambda x: True,
                                         self.presence()), [])
        self.assertEqual(self.presence().fallback("brown", (2025, 12), "NE"), (False, "played for another team"))

    def test_without_any_presence_data_the_fallback_never_counts(self):
        self.assertEqual(sb.starter_back("brown", "WR", "NE", self.WIN, (2026, 10), sb.SnapIndex(self.ROWS), lambda x: True),
                         [])


class TestFallbackCounts(unittest.TestCase):
    """Each way a game the player sat out counts as 0%: he was with the team, absent for a depth-chart reason."""

    def p(self, **kw):
        base = dict(roster_seasons={2026}, injury_seasons={2026})
        return sb.Presence(**{**base, **kw})

    def test_dressed_with_zero_offense_snaps(self):
        self.assertEqual(self.p(dressed={(2026, 3, "p"): "XYZ"}).fallback("p", (2026, 3), "XYZ"),
                         (True, "dressed, 0 offense snaps"))

    def test_practice_squad_any_sub_code(self):
        self.assertEqual(self.p(roster={(2026, 3, "p"): ("XYZ", "DEV")}).fallback("p", (2026, 3), "XYZ"), (True, "practice squad"))

    def test_healthy_inactive(self):
        self.assertEqual(self.p(roster={(2026, 3, "p"): ("XYZ", "INA")}).fallback("p", (2026, 3), "XYZ"), (True, "healthy inactive"))

    def test_healthy_backup_active_without_a_snap_row(self):
        self.assertEqual(self.p(roster={(2026, 3, "p"): ("XYZ", "ACT")}).fallback("p", (2026, 3), "XYZ"),
                         (True, "healthy backup (active, no snaps)"))


class TestFallbackExclusions(unittest.TestCase):
    """Each way a game is left out: he wasn't with the team, was out injured, or the data can't tell."""

    def p(self, **kw):
        base = dict(roster_seasons={2026}, injury_seasons={2026})
        return sb.Presence(**{**base, **kw})

    def assert_out(self, presence, why, game=(2026, 3)):
        counts, got = presence.fallback("p", game, "XYZ")
        self.assertFalse(counts)
        self.assertEqual(got, why)

    def test_played_for_another_team(self):
        self.assert_out(self.p(dressed={(2026, 3, "p"): "PHI"}), "played for another team")
        self.assert_out(self.p(stat_team={(2026, 3, "p"): "PHI"}), "played for another team")

    def test_not_in_the_league_yet(self):
        self.assert_out(self.p(rookie_year={"p": 2026}, roster_seasons={2025, 2026}), "not in the league yet", game=(2025, 15))

    def test_no_weekly_roster_row_that_week(self):
        self.assert_out(self.p(), "not on a roster that week")

    def test_on_another_team_per_the_weekly_roster(self):
        self.assert_out(self.p(roster={(2026, 3, "p"): ("ATL", "ACT")}), "on ATL that week")

    def test_cut_or_retired(self):
        self.assert_out(self.p(roster={(2026, 3, "p"): ("XYZ", "CUT")}), "weekly roster CUT")
        self.assert_out(self.p(roster={(2026, 3, "p"): ("XYZ", "RET")}), "weekly roster RET")

    def test_reserve_ir(self):
        self.assert_out(self.p(roster={(2026, 3, "p"): ("XYZ", "RES")}), "weekly roster RES")

    def test_exempt_list_and_other_statuses(self):
        for status in ("EXE", "E14", "E01", "PUP", "SUS"):
            self.assert_out(self.p(roster={(2026, 3, "p"): ("XYZ", status)}), f"weekly roster {status}")

    def test_inactive_with_an_injury_designation(self):
        for d in ("Out", "Doubtful", "Questionable"):
            self.assert_out(self.p(roster={(2026, 3, "p"): ("XYZ", "INA")}, designation={(2026, 3, "p"): d}),
                            f"inactive, injured ({d})")

    def test_active_without_snaps_but_designated(self):
        self.assert_out(self.p(roster={(2026, 3, "p"): ("XYZ", "ACT")}, designation={(2026, 3, "p"): "Out"}),
                        "active, no snaps, injured (Out)")

    def test_an_unknown_report_value_is_undetermined(self):
        self.assert_out(self.p(roster={(2026, 3, "p"): ("XYZ", "INA")}, designation={(2026, 3, "p"): "Note"}),
                        "inactive, undetermined (report: Note)")

    def test_undetermined_without_a_weekly_roster_or_injury_report_for_the_season(self):
        self.assert_out(self.p(roster_seasons=set()), "undetermined: no weekly roster for that season")
        self.assert_out(self.p(roster={(2026, 3, "p"): ("XYZ", "INA")}, injury_seasons=set()),
                        "inactive, undetermined: no injury report for that season")

    def test_no_counted_comparison_game_left_means_no_flag(self):
        # backup QB never took a snap alongside the starter (weeks 1-5); he was on IR then: nothing counts, no flag
        rows = ([(2026, w, "starter-qb", "QB", "XYZ", 1.0) for w in (1, 2, 3, 4, 5)]
                + [(2026, w, "backup-qb", "QB", "XYZ", 1.0) for w in (6, 7, 8)])
        ir = self.p(roster={(2026, w, "backup-qb"): ("XYZ", "RES") for w in range(1, 6)})
        win = window((2026, 6), (2026, 7), (2026, 8), team="XYZ")
        self.assertEqual(flags("backup-qb", "QB", "XYZ", win, (2026, 9), rows, presence=ir), [])
        # the same weeks as a healthy backup (active, no snaps): flagged, any position, QB included
        healthy = self.p(roster={(2026, w, "backup-qb"): ("XYZ", "ACT") for w in range(1, 6)})
        fl = flags("backup-qb", "QB", "XYZ", win, (2026, 9), rows, presence=healthy)
        self.assertEqual([(f["teammate_id"], f["returned"]) for f in fl], [("starter-qb", "not back yet")])

    def test_only_the_counted_games_form_the_comparison(self):
        # X's last 4 games before the window: P was injured in two (RES) and a healthy inactive in two. X's share is
        # averaged over the two that count only.
        rows = ([(2026, w, "X", "RB", "XYZ", p) for w, p in ((1, .30), (2, .30), (3, .80), (4, .80))]
                + [(2026, w, "P", "RB", "XYZ", .60) for w in (5, 6, 7)])
        pres = self.p(roster={(2026, 1, "P"): ("XYZ", "RES"), (2026, 2, "P"): ("XYZ", "RES"),
                              (2026, 3, "P"): ("XYZ", "INA"), (2026, 4, "P"): ("XYZ", "INA")})
        fl = flags("P", "RB", "XYZ", window((2026, 5), (2026, 6), (2026, 7), team="XYZ"), (2026, 8), rows, presence=pres)
        self.assertEqual(fl[0]["before_window"]["teammate_share"], 0.8)
        self.assertEqual([g["week"] for g in fl[0]["before_window"]["games"]], [3, 4])
        self.assertEqual([(g["week"], g["counts"]) for g in fl[0]["before_window"]["fallback"]],
                         [(1, False), (2, False), (3, True), (4, True)])


class TestPresenceFiles(unittest.TestCase):
    ROSTER = ("season,team,position,status,gsis_id,week,game_type,status_description_abbr,rookie_year\n"
              "2025,NE,WR,DEV,p,3,REG,P01,2024\n2025,NE,WR,INA,p,4,REG,A01,2024\n2025,NE,WR,ACT,p,20,WC,A01,2024\n"
              "2025,NE,WR,ACT,,5,REG,A01,2024\n")
    INJ_2021_STYLE = ("season,game_type,team,week,gsis_id,position,full_name,first_name,last_name,report_primary_injury,"
                      "report_secondary_injury,report_status,practice_primary_injury,practice_secondary_injury,"
                      "practice_status,date_modified\n"
                      "2021,REG,NE,4,p,WR,P,,,Knee,,Out,Knee,,Did Not Participate In Practice,2021-10-01\n"
                      "2021,REG,NE,5,p,WR,P,,,,,,,,Full Participation in Practice,2021-10-08\n"
                      "2021,WC,NE,19,p,WR,P,,,Knee,,Out,,,,2022-01-14\n")

    def test_loaders_read_regular_season_rows_and_both_file_layouts(self):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d) / "roster_weekly_2025.csv"
            r.write_text(self.ROSTER, encoding="utf-8")
            i = Path(d) / "injuries_2021.csv"
            i.write_text(self.INJ_2021_STYLE, encoding="utf-8")
            pres = sb.Presence().add_roster_csv(r, season=2025).add_injury_csv(i, season=2021)
            pres.add_roster_csv(Path(d) / "missing.csv").add_injury_csv(Path(d) / "missing.csv")   # nothing, no error
        self.assertEqual(pres.roster, {(2025, 3, "p"): ("NE", "DEV"), (2025, 4, "p"): ("NE", "INA")})
        self.assertEqual((pres.roster_seasons, pres.rookie_year), ({2025}, {"p": 2024}))
        self.assertEqual((pres.designation, pres.injury_seasons), ({(2021, 4, "p"): "Out"}, {2021}))

    def test_the_current_seasons_designations_come_from_the_run(self):
        pres = sb.Presence().add_designations(2026, {"3": {"p": "Questionable"}})
        self.assertEqual((pres.designation, pres.injury_seasons), ({(2026, 3, "p"): "Questionable"}, {2026}))


class TestMissingSeasonFiles(unittest.TestCase):
    """A prior season's weekly roster or injury report missing from nflverse_raw/ (wiped folder, new machine, a season
    rollover): it is downloaded, or the run stops naming it -- never a quiet "undeterminable" that drops flags."""

    ROSTER = b"season,team,position,status,gsis_id,week,game_type,status_description_abbr,rookie_year\n2025,NE,WR,DEV,p,3,REG,P01,2024\n"
    INJURY = b"season,game_type,team,week,gsis_id,report_status\n2025,REG,NE,4,p,Out\n"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def fetcher(self, fail=()):
        self.urls = []

        def fetch(url):
            self.urls.append(url)
            if any(f in url for f in fail):
                raise ConnectionError("no network")
            return self.ROSTER if "roster_weekly" in url else self.INJURY
        return fetch

    def write_all(self, seasons):
        for s in seasons:
            (self.dir / f"roster_weekly_{s}.csv").write_bytes(self.ROSTER)
            (self.dir / f"injuries_{s}.csv").write_bytes(self.INJURY)

    def test_all_present_downloads_nothing(self):
        self.write_all((2024, 2025))
        self.assertEqual(sb.ensure_season_files(self.dir, (2024, 2025), fetch=self.fetcher()), [])
        self.assertEqual(self.urls, [])

    def test_a_missing_file_is_downloaded_from_the_same_nflverse_release(self):
        self.write_all((2024,))
        (self.dir / "roster_weekly_2025.csv").write_bytes(self.ROSTER)
        got = sb.ensure_season_files(self.dir, (2024, 2025), fetch=self.fetcher())
        self.assertEqual(self.urls, ["https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_2025.csv"])
        self.assertEqual([p.name for p in got], ["injuries_2025.csv"])
        self.assertEqual((self.dir / "injuries_2025.csv").read_bytes(), self.INJURY)

    def test_the_2027_rollover_fetches_2026s_injury_report(self):
        # in 2027 the prior seasons are 2025 and 2026; 2026's roster file is there from the weekly refresh, its injury
        # report never was (the runs keep theirs under raw/injuries/)
        self.write_all((2025,))
        (self.dir / "roster_weekly_2026.csv").write_bytes(self.ROSTER)
        sb.ensure_season_files(self.dir, (2025, 2026), fetch=self.fetcher())
        self.assertEqual(self.urls, ["https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_2026.csv"])

    def test_a_failed_download_stops_with_every_missing_file_named(self):
        with self.assertRaises(sb.MissingPresenceData) as cm:
            sb.ensure_season_files(self.dir, (2024, 2025), fetch=self.fetcher(fail=("2025",)))
        msg = str(cm.exception)
        self.assertIn("roster_weekly_2025.csv (ConnectionError: no network)", msg)
        self.assertIn("injuries_2025.csv (ConnectionError: no network)", msg)
        self.assertNotIn("2024.csv (", msg)                                   # those two downloaded fine
        self.assertFalse((self.dir / "roster_weekly_2025.csv").exists())
        self.assertEqual(list(self.dir.glob("*.part")), [])                    # nothing half-written left behind

    def test_a_reply_that_is_not_an_nflverse_csv_is_a_failure_not_a_file(self):
        with self.assertRaises(sb.MissingPresenceData) as cm:
            sb.ensure_season_files(self.dir, (2025,), fetch=lambda url: b"<html>rate limited</html>")
        self.assertIn("not an nflverse CSV", str(cm.exception))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_the_live_run_builds_its_presence_through_the_check(self):
        import sqlite3
        con = sqlite3.connect(":memory:")                                      # no tables: the DB sources say nothing
        rule = {"source": "nflverse", "season_designations": {"3": {"p": "Out"}}}
        with self.assertRaises(sb.MissingPresenceData):
            score_week.starter_back_presence(con, 2026, None, rule, raw_dir=self.dir, fetch=self.fetcher(fail=("2024",)))
        presence, notes = score_week.starter_back_presence(con, 2026, None, rule, raw_dir=self.dir, fetch=self.fetcher())
        self.assertEqual({2025}, presence.roster_seasons)                     # the fixture rows are 2025's
        self.assertIn(2026, presence.injury_seasons)
        self.assertTrue(any(n.startswith("downloaded roster_weekly_2024.csv") for n in notes))
        self.assertTrue(any("no 2026 weekly roster" in n for n in notes))
        _p, notes = score_week.starter_back_presence(con, 2026, None, {"source": "espn-fallback", "season_designations": None},
                                                     raw_dir=self.dir, fetch=self.fetcher())
        self.assertTrue(any("no 2026 injury designations (injury source: espn-fallback)" in n for n in notes))

    def test_the_markdown_says_what_data_the_run_lacked(self):
        out = {"season": 2026, "week": 10, "counts": dict(total_eligible=1, after_position_filter=1, stable_scored=0,
                                                          team_changed_suppressed=0, new_competitor_suppressed=0,
                                                          new_competitor_via_production_only=0,
                                                          new_competitor_via_draft_only=0, new_competitor_via_both=0),
               "top": [], "starter_back_rule": {"data_notes": ["no 2026 injury designations (injury source: skipped)"]}}
        self.assertIn("_Starter back, data this run lacked: no 2026 injury designations (injury source: skipped)._",
                      score_week.render_markdown_report(out))


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
            "returned": {"season": 2026, "week": 7, "teammate_share": 0.15, "player_played": True, "player_share": 0.64},
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
        self.assertEqual(flagged["starter_back_rule"], {"rule": "starter-back-v1.1", "lead_min": sb.LEAD_MIN,
                                                        "pre_window_games": sb.PRE_WINDOW_GAMES, "display_only": True,
                                                        "n_flagged": 1})
        self.assertIn("| 1 | Dameon Pierce *(starter back)* | RB | PHI | 0.9000 |", md)
        self.assertIn("- Dameon Pierce (#1): usage built while Saquon Barkley missed weeks 5-6; Saquon Barkley back in week 7 "
                      "at 15% of snaps (Dameon Pierce 64%)", md)
        self.assertIn("A game he sat out himself counts as 0% only if he was on the team's practice squad, a healthy "
                      "inactive or a healthy backup that week.", md)
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
