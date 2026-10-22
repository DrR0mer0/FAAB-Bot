"""Tests for the week-file builder and its projection providers. From the repo root:

    python -m unittest discover -s startsit -v

A made-up league ("Team Name N", invented players) in a temp DB, stats and
roster CSVs in a temp dir, and a stand-in injury report -- never real league
data, and nothing is fetched.
"""
import contextlib
import csv
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import build_week as bw
import lineup_sim
import projections

TZ = timezone.utc
AS_OF = datetime(2026, 10, 22, 14, 0, tzinfo=TZ)          # Thursday 07:00 Pacific, before the Thursday game
THU_KICK = "2026-10-22T20:15:00"                            # nfl_games stores US/Eastern -> 2026-10-23T00:15 UTC
STATS_COLS = ["player_id", "season", "week", "season_type", "position", "team", "receiving_yards"]

# (yahoo id, gsis, name, pos, yahoo team, slot, team id)
ROSTER = [
    ("101", "g-qb", "Quinn Mine", "QB", "KC", "QB", 3),
    ("102", "g-rb1", "Rory Mine", "RB", "LAR", "RB", 3),          # Yahoo's LAR is nflverse's LA
    ("103", "g-rb2", "Reed Mine", "RB", "BUF", "RB", 3),
    ("104", "g-wr1", "Wade Mine", "WR", "BUF", "WR", 3),
    ("105", "g-wr2", "Wes Bye", "WR", "MIA", "WR", 3),            # MIA has no week-7 game
    ("106", "g-te", "Tate Mine", "TE", "KC", "TE", 3),
    ("107", "g-flex", "Flex Iffy", "WR", "LA", "W/R/T", 3),        # Questionable
    ("108", "g-rookie", "Rookie Nobody", "RB", "BUF", "BN", 3),    # no NFL games at all
    ("109", "g-out", "Owen Out", "WR", "KC", "BN", 3),             # Out
    ("110", "g-ir", "Ira Reserve", "RB", "BUF", "IR", 3),          # nflverse RES
    ("111", "g-moved", "Moe Moved", "TE", "KC", "BN", 3),          # Yahoo KC, nflverse LV
    ("201", "g-oqb", "Otto Qb", "QB", "BUF", "QB", 4),
    ("202", "g-orb", "Oren Rb", "RB", "KC", "RB", 4),              # Doubtful
    ("203", "g-orook", "Opp Rookie", "WR", "LA", "WR", 4),         # no history: gets a labelled fallback
    ("204", "g-obench", "Opp Bench", "WR", "BUF", "BN", 4),        # on the bench: not a starter
]
# league-scored points per game (receiving_yards / 10), weeks of 2025 then 2026
HISTORY = {"g-qb": [(2025, 16, 180), (2025, 17, 200), (2026, 1, 150), (2026, 2, 210), (2026, 3, 190), (2026, 4, 220), (2026, 5, 170),
                    (2026, 6, 230)],
           "g-rb1": [(2026, 5, 100), (2026, 6, 140)],               # thin: two games
           "g-rb2": [(2026, w, 90 + 10 * w) for w in range(1, 7)],
           "g-wr1": [(2026, w, 120) for w in range(1, 7)],
           "g-wr2": [(2026, w, 80) for w in range(1, 7)],
           "g-te": [(2026, w, 60) for w in range(2, 7)],
           "g-flex": [(2026, w, 70) for w in range(1, 7)],
           "g-out": [(2026, w, 50) for w in range(1, 7)],
           "g-ir": [(2025, 15, 90)],                                  # nothing this season
           "g-moved": [(2026, w, 40) for w in range(1, 7)],
           "g-oqb": [(2026, w, 200) for w in range(1, 7)],
           "g-orb": [(2026, w, 110) for w in range(1, 7)],
           "g-obench": [(2026, w, 30) for w in range(1, 7)]}
TEAM_OF = {"g-qb": "KC", "g-rb1": "LA", "g-rb2": "BUF", "g-wr1": "BUF", "g-wr2": "MIA", "g-te": "KC", "g-flex": "LA", "g-out": "KC",
           "g-ir": "BUF", "g-moved": "KC", "g-oqb": "BUF", "g-orb": "KC", "g-obench": "BUF"}
