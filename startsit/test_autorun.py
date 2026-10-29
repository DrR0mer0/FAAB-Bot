"""Tests for startsit/autorun.py: run times from the real 2026 schedule, the lineup comparison, MISSED handling,
the end-of-season / no-matchup skip and the task definitions. No network, no Task Scheduler, no real DB."""
import argparse
import gzip
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import autorun as ar
import lineup_sim
import scheduled_run as sr

HERE = Path(__file__).resolve().parent
PT = ZoneInfo("America/Los_Angeles")      # the PC's zone; passed explicitly so the tests don't depend on the machine
SCHEDULE = ar.parse_games_csv((HERE / "fixtures" / "schedule_2026_weeks_9_12_15_16.csv").read_text(encoding="utf-8"), 2026)
EXAMPLE = json.loads((HERE / "example_week.json").read_text(encoding="utf-8"))


def utc(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def plan(my_teams, week, matchups=None, end_week=17, now=None, horizon=None):
    games = [g for g in SCHEDULE if g[0] == week]
    now = now or games[0][1] - timedelta(days=3)
    entries, skipped = ar.compute_plan(games, my_teams, matchups if matchups is not None else {week: "ok"}, end_week, now,
                                       "fixture", horizon=horizon, tz=PT, season=2026)
    return entries, skipped


def times(entries):
    return [(e["kind"], e["run_at_local"], e["lock_local"]) for e in entries]


class TestRunTimesFromTheRealSchedule(unittest.TestCase):
    """Made-up rosters (team codes only) against the real 2026 schedule, in Pacific time."""

    def test_week_9_thursday_preliminary_the_9_30_et_game_and_only_my_kickoffs(self):
        my = {"ATL": ["London Back"], "BAL": ["Thursday Back"], "SF": ["Late Receiver"], "NE": ["Late Quarterback"]}
        self.assertEqual(times(plan(my, 9)[0]), [
            ("preliminary", "2026-11-05T06:50", "2026-11-05T17:15"),   # first game day, after the 06:35 snapshot
            ("prelock", "2026-11-05T16:15", "2026-11-05T17:15"),       # TNF JAX@BAL, 60 minutes before
            ("prelock", "2026-11-08T05:30", "2026-11-08T06:30"),       # CIN@ATL at 9:30 ET = 6:30 PT
            ("prelock", "2026-11-08T12:05", "2026-11-08T13:05"),       # LV@SF, 1:05 PT
            ("prelock", "2026-11-08T12:25", "2026-11-08T13:25")])      # GB@NE, 1:25 PT -- its own run, after its inactives
        # opponent-only kickoffs (the 10:00 slate, SNF, MNF) get none
        entries, _ = plan(my, 9)
        self.assertEqual(entries[2]["players"], ["London Back"])
        self.assertEqual(entries[2]["key"], "2026w09-prelock-20261108T1430Z")

    def test_week_12_wednesday_preliminary_and_the_wednesday_thursday_friday_games(self):
        my = {"LA": ["Wednesday Receiver"], "DET": ["Thanksgiving Back"], "PIT": ["Friday Receiver"], "JAX": ["Sunday End"]}
        self.assertEqual(times(plan(my, 12)[0]), [
            ("preliminary", "2026-11-25T06:50", "2026-11-25T17:00"),   # the week's first game day is a Wednesday
            ("prelock", "2026-11-25T16:00", "2026-11-25T17:00"),       # GB@LA, Wednesday 8:00 PM ET
            ("prelock", "2026-11-26T09:00", "2026-11-26T10:00"),       # CHI@DET, Thanksgiving 1:00 PM ET
            ("prelock", "2026-11-27T11:00", "2026-11-27T12:00"),       # DEN@PIT, Friday 3:00 PM ET
            ("prelock", "2026-11-29T12:05", "2026-11-29T13:05")])      # TEN@JAX

    def test_week_15_saturday_games(self):
        my = {"PHI": ["Saturday Back"], "BUF": ["Saturday Night Receiver"], "KC": ["Monday End"]}
        self.assertEqual(times(plan(my, 15)[0]), [
            ("preliminary", "2026-12-17T06:50", "2026-12-17T17:15"),
            ("prelock", "2026-12-19T13:00", "2026-12-19T14:00"),       # SEA@PHI, Saturday 5:00 PM ET
            ("prelock", "2026-12-19T16:20", "2026-12-19T17:20"),       # CHI@BUF, Saturday 8:20 PM ET
            ("prelock", "2026-12-21T16:15", "2026-12-21T17:15")])      # NE@KC, Monday

    def test_week_16_thursday_and_christmas_friday(self):
        my = {"HOU": ["Thursday Receiver"], "CHI": ["Christmas Back"], "SEA": ["Christmas Night End"]}
        self.assertEqual(times(plan(my, 16)[0]), [
            ("preliminary", "2026-12-24T06:50", "2026-12-24T17:15"),
            ("prelock", "2026-12-24T16:15", "2026-12-24T17:15"),       # HOU@PHI
            ("prelock", "2026-12-25T09:00", "2026-12-25T10:00"),       # GB@CHI, Christmas 1:00 PM ET
            ("prelock", "2026-12-25T16:15", "2026-12-25T17:15")])      # LA@SEA, Christmas 8:15 PM ET

    def test_one_run_per_kickoff_however_many_of_my_players_share_it(self):
        entries, _ = plan({"KC": ["A"], "WAS": ["B"], "NYJ": ["C"]}, 9)    # all three 10:00 PT games
        prelocks = [e for e in entries if e["kind"] == "prelock"]
        self.assertEqual(len(prelocks), 1)
        self.assertEqual((prelocks[0]["players"], sorted(prelocks[0]["games"])), (["A", "B", "C"], ["LA@WAS", "NYJ@KC"]))

    def test_only_future_runs_inside_the_horizon(self):
        my = {"BAL": ["Thursday Back"], "ATL": ["London Back"]}
        games = [g for g in SCHEDULE if g[0] == 9]
        now = utc("2026-11-06T00:30:00")                                   # Thursday 16:30 PT: prelim and TNF run passed
        entries, _ = ar.compute_plan(games, my, {9: "ok"}, 17, now, "fixture", horizon=timedelta(days=8), tz=PT, season=2026)
        self.assertEqual([e["run_at_local"] for e in entries], ["2026-11-08T05:30"])
        entries, _ = ar.compute_plan(games, my, {9: "ok"}, 17, now, "fixture", horizon=timedelta(hours=12), tz=PT, season=2026)
        self.assertEqual(entries, [])

    def test_a_preliminary_that_would_come_too_late_is_left_out(self):
        early = [(9, utc("2026-11-08T14:00:00"), "ATL", "CIN")]              # a first game at 06:00 PT
        entries, _ = ar.compute_plan(early, {"ATL": ["x"]}, {9: "ok"}, 17, utc("2026-11-01T00:00:00"), "f", None, PT, 2026)
        self.assertEqual([e["kind"] for e in entries], ["prelock"])


class TestEndOfSeasonAndNoMatchup(unittest.TestCase):
    MY = {"PHI": ["Saturday Back"]}

    def test_no_runs_after_the_league_end_week(self):
        entries, skipped = plan(self.MY, 15, end_week=14)
        self.assertEqual((entries, skipped), ([], {15: "after the league's end_week (14)"}))

    def test_no_runs_in_a_bye_an_elimination_or_a_week_not_on_record(self):
        for status, why in (("no matchup", "no matchup -- no runs"), ("eliminated", "eliminated -- no runs")):
            self.assertEqual(plan(self.MY, 15, matchups={15: status}), ([], {15: why}))
        entries, skipped = plan(self.MY, 15, matchups={})
        self.assertEqual(entries, [])
        self.assertIn("isn't on Yahoo's scoreboard yet", skipped[15])

    def test_the_table_plans_a_pending_week_but_marks_it(self):
        games = [g for g in SCHEDULE if g[0] == 15]
        entries, _ = ar.compute_plan(games, self.MY, {}, 17, utc("2026-12-01T00:00:00"), "f", None, PT, 2026, assume_pending=True)
        self.assertTrue(entries and all(e["matchup_assumed"] for e in entries))

    def league_db(self):
        con = sqlite3.connect(":memory:")
        con.executescript("""
          CREATE TABLE lg_snapshots (snapshot_id INTEGER PRIMARY KEY, taken_at TEXT, resource TEXT, ok INTEGER, raw_file TEXT);
          CREATE TABLE lg_matchups (snapshot_id INTEGER, week INTEGER, team_id INTEGER, is_consolation INTEGER);
          CREATE TABLE lg_rosters (snapshot_id INTEGER, team_id INTEGER, external_id TEXT, name TEXT, team TEXT, player_id TEXT,
                                   selected_position TEXT);
          INSERT INTO lg_snapshots VALUES (1, '2026-12-15T13:35:00+00:00', 'scoreboard', 1, NULL);
          INSERT INTO lg_snapshots VALUES (2, '2026-12-15T13:35:00+00:00', 'rosters', 1, NULL);
          INSERT INTO lg_matchups VALUES (1, 14, 9, 0), (1, 14, 3, 0), (1, 15, 3, 0), (1, 15, 4, 0), (1, 15, 5, 1);
          INSERT INTO lg_rosters VALUES (2, 9, 'y1', 'Starter', 'PHI', NULL, 'RB'), (2, 9, 'y2', 'Benched', 'LAR', NULL, 'BN'),
                                        (2, 9, 'y3', 'Hurt', 'KC', NULL, 'IR'), (2, 9, 'y4', 'Inactive', 'NE', NULL, 'NA'),
                                        (2, 4, 'y5', 'Someone else', 'DAL', NULL, 'WR');
        """)
        return con

    def test_matchups_and_the_playing_roster_come_from_the_newest_snapshots(self):
        con = self.league_db()
        self.assertEqual(ar.matchup_status(con, 9), {14: "ok", 15: "no matchup"})    # a playoff bye
        self.assertEqual(ar.matchup_status(con, 5)[15], "eliminated")                 # consolation bracket
        teams, taken = ar.my_playing_roster(con, 9, 2026, 15, "missing.csv")
        self.assertEqual(teams, {"PHI": ["Starter"], "LA": ["Benched"]})              # starters and bench; not IR / NA

    def test_end_week_is_read_from_the_settings_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            body = json.dumps({"fantasy_content": {"league": [{"start_week": "1", "end_week": "16"}, {}]}})
            (Path(d) / "s.json.gz").write_bytes(gzip.compress(json.dumps({"pages": [{"body": body}]}).encode()))
            con = self.league_db()
            con.execute("INSERT INTO lg_snapshots VALUES (3, '2026-12-15T13:35:00+00:00', 'settings', 1, 's.json.gz')")
            self.assertEqual(ar.league_end_week(con, d), (16, "Yahoo league settings"))
            self.assertEqual(ar.league_end_week(self.league_db(), d)[0], 17)          # no settings snapshot: the default


class TestScheduleSource(unittest.TestCase):
    CSV = "season,game_type,week,gameday,gametime,away_team,home_team\n2026,REG,9,2026-11-05,20:15,JAX,BAL\n"

    def test_a_download_is_used_and_cached_else_the_newest_file_else_the_db(self):
        now = utc("2026-11-03T14:45:00")
        with tempfile.TemporaryDirectory() as d:
            cache, nfl = Path(d) / "cache.csv", Path(d) / "games.csv"
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE nfl_games (season INTEGER, week INTEGER, kickoff_utc TEXT, home_team TEXT, away_team TEXT, "
                        "is_playoffs INTEGER)")
            con.execute("INSERT INTO nfl_games VALUES (2026, 9, '2026-11-05T20:15:00', 'BAL', 'JAX', 0)")
            games, src = ar.load_schedule(2026, con, now, download=lambda url: self.CSV.encode(), cache=cache, nflverse_file=nfl)
            self.assertEqual((games[0][0], games[0][1]), (9, utc("2026-11-06T01:15:00")))
            self.assertTrue(src.startswith("nflverse schedule, downloaded") and cache.exists())

            def fail(url):
                raise OSError("offline")
            games, src = ar.load_schedule(2026, con, now, download=fail, cache=cache, nflverse_file=nfl)
            self.assertIn("cache.csv on disk", src)
            self.assertIn("download failed", src)
            cache.unlink()
            games, src = ar.load_schedule(2026, con, now, download=fail, cache=cache, nflverse_file=nfl)
            self.assertTrue(src.startswith("nfl_games in the DB") and games)


class TestComparingWithTheYahooLineup(unittest.TestCase):
    """The alert fires only when the recommendation differs from the current Yahoo lineup."""

    @classmethod
    def setUpClass(cls):
        cls.report = lineup_sim.run_week(EXAMPLE, n_sims=4000, seed=7)
        cls.rec = [s["player"]["id"] for s in cls.report["lineup"] if s.get("player")]

    def yahoo(self, ids):
        names = {p["id"]: p["name"] for p in EXAMPLE["my_team"]["players"]}
        return {i: (names[i], "X") for i in ids}

    def test_no_alert_when_yahoo_already_starts_the_recommended_lineup(self):
        self.assertIsNone(ar.compare(EXAMPLE, self.report, self.yahoo(self.rec)))

    def test_a_difference_says_what_to_swap_and_how_strong_the_case_is(self):
        bench = next(p["id"] for p in EXAMPLE["my_team"]["players"] if p["id"] not in self.rec and p["pos"] == "WR")
        swapped_out = next(i for i in self.rec if next(p for p in EXAMPLE["my_team"]["players"] if p["id"] == i)["pos"] == "WR")
        current = [bench if i == swapped_out else i for i in self.rec]
        diff = ar.compare(EXAMPLE, self.report, self.yahoo(current))
        names = {p["id"]: p["name"] for p in EXAMPLE["my_team"]["players"]}
        self.assertEqual((diff["start"], diff["bench"]), ([names[swapped_out]], [names[bench]]))
        self.assertTrue(diff["text"].startswith(f"Start {names[swapped_out]} over {names[bench]} -- "))
        self.assertIn(diff["margin"]["label"], ("coin flip", "lean", "clear"))
        self.assertGreaterEqual(diff["margin"]["delta"], 0)

    def test_the_margin_uses_lineup_sims_own_draws(self):
        m = ar.lineup_margin(EXAMPLE, self.rec, self.rec, 4000, 7)
        self.assertEqual(m["best"], self.report["win_prob"])                 # the very simulation the report came from
        self.assertEqual((m["delta"], m["label"]), (0.0, "coin flip"))

    def test_a_yahoo_starter_with_no_projection_still_alerts_with_strength_unknown(self):
        current = dict(self.yahoo(self.rec[1:]), ghost=("Dropped Player", "WR"))
        diff = ar.compare(EXAMPLE, self.report, current)
        self.assertIsNone(diff["margin"])
        self.assertIn("strength unknown: Dropped Player", diff["text"])


class TestPreliminaryAlert(unittest.TestCase):
    """The preliminary run alerts only when a current Yahoo starter can't play at all."""

    def week(self, statuses):
        wf = json.loads(json.dumps(EXAMPLE))
        for p in wf["my_team"]["players"]:
            p["yahoo_slot"] = "BN" if p["id"] in ("tate", "concepcion", "jacobs", "dobbins", "etienne") else "X"
            if p["id"] in statuses:
                p["status"] = statuses[p["id"]]      # the example's benched Jacobs keeps his EXEMPT
        return wf

    def test_who_cannot_play(self):
        wf = self.week({"henry": "BYE", "mccaffrey": "O", "maye": "Q", "washington": "D", "jacobs": "IR"})
        self.assertEqual(ar.cant_play(wf), ["C. McCaffrey (O)", "D. Henry (BYE)"])      # Q / D may play; Jacobs is benched
        self.assertEqual(ar.cant_play(self.week({})), [])

    def run_prelim(self, kind, statuses, same=True):
        wf = self.week(statuses)
        report = lineup_sim.run_week(wf, n_sims=2000, seed=7)
        e = {"key": "k", "kind": kind, "season": 2026, "week": 9, "run_at_local": "2026-11-05T06:50",
             "lock_local": "2026-11-05T17:15", "games": ["JAX@BAL"], "players": []}
        with tempfile.TemporaryDirectory() as d:
            stem = ar.output_stem(e, d)
            stem.parent.mkdir(parents=True, exist_ok=True)

            def fake(argv):
                Path(f"{stem}.json").write_text(json.dumps(wf), encoding="utf-8")
                Path(f"{stem}_report.json").write_text(json.dumps({k: v for k, v in report.items() if k != "report_md"}), encoding="utf-8")
                return 0, "built"
            starters = {s["player"]["id"]: (s["player"]["name"], "X") for s in report["lineup"] if s.get("player")}
            if not same:
                starters = {p["id"]: (p["name"], "X") for p in wf["my_team"]["players"] if p["yahoo_slot"] != "BN"}
            wf.setdefault("_meta", {})["team_id"] = 9
            with mock.patch.object(ar, "current_yahoo_starters", return_value=starters), \
                    mock.patch.object(ar.bw, "connect_ro", return_value=mock.Mock()):
                return ar.run_entry(e, utc("2026-11-05T14:50:00"), "db", "raw", "lraw", d, run=fake, take_snapshot=False)

    def test_a_preliminary_alerts_only_when_a_starter_cannot_play(self):
        rc, summary, _t, alert = self.run_prelim("preliminary", {})
        self.assertEqual((rc, alert), (0, None))
        rc, summary, _t, alert = self.run_prelim("preliminary", {"henry": "BYE"}, same=False)
        self.assertIn("a starter can't play", alert[0])
        self.assertIn("D. Henry (BYE)", alert[1])
        self.assertIn("can't play: D. Henry (BYE)", summary)

    def test_a_preliminary_difference_alone_does_not_alert(self):
        wf = self.week({})
        rc, summary, _t, alert = self.run_prelim("preliminary", {"maye": "Q"}, same=False)
        self.assertIsNone(alert)


class TestLivePoints(unittest.TestCase):
    """Yahoo's live points for my team and my opponent, for a pre-lock run (read-only; never fatal)."""

    @staticmethod
    def payload(team, week, players):
        cov = {"coverage_type": "week", "week": str(week)}
        items = [{"player": [[{"player_key": f"470.p.{pid}"}, {"player_id": str(pid)}, {"name": {"full": f"Player {pid}"}},
                              {"editorial_team_abbr": "Kc"}, {"display_position": "WR"}, {"primary_position": "WR"}],
                             {"selected_position": [cov, {"position": "WR"}]}, {"player_points": {"0": cov, "total": f"{pts:.2f}"}}]}
                 for pid, pts in players]
        numbered = dict({str(i): x for i, x in enumerate(items)}, count=len(items))
        return {"fantasy_content": {"team": [[{"team_key": f"470.l.1.t.{team}"}, {"team_id": str(team)}],
                                             {"roster": dict(cov, **{"0": {"players": numbered}})}]}}

    def session(self, bodies, status=200):
        urls = []

        def get(url, timeout):
            urls.append(url)
            team = int(url.split(".t.")[1].split("/")[0])
            return mock.Mock(status_code=status, text=json.dumps(bodies[team]))
        return mock.Mock(get=get), urls

    def test_points_of_both_teams_for_the_week_in_progress(self):
        sess, urls = self.session({9: self.payload(9, 8, [(101, 12.5), (102, 0.0)]), 4: self.payload(4, 8, [(201, 7.25)])})
        got = ar.fetch_live_points(8, [9, 4], league_key="470.l.1", session=sess)
        self.assertTrue(got["ok"])
        self.assertEqual(got["points"], {"101": 12.5, "102": 0.0, "201": 7.25})
        self.assertTrue(all("roster;week=8/players/stats;type=week;week=8" in u for u in urls))
        self.assertEqual(len(urls), 2)

    def test_a_failure_is_reported_never_raised_and_scrubbed_of_the_key(self):
        sess, _ = self.session({9: {}}, status=401)
        got = ar.fetch_live_points(8, [9], league_key="470.l.1", session=sess)
        self.assertEqual((got["ok"], got["points"]), (False, {}))
        self.assertIn("HTTP 401", got["error"])
        self.assertNotIn("470.l.1", got["error"])

    def test_the_build_gets_the_file_and_a_failed_fetch_only_costs_the_live_points(self):
        e = {"key": "k", "kind": "prelock", "season": 2026, "week": 8, "lock_local": "2026-11-01T13:25", "run_at_local": "2026-11-01T12:25",
             "games": ["NE@MIA"], "players": []}
        argvs = []

        def fake(argv):
            argvs.append(argv)
            return 2, "[WEEK FILE] NOT BUILT: stop here"
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.object(ar, "write_live_points", return_value=(Path(d) / "x_live.json", "live points: 2")):
                ar.run_entry(e, utc("2026-11-01T20:25:00"), "db", "raw", "lraw", d, run=fake, take_snapshot=False)
            self.assertIn("--live-points", argvs[-1])
            with mock.patch.object(ar, "write_live_points", return_value=(None, "live points unavailable (x)")):
                rc, _s, text, _a = ar.run_entry(e, utc("2026-11-01T20:25:00"), "db", "raw", "lraw", d, run=fake, take_snapshot=False)
            self.assertNotIn("--live-points", argvs[-1])
            self.assertIn("live points unavailable", text)


class TestRunTask(unittest.TestCase):
    """What the run task does when it fires: due runs, MISSED, superseded, handled."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, kind, run_at, lock, week=9):
        e = {"key": f"2026w{week:02d}-{kind}-{lock:%Y%m%dT%H%MZ}", "kind": kind, "season": 2026, "week": week,
             "run_at": run_at.isoformat(), "lock": lock.isoformat(), "run_at_local": ar.to_local(run_at, PT).isoformat(timespec="minutes"),
             "lock_local": ar.to_local(lock, PT).isoformat(timespec="minutes"), "games": ["JAX@BAL"], "players": ["x"],
             "schedule_source": "fixture"}
        return e

    def run_task(self, entries, now, run_entry=None, log_job=None):
        plan_file = self.dir / "plan.json"
        ar.write_plan(plan_file, {"entries": entries})
        shown = []
        args = argparse.Namespace(
            as_of=now.isoformat(), log_dir=str(self.dir), plan_file=str(plan_file), no_popup=True, no_notify=False,
            no_snapshot=True, no_register=True, db="x.db", raw_dir="r", league_raw_dir="l", out_dir=str(self.dir))
        calls = []
        fake = run_entry or (lambda e, *a, **k: calls.append(e["key"]) or (0, "[STARTSIT] matches", "text", None))
        with mock.patch.object(ar, "run_entry", side_effect=fake), \
                mock.patch.object(ar, "notify", side_effect=lambda t, x: shown.append((t, x)) or "toast"), \
                mock.patch.object(ar, "log_job_running", side_effect=log_job or (lambda: False)), \
                mock.patch.object(ar.time, "sleep"), mock.patch("builtins.print"):
            rc = ar.cmd_run(args)
        lines = (self.dir / sr.SUMMARY_LOG).read_text(encoding="utf-8").splitlines() if (self.dir / sr.SUMMARY_LOG).exists() else []
        return rc, calls, shown, lines

    def test_a_run_woken_after_its_kickoff_is_missed_with_a_notification_and_never_run_late(self):
        lock = utc("2026-11-06T01:15:00")
        e = self.entry("prelock", lock - ar.LEAD, lock)
        rc, calls, shown, lines = self.run_task([e], lock + timedelta(minutes=30))     # PC woke after kickoff
        self.assertEqual(calls, [])
        self.assertEqual(rc, 0)
        self.assertEqual(len(lines), 1)
        self.assertIn(" MISSED ", lines[0])
        self.assertIn(f"key={e['key']}", lines[0])
        self.assertEqual(len(shown), 1)
        self.assertIn("missed", shown[0][0])
        # and it is reported once: the next wake-up finds it handled
        rc, calls, shown, lines = self.run_task([e], lock + timedelta(hours=2))
        self.assertEqual((calls, shown, len(lines)), ([], [], 1))

    def test_too_close_to_kickoff_counts_as_missed_a_little_late_still_runs(self):
        lock = utc("2026-11-06T01:15:00")
        e = self.entry("prelock", lock - ar.LEAD, lock)
        self.assertEqual(self.run_task([e], lock - timedelta(minutes=5))[1], [])          # 5 minutes before: missed
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.assertEqual(self.run_task([e], lock - timedelta(minutes=30))[1], [e["key"]])  # woke 45 min late: still runs

    def test_an_earlier_due_run_is_superseded_by_a_later_one(self):
        first = utc("2026-11-06T01:15:00")
        prelim = self.entry("preliminary", utc("2026-11-05T14:50:00"), first)
        prelock = self.entry("prelock", first - ar.LEAD, first)
        rc, calls, shown, lines = self.run_task([prelim, prelock], first - timedelta(minutes=60))
        self.assertEqual(calls, [prelock["key"]])
        self.assertTrue(any(" SKIP " in ln and prelim["key"] in ln for ln in lines))
        self.assertEqual(shown, [])

    def test_nothing_due_writes_nothing(self):
        lock = utc("2026-11-06T01:15:00")
        rc, calls, shown, lines = self.run_task([self.entry("prelock", lock - ar.LEAD, lock)], lock - timedelta(hours=3))
        self.assertEqual((rc, calls, shown, lines), (0, [], [], []))

    def test_a_difference_notifies_and_a_failure_is_a_failed_line(self):
        lock = utc("2026-11-06T01:15:00")
        e = self.entry("prelock", lock - ar.LEAD, lock)
        rc, _c, shown, lines = self.run_task([e], lock - ar.LEAD, run_entry=lambda *a, **k: (
            0, "[STARTSIT] DIFFERS", "text", ("Start/sit week 9: change before Thu 17:15", "Start A over B -- lean")))
        self.assertEqual(shown, [("Start/sit week 9: change before Thu 17:15", "Start A over B -- lean")])
        self.assertIn(" OK ", lines[0])
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        rc, _c, shown, lines = self.run_task([e], lock - ar.LEAD, run_entry=lambda *a, **k: (2, "[STARTSIT] NOT BUILT", "t", None))
        self.assertEqual((rc, shown), (2, []))
        self.assertIn(" FAILED ", lines[0])

    def test_a_running_log_job_comes_first_the_run_waits_for_it(self):
        lock = utc("2026-11-06T01:15:00")
        e = self.entry("prelock", lock - ar.LEAD, lock)
        busy = iter([True, True, False])
        clock = iter([lock - ar.LEAD, lock - ar.LEAD + timedelta(seconds=40), lock - ar.LEAD + timedelta(seconds=40)])
        with mock.patch.object(ar, "utcnow", side_effect=lambda: next(clock, lock - ar.LEAD + timedelta(seconds=40))):
            rc, calls, shown, lines = self.run_task([e], lock - ar.LEAD, log_job=lambda: next(busy))
        self.assertEqual(calls, [e["key"]])                      # it ran once the log job had finished
        self.assertIn(" OK ", lines[0])

    def test_a_log_job_still_running_at_the_deadline_makes_the_run_missed_not_late(self):
        lock = utc("2026-11-06T01:15:00")
        e = self.entry("prelock", lock - ar.LEAD, lock)
        late = lock - ar.MIN_LEAD - ar.RUN_BUDGET + timedelta(seconds=1)
        with mock.patch.object(ar, "utcnow", return_value=late):
            rc, calls, shown, lines = self.run_task([e], lock - ar.LEAD, log_job=lambda: True)
        self.assertEqual(calls, [])                              # never touched the DB while the log job ran
        self.assertIn(" MISSED ", lines[0])
        self.assertIn("a tracker log job was still running", lines[0])
        self.assertEqual(len(shown), 1)

    def test_the_wait_helper(self):
        t = [utc("2026-11-01T14:00:00")]

        def clock():
            return t[0]

        def sleep(s):
            t[0] += timedelta(seconds=s)
        busy = iter([True, True, True, False])
        self.assertEqual(ar.wait_for_log_jobs(t[0] + timedelta(minutes=5), lambda: next(busy), sleep, clock), (True, 60.0))
        self.assertEqual(ar.wait_for_log_jobs(t[0] + timedelta(seconds=30), lambda: True, sleep, clock), (False, 40.0))
        self.assertEqual(ar.wait_for_log_jobs(t[0], lambda: False, sleep, clock), (True, 0.0))

    def test_the_plan_keeps_unhandled_past_runs_so_they_can_be_reported(self):
        lock = utc("2026-11-06T01:15:00")
        old = {"entries": [self.entry("prelock", lock - ar.LEAD, lock)]}
        new = ar.carry_over(old, {"entries": []}, lock + timedelta(hours=1), set())
        self.assertEqual(len(new["entries"]), 1)
        self.assertEqual(ar.carry_over(old, {"entries": []}, lock + timedelta(hours=1), {old["entries"][0]["key"]})["entries"], [])
        self.assertEqual(ar.carry_over(old, {"entries": []}, lock + timedelta(days=9), set())["entries"], [])


class TestTaskDefinitions(unittest.TestCase):
    def test_one_one_shot_trigger_per_future_run_and_the_other_tasks_settings(self):
        lock = utc("2026-11-06T01:15:00")
        entries = [{"run_at": (lock - ar.LEAD).isoformat(), "run_at_local": "2026-11-05T16:00"},
                   {"run_at": (lock - timedelta(days=2)).isoformat(), "run_at_local": "2026-11-03T17:15"}]   # past
        xml = ar.run_task_xml("pc\\user", entries, lock - timedelta(hours=3))
        self.assertEqual(xml.count("<TimeTrigger>"), 1)
        self.assertIn("<StartBoundary>2026-11-05T16:00:00</StartBoundary>", xml)
        self.assertIn("<Delay>PT4M</Delay>", xml)
        self.assertIn("autorun.py\" run --scheduled", xml)
        ref = sr.task_xml("snapshot", "pc\\user", datetime(2026, 1, 1))
        block = ref[ref.index("<Settings>"): ref.index("</Settings>")]
        self.assertIn(block, xml)
        self.assertIn("<WakeToRun>true</WakeToRun>", xml)
        plan_xml = ar.plan_task_xml("pc\\user", datetime(2026, 11, 3, 7, 0))
        self.assertIn("<StartBoundary>2026-11-04T06:45:00</StartBoundary>", plan_xml)
        self.assertIn("plan --register --scheduled", plan_xml)
        self.assertIn(block, plan_xml)


if __name__ == "__main__":
    unittest.main()