INJURIES = [("g-flex", "Questionable"), ("g-out", "Out"), ("g-orb", "Doubtful")]


def injury_report(rows=INJURIES, week=7):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["season", "season_type", "game_type", "team", "week", "gsis_id", "position", "full_name",
                                        "report_status"])
    w.writeheader()
    for pid, st in rows:
        w.writerow(dict(season=2026, season_type="REG", game_type="REG", team="X", week=week, gsis_id=pid, position="WR",
                        full_name=pid, report_status=st))
    return buf.getvalue().encode("utf-8")


def fetched(raw=None, age_hours=8):
    return lambda season: {"ok": True, "raw_bytes": raw if raw is not None else injury_report(), "error": None, "url": "u",
                           "last_modified": AS_OF - timedelta(hours=age_hours)}


class BuildCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.raw = self.dir / "raw"
        self.raw.mkdir()
        self.db = self.dir / "league.db"
        con = sqlite3.connect(self.db)
        bw.ld.init_schema(con)
        con.execute("CREATE TABLE nfl_games (season INTEGER, week INTEGER, game_id TEXT, kickoff_utc TEXT, home_team TEXT, "
                    "away_team TEXT, home_score INTEGER, away_score INTEGER, is_thursday INTEGER, is_london INTEGER, is_playoffs INTEGER DEFAULT 0)")
        games = [(7, "LA", "KC", THU_KICK), (7, "BUF", "NYJ", "2026-10-25T13:00:00"), (6, "MIA", "BUF", "2026-10-18T13:00:00")]
        con.executemany("INSERT INTO nfl_games (season, week, game_id, kickoff_utc, home_team, away_team) VALUES (2026, ?, 'g', ?, ?, ?)",
                        [(w, k, h, a) for w, h, a, k in games])
        self.snap = lambda resource, taken: con.execute(
            "INSERT INTO lg_snapshots (batch_id, taken_at, resource, ok) VALUES ('b', ?, ?, 1)", (taken, resource)).lastrowid
        t0 = "2026-10-22T13:35:00+00:00"
        sid = self.snap("teams", t0)
        con.executemany("INSERT INTO lg_teams (snapshot_id, team_id, team_name, is_mine) VALUES (?, ?, ?, ?)",
                        [(sid, t, f"Team Name {t}", 1 if t == 3 else 0) for t in (1, 3, 4)])
        sid = self.snap("settings", t0)
        con.executemany("INSERT INTO lg_roster_slots VALUES (?, ?, ?, ?)", [(sid, p, n, s) for p, n, s in (
            ("BN", 4, 0), ("IR", 1, 0), ("QB", 1, 1), ("RB", 2, 1), ("TE", 1, 1), ("W/R/T", 1, 1), ("WR", 2, 1))])
        sid = self.snap("scoreboard", t0)
        con.executemany("INSERT INTO lg_matchups (snapshot_id, week, team_id, opponent_team_id) VALUES (?, 7, ?, ?)",
                        [(sid, 3, 4), (sid, 4, 3), (sid, 1, 2)])
        self.roster_rows = ROSTER
        self.add_rosters(con, t0)
        con.commit()
        self.con = con
        self.write_stats(HISTORY)
        with open(self.raw / "roster_weekly_2026.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["season", "week", "gsis_id", "team", "position", "status"])
            w.writeheader()
            for pid, team in TEAM_OF.items():
                w.writerow(dict(season=2026, week=7, gsis_id=pid, team="LV" if pid == "g-moved" else team, position="WR",
                                status="RES" if pid == "g-ir" else "ACT"))
        self.ro = bw.connect_ro(self.db)

    def tearDown(self):
        self.ro.close()
        self.con.close()
        self.tmp.cleanup()

    def add_rosters(self, con, taken, rows=None, week=7):
        sid = self.snap("rosters", taken)
        con.executemany("INSERT INTO lg_rosters (snapshot_id, week, team_id, external_id, player_id, name, position, team, "
                        "selected_position) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(sid, week, t, y, g, n, p, tm, slot) for y, g, n, p, tm, slot, t in (rows or self.roster_rows)])
        con.commit()

    def write_stats(self, history, extra=()):
        by_season = {}
        for pid, games in history.items():
            for s, w, yds in games:
                by_season.setdefault(s, []).append(dict(player_id=pid, season=s, week=w, season_type="REG", position="WR",
                                                        team=TEAM_OF.get(pid), receiving_yards=yds))
        for row in extra:
            by_season.setdefault(row["season"], []).append(row)
        for s, rows in by_season.items():
            with open(self.raw / f"stats_player_week_{s}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=STATS_COLS)
                w.writeheader()
                w.writerows(rows)

    def build(self, stage="thursday", as_of=AS_OF, fetch=None, provider=None, week=7):
        with contextlib.redirect_stderr(io.StringIO()):
            return bw.build_week(self.ro, 2026, week, stage, provider=provider, as_of=as_of, raw_dir=self.raw,
                                 injury_fetch=fetch or fetched())

    def by_name(self, wf, side="my_team", key="players"):
        return {p["name"]: p for p in wf[side][key]}


class TestWeekFile(BuildCase):
    def test_a_valid_week_file_from_the_league_tables(self):
        wf, warnings = self.build()
        lineup_sim.parse_week(wf)                                           # lineup_sim's contract, schema_version 1
        self.assertEqual(wf["league"]["slots"], ["QB", "WR", "WR", "RB", "RB", "TE", "W/R/T", "BN", "BN", "BN", "BN", "IR"])
        mine = self.by_name(wf)
        self.assertEqual(len(mine), 10)                                     # the whole roster, bench and IR -- minus the rookie
        self.assertEqual(wf["my_team"]["name"], "Team Name 3")
        self.assertEqual(mine["Rory Mine"]["team"], "LA")                   # LAR -> LA
        self.assertEqual((mine["Rory Mine"]["opp"], mine["Quinn Mine"]["opp"]), ("KC", "LA"))
        self.assertEqual(mine["Wes Bye"]["status"], "BYE")
        self.assertEqual(mine["Flex Iffy"]["status"], "Q")
        self.assertEqual(mine["Owen Out"]["status"], "O")
        self.assertEqual(mine["Ira Reserve"]["status"], "IR")               # nflverse RES
        self.assertEqual((mine["Ira Reserve"]["yahoo_slot"], mine["Quinn Mine"]["id"]), ("IR", "101"))
        self.assertNotIn("status", mine["Wade Mine"])
        self.assertNotIn("play_prob", mine["Flex Iffy"])                    # left to lineup_sim's STATUS_PLAY_PROB
        opp = self.by_name(wf, "opponent", "starters")
        self.assertEqual(set(opp), {"Otto Qb", "Oren Rb", "Opp Rookie"})    # starters only
        self.assertEqual((wf["opponent"]["name"], opp["Oren Rb"]["status"]), ("Team Name 4", "D"))
        meta = wf["_meta"]
        self.assertEqual((meta["injuries"]["source"], meta["injuries"]["age_hours"]), ("nflverse", 8.0))
        self.assertIn("resolve_designations", meta["injuries"]["resolved_by"])
        self.assertEqual(mine["Moe Moved"]["team"], "LV")                   # nflverse's weekly roster wins ...
        self.assertEqual(meta["team_mismatches"], ["Moe Moved: Yahoo KC, nflverse LV (nflverse used)"])   # ... and it's recorded
        self.assertIn("roster snapshot", meta["roster_source"])

    def test_no_projection_my_player_is_dropped_loudly_an_opponent_starter_gets_a_labelled_fallback(self):
        wf, warnings = self.build()
        self.assertNotIn("Rookie Nobody", self.by_name(wf))
        self.assertTrue(any(w.startswith("NO PROJECTION, left out of the week file: Rookie Nobody (RB)") for w in warnings))
        rook = self.by_name(wf, "opponent", "starters")["Opp Rookie"]
        self.assertEqual(rook["p50"], 7.5)                                  # median WR p50 in the file: Wade 12, Wes 8, Flex 7, Owen 5
        self.assertTrue(rook["note"].startswith("FALLBACK projection: WR median in this file"))
        self.assertEqual(wf["_meta"]["projections"]["fallback"], ["Opp Rookie (WR) -> 7.5"])
        self.assertIn("Owen Out", self.by_name(wf))                         # out players need no p50 ... and keep theirs if they have one

    def test_opponent_lineup_missing_is_left_out_not_guessed(self):
        self.add_rosters(self.con, "2026-10-22T13:40:00+00:00", rows=[r for r in ROSTER if r[6] == 3])
        wf, warnings = self.build()
        self.assertNotIn("opponent", wf)
        self.assertTrue(any("opponent left out, not guessed" in w for w in warnings))

    def test_an_unknown_yahoo_team_code_stops_the_build(self):
        self.add_rosters(self.con, "2026-10-22T13:40:00+00:00", rows=[r if r[0] != "104" else r[:4] + ("BUFF",) + r[5:] for r in ROSTER])
        with self.assertRaisesRegex(bw.BuildError, r"\['BUFF'\] are not in the 2026 nflverse schedule"):
            self.build()

    def test_a_past_week_uses_its_final_lineups(self):
        sid = self.snap("lineups", "2026-10-20T13:35:00+00:00")
        self.con.executemany("INSERT INTO lg_lineups (snapshot_id, week, team_id, external_id, player_id, name, position, team, "
                             "selected_position, points) VALUES (?, 6, ?, ?, ?, ?, ?, ?, ?, ?)",
                             [(sid, t, y, g, n, p, tm, slot, 9.5) for y, g, n, p, tm, slot, t in ROSTER])
        self.con.execute("INSERT INTO lg_matchups (snapshot_id, week, team_id, opponent_team_id) SELECT snapshot_id, 6, team_id, "
                         "opponent_team_id FROM lg_matchups WHERE week=7")
        self.con.commit()
        wf, _w = self.build(week=6, fetch=fetched(injury_report(week=6)))
        self.assertEqual(wf["_meta"]["roster_source"], "final lineups of week 6 (lg_lineups)")
        self.assertNotIn("actual", self.by_name(wf)["Quinn Mine"])          # a thursday-stage file never carries actuals


class TestStatusConflicts(BuildCase):
    """A long-term roster status stands unless YAHOO's current status contradicts it; ESPN only cross-checks (2026-10-22)."""

    LONG_TERM = {"g-te": "EXE", "g-flex": "PUP", "g-out": "EXE", "g-ir": "RES", "g-wr1": "SUS"}
    ESPN = {"g-te": "DAY_TO_DAY", "g-flex": "ACTIVE", "g-out": "ACTIVE", "g-ir": "INJURY_RESERVE"}

    def setUp(self):
        super().setUp()
        bw.ld.ledger.init_schema(self.con)                                     # the tracker's tables: an ESPN roster-% snapshot
        sid = self.con.execute("INSERT INTO tr_ownership_snapshots (taken_at, source, ok) VALUES (?, 'espn', 1)",
                               ((AS_OF - timedelta(hours=1)).isoformat(),)).lastrowid
        self.con.executemany("INSERT INTO tr_ownership (snapshot_id, source, external_id, player_id, percent_owned, injury_status) "
                             "VALUES (?, 'espn', ?, ?, 1.0, ?)", [(sid, p, p, st) for p, st in self.ESPN.items()])
        self.con.commit()
        with open(self.raw / "roster_weekly_2026.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["season", "week", "gsis_id", "team", "position", "status"])
            w.writeheader()
            for pid, team in TEAM_OF.items():
                w.writerow(dict(season=2026, week=7, gsis_id=pid, team=team, position="WR", status=self.LONG_TERM.get(pid, "ACT")))

    def with_yahoo(self, statuses, taken="2026-10-22T13:40:00+00:00"):
        """A newer roster snapshot that carries Yahoo's status for every player ('' unless given)."""
        sid = self.snap("rosters", taken)
        self.con.executemany("INSERT INTO lg_rosters (snapshot_id, week, team_id, external_id, player_id, name, position, team, "
                             "selected_position, status) VALUES (?, 7, ?, ?, ?, ?, ?, ?, ?, ?)",
                             [(sid, t, y, g, n, p, tm, slot, statuses.get(g, "")) for y, g, n, p, tm, slot, t in ROSTER])
        self.con.commit()

    def test_jacobs_case_nflverse_exempt_yahoo_no_status_is_in_and_the_conflict_is_loud(self):
        self.with_yahoo({"g-ir": "IR", "g-wr1": "SUSP"})
        wf, warnings = self.build()
        mine = self.by_name(wf)
        self.assertNotIn("status", mine["Tate Mine"])                 # nflverse EXE, Yahoo no status, ESPN DTD: available
        self.assertEqual(mine["Flex Iffy"]["status"], "Q")            # nflverse PUP, Yahoo no status: the injury report's Q
        self.assertEqual(mine["Owen Out"]["status"], "O")             # nflverse EXE, Yahoo no status: the report's Out still holds
        self.assertEqual(mine["Ira Reserve"]["status"], "IR")         # Yahoo agrees (IR): no conflict
        self.assertEqual(mine["Wade Mine"]["status"], "SUSP")         # Yahoo agrees (SUSP)
        conflicts = {c["name"]: c for c in wf["_meta"]["status_conflicts"]}
        self.assertEqual(set(conflicts), {"Tate Mine", "Flex Iffy", "Owen Out"})
        self.assertEqual((conflicts["Tate Mine"]["yahoo"], conflicts["Tate Mine"]["used"], conflicts["Tate Mine"]["play_chance"]),
                         ("", "available", 1.0))
        loud = [w for w in warnings if w.startswith("STATUS CONFLICT:")]
        self.assertEqual(len(loud), 3)
        self.assertTrue(any("Tate Mine -- nflverse's weekly roster says EXE, but Yahoo shows no status (ESPN DAY_TO_DAY). Using "
                            "Yahoo: available" in w for w in loud))
        self.assertIn("captured", wf["_meta"]["yahoo_status"])
        lineup_sim.parse_week(wf)

    def test_espn_day_to_day_but_yahoo_exempt_stays_out_and_espn_only_adds_a_warning(self):
        self.with_yahoo({"g-te": "CEL", "g-flex": "PUP-R", "g-out": "CEL", "g-ir": "IR", "g-wr1": "SUSP"})
        wf, warnings = self.build()
        mine = self.by_name(wf)
        self.assertEqual((mine["Tate Mine"]["status"], mine["Flex Iffy"]["status"]), ("EXEMPT", "PUP"))
        self.assertEqual(wf["_meta"]["status_conflicts"], [])
        cross = [w for w in warnings if w.startswith("ESPN CROSS-CHECK:")]
        self.assertTrue(any("Tate Mine: ESPN says DAY_TO_DAY, the file has him out (EXEMPT) -- cross-check only" in w for w in cross))
        self.assertEqual(len(wf["_meta"]["espn_cross_check"]), 3)          # Tate, Flex and Owen: ESPN says they may play

    def test_without_a_yahoo_status_the_long_term_status_stands(self):
        wf, warnings = self.build()                                         # the setUp snapshot predates the capture: NULL
        mine = self.by_name(wf)
        self.assertEqual((mine["Tate Mine"]["status"], mine["Flex Iffy"]["status"]), ("EXEMPT", "PUP"))
        self.assertEqual(wf["_meta"]["status_conflicts"], [])
        self.assertIn("not available", wf["_meta"]["yahoo_status"])
        self.assertTrue(any(w.startswith("ESPN CROSS-CHECK: Tate Mine") for w in warnings))

    def test_an_unknown_yahoo_code_is_ignored_loudly(self):
        self.with_yahoo({"g-te": "WEIRD"})
        wf, warnings = self.build()
        self.assertEqual(self.by_name(wf)["Tate Mine"]["status"], "EXEMPT")
        self.assertTrue(any("unknown Yahoo status 'WEIRD'" in w for w in warnings))


class TestBaseline(BuildCase):
    def provider(self):
        store = projections.scoring.StatsStore(self.raw, projections.scoring.load_config()["scoring"])
        return projections.BaselineProvider(store)

    def test_a_weighted_mean_of_the_last_games_with_his_own_spread_from_six_games(self):
        wf, _w = self.build()
        mine = self.by_name(wf)
        # Quinn: last 8 games oldest first 18, 20, 15, 21, 19, 22, 17, 23 (points = yards / 10); newest weight 1, half-life 3
        pts = [18, 20, 15, 21, 19, 22, 17, 23]
        wts = [0.5 ** ((7 - i) / 3) for i in range(8)]
        self.assertAlmostEqual(mine["Quinn Mine"]["p50"], round(sum(p * w for p, w in zip(pts, wts)) / sum(wts), 2))
        self.assertLessEqual(mine["Quinn Mine"]["p10"], mine["Quinn Mine"]["p50"])
        self.assertGreaterEqual(mine["Quinn Mine"]["p90"], mine["Quinn Mine"]["p50"])
        self.assertEqual(mine["Wade Mine"]["p50"], 12.0)
        self.assertNotIn("p10", mine["Tate Mine"])                           # five games: lineup_sim's DEFAULT_SPREADS
        self.assertIn("weighted mean", wf["_meta"]["projections"]["provenance"])

    def test_thin_new_team_and_last_season_only_players_are_listed(self):
        wf, _w = self.build()
        flagged = wf["_meta"]["projections"]["flagged"]
        self.assertEqual(flagged["Rory Mine"], ["thin history (2 games)"])
        self.assertEqual(flagged["Moe Moved"], ["new team (recent games for KC, now LV)"])
        self.assertEqual(flagged["Ira Reserve"], ["thin history (1 game)", "no 2026 games yet (last game 2025 week 15)"])
        self.assertIn("thin history", self.by_name(wf)["Rory Mine"]["note"])   # shown in lineup_sim's close-call text too

    def test_the_target_week_and_later_weeks_are_never_read(self):
        before = self.provider().project(2026, 7, [{"id": p, "gsis_id": p, "name": p, "pos": "WR", "team": TEAM_OF[p]} for p in HISTORY])
        extra = [dict(player_id=p, season=2026, week=w, season_type="REG", position="WR", team=TEAM_OF[p], receiving_yards=990)
                 for p in HISTORY for w in (7, 8, 9)]
        self.write_stats(HISTORY, extra)
        after = self.provider().project(2026, 7, [{"id": p, "gsis_id": p, "name": p, "pos": "WR", "team": TEAM_OF[p]} for p in HISTORY])
        self.assertEqual(after.values, before.values)
        self.assertEqual(after.flags, before.flags)
        wf_a, _ = self.build()
        self.write_stats(HISTORY)
        wf_b, _ = self.build()
        strip = lambda wf: [{k: v for k, v in p.items()} for p in wf["my_team"]["players"]]
        self.assertEqual(strip(wf_a), strip(wf_b))

    def test_weighted_quantile(self):
        self.assertEqual(projections.weighted_quantile([1, 2, 3, 4], [1, 1, 1, 1], 0.5), 2)
        self.assertEqual(projections.weighted_quantile([1, 2, 3, 4], [1, 1, 1, 1], 0.9), 4)
        self.assertEqual(projections.weighted_quantile([1, 2, 3, 4], [0, 0, 0, 1], 0.1), 4)


class TestCsvOverride(BuildCase):
    def test_a_row_overrides_the_baseline_for_that_player_only(self):
        path = self.dir / "proj.csv"
        path.write_text("# outside projections\nid,name,p50,p10,p90\n,Wade Mine,30,20,40\n108,,9,,\n,Nobody Here,5,,\n,Tate Mine,5,9,3\n",
                        encoding="utf-8")
        store = projections.scoring.StatsStore(self.raw, projections.scoring.load_config()["scoring"])
        wf, warnings = self.build(provider=projections.CsvProvider(path, projections.BaselineProvider(store)))
        mine = self.by_name(wf)
        self.assertEqual((mine["Wade Mine"]["p10"], mine["Wade Mine"]["p50"], mine["Wade Mine"]["p90"]), (20, 30, 40))
        self.assertEqual(mine["Rookie Nobody"]["p50"], 9)                   # by Yahoo id: no longer dropped
        self.assertEqual(mine["Quinn Mine"]["p50"], self.by_name(self.build()[0])["Quinn Mine"]["p50"])   # untouched
        self.assertTrue(any("'Nobody Here' matches no player" in w for w in warnings))
        self.assertTrue(any("needs p10 <= p50 <= p90" in w for w in warnings))
        self.assertTrue(wf["_meta"]["projections"]["provenance"].startswith("csv: proj.csv (2 override(s)) over baseline:"))


class TestSundayLocks(BuildCase):
    AFTER_THU = datetime(2026, 10, 25, 14, 0, tzinfo=TZ)                    # Sunday morning, the Thursday game is over

    def test_a_kicked_off_game_without_a_later_roster_snapshot_stops_the_build(self):
        with self.assertRaisesRegex(bw.BuildError, r"have kicked off but the newest roster snapshot was taken"):
            self.build("sunday", as_of=self.AFTER_THU)

    def test_locks_come_from_kickoff_times_and_a_snapshot_taken_after_them(self):
        self.add_rosters(self.con, "2026-10-24T13:35:00+00:00")
        self.write_stats(HISTORY, [dict(player_id="g-qb", season=2026, week=7, season_type="REG", position="QB", team="KC",
                                        receiving_yards=255)])
        wf, _w = self.build("sunday", as_of=self.AFTER_THU)
        lineup_sim.parse_week(wf)
        mine = self.by_name(wf)
        q, t, bench, buf = mine["Quinn Mine"], mine["Tate Mine"], mine["Owen Out"], mine["Wade Mine"]
        self.assertEqual((q["locked"], q["started"], q["locked_slot"], q["actual"]), (True, True, "QB", 25.5))
        self.assertEqual((t["locked"], t["started"], t.get("actual")), (True, True, None))   # no stats published yet: simulated
        self.assertEqual((bench["locked"], bench["started"]), (True, False))
        self.assertNotIn("locked", buf)                                       # BUF plays Sunday afternoon
        self.assertTrue(any("kicked off" in n for n in wf["_meta"]["locks"]))


class TestCli(BuildCase):
    def test_run_writes_the_week_file_and_the_report_beside_it(self):
        out = self.dir / "league_data" / "2026_week07_thursday.json"
        with contextlib.redirect_stdout(io.StringIO()) as so, contextlib.redirect_stderr(io.StringIO()):
            rc = bw.main(["--season", "2026", "--week", "7", "--stage", "thursday", "--db", str(self.db), "--raw-dir", str(self.raw),
                          "--out", str(out), "--as-of", AS_OF.isoformat(), "--run", "--sims", "2000"], injury_fetch=fetched())
        self.assertEqual(rc, 0, so.getvalue())
        self.assertTrue(out.exists())
        self.assertTrue((self.dir / "league_data" / "2026_week07_thursday_report.md").read_text(encoding="utf-8").strip())
        report = json.loads((self.dir / "league_data" / "2026_week07_thursday_report.json").read_text(encoding="utf-8"))
        self.assertIn("win_prob", report)
        self.assertEqual(bw.default_out(2026, 7, "sunday").relative_to(bw.REPO_ROOT).as_posix(), "league_data/2026_week07_sunday.json")

    def test_a_build_error_is_exit_code_2_and_writes_nothing(self):
        out = self.dir / "x.json"
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as se:
            rc = bw.main(["--season", "2026", "--week", "7", "--stage", "sunday", "--db", str(self.db), "--raw-dir", str(self.raw),
                          "--out", str(out), "--as-of", "2026-10-25T14:00:00+00:00"], injury_fetch=fetched())
        self.assertEqual(rc, 2)
        self.assertIn("NOT BUILT", se.getvalue())
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
