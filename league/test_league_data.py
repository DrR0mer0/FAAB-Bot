#!/usr/bin/env python3
"""Tests for the Yahoo league data layer. From the repo root:

    python -m unittest discover -s league -v

Stdlib unittest only. Every league, team and manager here is made up, the
"Yahoo" answering is a stand-in session, and everything is written to temp
dirs / in-memory SQLite -- never the real DB, raw/ or league_data/.
"""
import contextlib
import csv
import gzip
import hashlib
import io
import json
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

import league_data

KEY = "470.l.999"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bids_synthetic.csv"
NOW = datetime(2026, 10, 10, 13, 35, tzinfo=timezone.utc)
WEEKS = {1: ("2026-09-09", "2026-09-14"), 2: ("2026-09-15", "2026-09-21"), 3: ("2026-09-22", "2026-09-28"),
         4: ("2026-09-29", "2026-10-05"), 5: ("2026-10-06", "2026-10-12")}


def ts(s):
    return str(int(datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()))


def numbered(items):
    """A Python list the way Yahoo writes one: {"0": .., "count": n}, and [] when empty."""
    return dict({str(i): x for i, x in enumerate(items)}, count=len(items)) if items else []


def league(body, current_week=5):
    return {"fantasy_content": {"league": [{"league_key": KEY, "current_week": current_week, "start_week": "1", "end_week": "17"}, body]}}


def player(yid, name, pos, nfl, extra):
    return {"player": [[{"player_key": f"470.p.{yid}"}, {"player_id": str(yid)}, {"name": {"full": name}},
                        {"editorial_team_abbr": nfl}, {"display_position": pos}, []], extra]}


def team_info(tid, name=None, **extra):
    return [{"team_key": f"{KEY}.t.{tid}"}, {"team_id": str(tid)}, {"name": name or f"Team Name {tid}"}, [],
            *({k: v} for k, v in extra.items())]


def teams_payload(balances, mine=3, names=None):
    return league({"teams": numbered([{"team": [team_info(
        tid, (names or {}).get(tid), faab_balance=str(bal), waiver_priority=str(tid), number_of_moves="4", number_of_trades="0",
        managers=[{"manager": dict({"manager_id": str(tid), "nickname": f"Nick{tid}", "guid": f"GUID{tid}", "image_url": "http://img"},
                                   **({"is_commissioner": "1"} if tid == 1 else {}))}],
        **({"is_owned_by_current_login": 1} if tid == mine else {}))]} for tid, bal in balances.items()])})


def add(yid, name, team, source="freeagents", pos="RB"):
    return player(yid, name, pos, "Kc", {"transaction_data": [{"type": "add", "source_type": source, "destination_type": "team",
                                                               "destination_team_key": f"{KEY}.t.{team}",
                                                               "destination_team_name": f"Team Name {team}"}]})


def drop(yid, name, team, pos="WR"):
    return player(yid, name, pos, "Det", {"transaction_data": {"type": "drop", "source_type": "team", "destination_type": "waivers",
                                                               "source_team_key": f"{KEY}.t.{team}",
                                                               "source_team_name": f"Team Name {team}"}})


def traded(yid, name, src, dst):
    return player(yid, name, "TE", "Sf", {"transaction_data": [{"type": "trade", "source_type": "team", "destination_type": "team",
                                                               "source_team_key": f"{KEY}.t.{src}",
                                                               "destination_team_key": f"{KEY}.t.{dst}"}]})


def tx(tid, kind, when, players=None, **head):
    record = [dict({"transaction_key": f"{KEY}.tr.{tid}", "transaction_id": str(tid), "type": kind, "status": "successful",
                    "timestamp": ts(when)}, **head)]
    if players is not None:
        record.append({"players": dict(numbered(players), count=str(len(players)))})   # Yahoo sends this count as a string
    return {"transaction": record}


# Newest first, as Yahoo lists them. Ids 7 and 8 are missing: claims that failed in the 09-30 waiver run.
TRANSACTIONS = [
    tx(12, "add/drop", "2026-10-07T07:35:00", [add(112, "Late Claim", 3, "waivers"), drop(212, "Cut Twelve", 3)], faab_bid="0"),
    tx(11, "trade", "2026-10-03T18:00:00", [traded(111, "Trade Away", 1, 2), traded(311, "Trade Back", 2, 1)],
       trader_team_key=f"{KEY}.t.1", tradee_team_key=f"{KEY}.t.2"),
    tx(10, "drop", "2026-10-01T12:00:00", [drop(210, "Cut Ten", 2)]),
    tx(9, "add", "2026-09-30T07:36:00", [add(109, "Day After", 2)]),
    tx(6, "add", "2026-09-30T07:35:00", [add(106, "Big Claim", 3, "waivers", "QB")], faab_bid="27"),
    tx(5, "add/drop", "2026-09-30T07:35:00", [add(105, "Other Claim", 1, "waivers"), drop(205, "Cut Five", 1)], faab_bid="10"),
    tx(4, "commish", "2026-09-25T10:00:00"),
    tx(3, "add/drop", "2026-09-23T07:35:00", [add(103, "Week Three Claim", 3, "waivers"), drop(203, "Cut Three", 3)], faab_bid="16"),
    tx(2, "add", "2026-09-16T07:35:00", [add(102, "Too Old", 2, "waivers")], faab_bid="40"),     # week 2: outside the window
    tx(1, "add/drop", "2026-08-30T20:00:00", [add(101, "Preseason Add", 1), drop(201, "Preseason Cut", 1)]),
]
BALANCES = {1: 90, 2: 60, 3: 57, 4: 100}


def roster_payload():
    def spot(yid, name, pos, slot):
        return player(yid, name, pos, "Min", {"selected_position": [{"coverage_type": "week", "week": "5"}, {"position": slot}, {"is_flex": 0}]})
    return league({"teams": numbered([{"team": [team_info(tid), {"roster": {
        "coverage_type": "week", "week": "5", "is_editable": 0,
        "0": {"players": numbered([spot(tid * 1000 + 1, f"Starter {tid}", "QB", "QB"), spot(tid * 1000 + 2, f"Bench {tid}", "WR", "BN")])}}}]}
        for tid in BALANCES])})


# Every week the same two games: 1 beats 2, 4 beats 3 -- except week 2, when 3 and 4 tie. Weeks 1-4 are over.
SCORES = {w: {1: 100.5, 2: 90.25, 3: 80.0, 4: 80.0 if w == 2 else 120.5} for w in range(1, 6)}
FINAL_WEEKS = 4
SLOTS = (("QB", 1, 1), ("RB", 1, 1), ("WR", 1, 1), ("W/R/T", 1, 1), ("BN", 2, 0), ("IR", 1, 0))
SCORING = ((4, "Passing Yards", "Pass Yds", "0.04"), (5, "Passing Touchdowns", "Pass TD", "5"), (10, "Sacks", "Sack", "-.5"),
           (57, "Pick Sixes Thrown", "Pick Six", "-4"), (78, "Targets", "Targets", "0"))


def settings_payload():
    return league({"settings": [{
        "roster_positions": [{"roster_position": {"position": pos, "count": n, "is_starting_position": start}} for pos, n, start in SLOTS],
        "stat_categories": {"stats": [{"stat": {"stat_id": i, "name": name, "display_name": short}} for i, name, short, _v in SCORING]},
        "stat_modifiers": {"stats": [{"stat": {"stat_id": i, "value": value}} for i, _n, _s, value in SCORING]}}]})


def lineup(team, week, scores=SCORES, bench=None, delta=None):
    """[(yahoo id, name, position, slot, points)]: four starters whose points add up to the team's score (plus `delta`
    for that team-week, to make them NOT add up), two bench players, and for team 1 a player on IR."""
    total = scores[week][team]
    qb, rb, wr = round(total * 0.4, 2), round(total * 0.25, 2), round(total * 0.2, 2)
    base = team * 1000
    rows = [(base + 1, f"QB {team}", "QB", "QB", round(qb + (delta or {}).get((week, team), 0), 2)), (base + 2, f"RB {team}", "RB", "RB", rb),
            (base + 3, f"WR {team}", "WR", "WR", wr), (base + 4, f"Flex {team}", "WR", "W/R/T", round(total - qb - rb - wr, 2))]
    rows += (bench or {}).get((week, team), [(base + 5, f"Bench RB {team}", "RB", "BN", 1.0), (base + 6, f"Bench TE {team}", "TE", "BN", 2.0)])
    return rows + ([(base + 9, "Hurt QB 1", "QB", "IR", 30.0)] if team == 1 else [])


def lineup_payload(team, week, **kw):
    """One team's final lineup of a week, the way team/<key>/roster;week=N/players/stats answers."""
    cov = {"coverage_type": "week", "week": str(week)}
    players = [{"player": [[{"player_key": f"470.p.{yid}"}, {"player_id": str(yid)}, {"name": {"full": name}}, {"editorial_team_abbr": "Min"},
                            {"display_position": pos}, {"primary_position": pos}, []],
                           {"selected_position": [cov, {"position": slot}, {"is_flex": 0}]},
                           {"player_stats": {"0": cov, "stats": []}}, {"player_points": {"0": cov, "total": f"{pts:.2f}"}}]}
               for yid, name, pos, slot, pts in lineup(team, week, **kw)]
    return {"fantasy_content": {"team": [team_info(team), {"roster": dict(cov, **{"0": {"players": numbered(players)}})}]}}


def scoreboard_payload(weeks, scores=SCORES, final_weeks=FINAL_WEEKS):
    matchups = []
    for w in weeks:
        final = w <= final_weeks
        for a, b in ((1, 2), (3, 4)):
            pa, pb = scores[w][a], scores[w][b]
            side = lambda tid, pts: {"team": [team_info(tid), {"win_probability": 0.5, "team_points": {"coverage_type": "week", "total": f"{pts if final else 0:.2f}"},
                                                             "team_projected_points": {"coverage_type": "week", "total": "101.10"}}]}
            m = {"week": str(w), "week_start": WEEKS[w][0], "week_end": WEEKS[w][1], "status": "postevent" if final else "midevent",
                 "is_playoffs": "0", "is_consolation": "0", "0": {"teams": numbered([side(a, pa), side(b, pb)])}}
            if final:
                tied = pa == pb
                m.update(is_tied=1 if tied else 0, **({} if tied else {"winner_team_key": f"{KEY}.t.{a if pa > pb else b}"}))
            matchups.append({"matchup": m})
    return league({"scoreboard": {"week": ",".join(map(str, weeks)), "0": {"matchups": numbered(matchups)}}})


class Yahoo:
    """A stand-in session: answers the league routes, and each team's weekly lineup, from the fixtures above."""

    def __init__(self, transactions=TRANSACTIONS, fail=None, odd=None, scores=SCORES, final_weeks=FINAL_WEEKS, bench=None, delta=None,
                 team_names=None):
        self.transactions, self.fail, self.odd, self.paths = transactions, fail or {}, odd or {}, []
        self.team_names = team_names
        self.scores, self.final_weeks, self.lineup_kw = scores, final_weeks, dict(scores=scores, bench=bench, delta=delta)

    def get(self, url, timeout=None):
        if f"/team/{KEY}.t." in url:
            team, week = int(url.split(f"/team/{KEY}.t.")[1].split("/")[0]), int(url.split("roster;week=")[1].split("/")[0])
            self.paths.append(f"lineup t{team} w{week}")
            if "lineups" in self.fail:
                return mock.Mock(status_code=self.fail["lineups"], text="denied", json=lambda: {"error": {}})
            body = lineup_payload(team, week, **self.lineup_kw)
            return mock.Mock(status_code=200, text=json.dumps(body), json=lambda: body)
        path = url.split(f"/league/{KEY}/")[1].split("?")[0]
        self.paths.append(path)
        resource = "rosters" if path == "teams/roster" else path.split(";")[0]
        if resource in self.fail:
            return mock.Mock(status_code=self.fail[resource], text=f"denied for {KEY}", json=lambda: {"error": {}})
        if resource in self.odd:
            body = league(self.odd[resource])
        elif resource == "teams":
            body = teams_payload(BALANCES, names=self.team_names)
        elif resource == "transactions":
            opts = dict(p.split("=") for p in path.split(";")[1:])
            start, count = int(opts["start"]), int(opts["count"])
            body = league({"transactions": numbered(self.transactions[start:start + count])})
        elif resource == "rosters":
            body = roster_payload()
        elif resource == "settings":
            body = settings_payload()
        else:
            body = scoreboard_payload([int(w) for w in path.split("week=")[1].split(",")], self.scores, self.final_weeks)
        return mock.Mock(status_code=200, text=json.dumps(body), json=lambda: body)


class LeagueDataCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        league_data.init_schema(self.con)
        self.con.execute("CREATE TABLE ref_players (player_id TEXT PRIMARY KEY, full_name TEXT, pos TEXT)")
        self.con.execute("INSERT INTO ref_players VALUES ('g-ref', 'Day After', 'RB')")
        self.roster = self.dir / "roster_weekly_2026.csv"
        with open(self.roster, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["gsis_id", "full_name", "position", "yahoo_id"])
            w.writeheader()
            w.writerow(dict(gsis_id="g-id", full_name="Renamed Since", position="QB", yahoo_id="106"))
            w.writerow(dict(gsis_id="g-name", full_name="Late Claim Jr.", position="RB", yahoo_id=""))

    def tearDown(self):
        self.con.close()
        self.tmp.cleanup()

    def snapshot(self, session=None, key=KEY, now=NOW, **fetch_kw):
        session = session if session is not None else Yahoo()
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            out = league_data.take_snapshot(self.con, key, self.dir / "raw", self.roster, now=now,
                                            fetcher=lambda k, **kw: league_data.fetch(k, session=session, **kw, **fetch_kw))
        self.stderr = err.getvalue()
        return out

    def rows(self, sql, *args):
        return [tuple(r) for r in self.con.execute(sql, args)]


class TestSnapshot(LeagueDataCase):
    def test_every_resource_is_stored_with_its_raw_response_hashed(self):
        session = Yahoo()
        summary = self.snapshot(session)
        self.assertEqual({r: (s["ok"], s["n_rows"]) for r, s in summary.items()},
                         {"teams": (True, 4), "transactions": (True, 10), "rosters": (True, 8), "scoreboard": (True, 20),
                          "settings": (True, 6), "lineups": (True, 100)})       # 4 weeks x (4 teams x 6 players + one on IR)
        self.assertEqual(session.paths[:5], ["teams", "transactions;start=0;count=50", "teams/roster", "scoreboard;week=1,2,3,4,5",
                                             "settings"])
        self.assertEqual(session.paths[5:], [f"lineup t{t} w{w}" for w in (1, 2, 3, 4) for t in (1, 2, 3, 4)])   # week 5 isn't over
        snaps = self.con.execute("SELECT * FROM lg_snapshots ORDER BY snapshot_id").fetchall()
        self.assertEqual([(s["resource"], s["ok"], s["league_key"], s["batch_id"]) for s in snaps],
                         [(r, 1, KEY, NOW.isoformat()) for r in league_data.RESOURCES])
        for s in snaps:
            raw = gzip.decompress((self.dir / "raw" / s["raw_file"]).read_bytes())
            self.assertEqual(hashlib.sha256(raw).hexdigest(), s["raw_sha256"])
            self.assertEqual(json.loads(raw)["resource"], s["resource"])       # the pages exactly as Yahoo sent them
        self.assertEqual(self.stderr, "")

    def test_teams_keep_faab_and_names_but_never_a_guid_or_image(self):
        self.snapshot()
        self.assertEqual(self.rows("SELECT team_id, team_name, manager_nickname, is_mine, is_commissioner, faab_balance, waiver_priority, "
                                   "number_of_moves FROM lg_teams ORDER BY team_id")[:3],
                         [(1, "Team Name 1", "Nick1", 0, 1, 90, 1, 4), (2, "Team Name 2", "Nick2", 0, 0, 60, 2, 4),
                          (3, "Team Name 3", "Nick3", 1, 0, 57, 3, 4)])
        dump = "\n".join(self.con.iterdump())
        self.assertNotIn("GUID", dump)
        self.assertNotIn("http://img", dump)

    def test_transactions_keep_the_winning_bid_and_where_each_player_came_from(self):
        self.snapshot()
        self.assertEqual(self.rows("SELECT transaction_id, type, faab_bid, trader_team_id, tradee_team_id, n_players, happened_at "
                                   "FROM lg_transactions WHERE transaction_id IN (4, 6, 11, 12) ORDER BY transaction_id"),
                         [(4, "commish", None, None, None, 0, "2026-09-25T10:00:00+00:00"),       # Yahoo gives no detail at all
                          (6, "add", 27, None, None, 1, "2026-09-30T07:35:00+00:00"),
                          (11, "trade", None, 1, 2, 2, "2026-10-03T18:00:00+00:00"),
                          (12, "add/drop", 0, None, None, 2, "2026-10-07T07:35:00+00:00")])       # a $0 winning bid is a bid, not NULL
        self.assertEqual(self.rows("SELECT transaction_id, seq, name, action, source_type, source_team_id, destination_type, "
                                   "destination_team_id FROM lg_transaction_players WHERE transaction_id IN (11, 12) ORDER BY 1, 2"),
                         [(11, 0, "Trade Away", "trade", "team", 1, "team", 2), (11, 1, "Trade Back", "trade", "team", 2, "team", 1),
                          (12, 0, "Late Claim", "add", "waivers", None, "team", 3), (12, 1, "Cut Twelve", "drop", "team", 3, "waivers", None)])
        # Yahoo player -> gsis id: by Yahoo id, then name on the weekly roster, then ref_players; otherwise stored unmatched
        self.assertEqual(self.rows("SELECT name, player_id, match_method FROM lg_transaction_players WHERE name IN "
                                   "('Big Claim', 'Late Claim', 'Day After', 'Cut Ten') ORDER BY name"),
                         [("Big Claim", "g-id", "yahoo_id"), ("Cut Ten", None, None), ("Day After", "g-ref", "ref_name"),
                          ("Late Claim", "g-name", "roster_name")])

    def test_transactions_are_paged_and_one_that_lands_mid_fetch_is_kept_once(self):
        class Shifting(Yahoo):       # a new transaction appears after the first page: every later page repeats one record
            def get(self, url, timeout=None):
                resp = super().get(url, timeout)
                if "transactions;start=0" in url:
                    self.transactions = [tx(13, "drop", "2026-10-08T09:00:00", [drop(213, "Cut Thirteen", 4)])] + self.transactions
                return resp
        session = Shifting()
        with mock.patch.object(league_data, "TX_PAGE", 4):
            summary = self.snapshot(session)
        self.assertEqual([p for p in session.paths if p.startswith("transactions")],
                         [f"transactions;start={s};count=4" for s in (0, 4, 8)])
        self.assertTrue(summary["transactions"]["ok"])
        self.assertEqual(self.rows("SELECT COUNT(*), COUNT(DISTINCT transaction_id) FROM lg_transactions"), [(10, 10)])

    def test_yahoo_player_status_is_kept_with_each_roster_row(self):
        """'' = Yahoo gives no status (available); a code (Q, IR, CEL, ...) as Yahoo sends it; NULL only in older snapshots."""
        payload = roster_payload()
        teams = payload["fantasy_content"]["league"][1]["teams"]
        first = teams["0"]["team"][1]["roster"]["0"]["players"]["0"]["player"][0]
        first.insert(-1, {"status": "CEL", "status_full": "Commissioner Exempt List"})
        rows = league_data.parse_rosters([payload])
        self.assertEqual([(r["name"], r["status"]) for r in rows if r["team_id"] == 1], [("Starter 1", "CEL"), ("Bench 1", "")])
        self.snapshot()
        self.assertEqual(self.rows("SELECT DISTINCT status FROM lg_rosters"), [("",)])
        old = sqlite3.connect(":memory:")                    # a DB created before the column existed gains it, empty
        self.addCleanup(old.close)
        old.execute("CREATE TABLE lg_rosters (snapshot_id INTEGER NOT NULL, week INTEGER, team_id INTEGER NOT NULL, external_id TEXT "
                    "NOT NULL, player_id TEXT, match_method TEXT, name TEXT, position TEXT, team TEXT, selected_position TEXT, "
                    "PRIMARY KEY (snapshot_id, team_id, external_id))")
        old.execute("INSERT INTO lg_rosters (snapshot_id, team_id, external_id) VALUES (1, 1, 'x')")
        league_data.init_schema(old)
        self.assertEqual(old.execute("SELECT status FROM lg_rosters").fetchall(), [(None,)])

    def test_rosters_and_matchups(self):
        self.snapshot()
        self.assertEqual(self.rows("SELECT week, team_id, name, position, team, selected_position FROM lg_rosters WHERE team_id=2 ORDER BY name"),
                         [(5, 2, "Bench 2", "WR", "MIN", "BN"), (5, 2, "Starter 2", "QB", "MIN", "QB")])
        self.assertEqual(self.rows("SELECT week, team_id, opponent_team_id, status, is_tied, winner_team_id, points, week_start, week_end "
                                   "FROM lg_matchups WHERE team_id=3 AND week IN (2, 4, 5) ORDER BY week"),
                         [(2, 3, 4, "postevent", 1, None, 80.0, "2026-09-15", "2026-09-21"),       # a tie has no winner
                          (4, 3, 4, "postevent", 0, 4, 80.0, "2026-09-29", "2026-10-05"),
                          (5, 3, 4, "midevent", 0, None, 0.0, "2026-10-06", "2026-10-12")])        # in progress: no final score yet

    def test_every_table_is_append_only_and_every_run_adds_a_full_picture(self):
        self.snapshot()
        self.snapshot(now=NOW.replace(day=11))
        self.assertEqual(self.rows("SELECT resource, COUNT(*) FROM lg_snapshots GROUP BY 1 ORDER BY 1"),
                         [(r, 2) for r in sorted(league_data.RESOURCES)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_transactions"), [(20,)])
        self.assertTrue(league_data.import_bids(self.con, FIXTURE, self.dir / "raw", now=NOW)["ok"])
        for table in league_data.TABLES:
            column = self.con.execute(f"PRAGMA table_info({table})").fetchall()[1]["name"]
            self.assertTrue(self.rows(f"SELECT 1 FROM {table} LIMIT 1"), table)       # a trigger only fires on a row
            for sql in (f"UPDATE {table} SET {column} = {column}", f"DELETE FROM {table}"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError) as cm:
                    self.con.execute(sql)
                self.assertIn("append-only", str(cm.exception))


class TestLineupsAndSettings(LeagueDataCase):
    def test_final_lineups_carry_each_players_slot_and_points_and_starters_add_up_to_the_score(self):
        self.snapshot()
        lineups = league_data.week_lineups(self.con, 3)
        self.assertEqual(sorted(lineups), [1, 2, 3, 4])
        self.assertEqual([(r["name"], r["position"], r["selected_position"], r["points"]) for r in sorted(lineups[1], key=lambda r: r["external_id"])],
                         [("QB 1", "QB", "QB", 40.2), ("RB 1", "RB", "RB", 25.12), ("WR 1", "WR", "WR", 20.1), ("Flex 1", "WR", "W/R/T", 15.08),
                          ("Bench RB 1", "RB", "BN", 1.0), ("Bench TE 1", "TE", "BN", 2.0), ("Hurt QB 1", "QB", "IR", 30.0)])
        for team, rows in lineups.items():
            starters = sum(r["points"] for r in rows if r["selected_position"] not in league_data.BENCH_SLOTS)
            self.assertAlmostEqual(starters, SCORES[3][team], places=2)
        self.assertEqual(league_data.week_lineups(self.con, 5), {})            # week 5 is still being played
        self.assertEqual(league_data.lineup_weeks_stored(self.con), {1, 2, 3, 4})

    def test_first_run_backfills_then_only_the_latest_completed_week_is_fetched_again(self):
        self.snapshot()
        later = Yahoo(delta={(4, 2): -0.7})                                   # a stat correction to week 4, after the fact
        self.snapshot(later, now=NOW.replace(day=11))
        self.assertEqual([p for p in later.paths if p.startswith("lineup")], [f"lineup t{t} w4" for t in (1, 2, 3, 4)])
        self.assertEqual(self.rows("SELECT n_pages, n_rows FROM lg_snapshots WHERE resource='lineups' ORDER BY snapshot_id"), [(16, 100), (4, 25)])
        qb = [r for r in league_data.week_lineups(self.con, 4)[2] if r["name"] == "QB 2"][0]
        self.assertEqual(qb["points"], 35.4)                                   # the newest snapshot of that week is the one read
        self.assertEqual(len(league_data.week_lineups(self.con, 1)), 4)        # earlier weeks still come from the first run
        nxt = Yahoo(final_weeks=5)                                             # a week ends: it is fetched, week 4 is not
        self.snapshot(nxt, now=NOW.replace(day=13))
        self.assertEqual([p for p in nxt.paths if p.startswith("lineup")], [f"lineup t{t} w5" for t in (1, 2, 3, 4)])

    def test_lineups_need_the_teams_and_the_scoreboard_and_fail_on_their_own(self):
        summary = self.snapshot(Yahoo(fail={"teams": 500}))
        self.assertFalse(summary["lineups"]["ok"])
        self.assertIn("needs the teams and the scoreboard", summary["lineups"]["error"])
        self.assertTrue(summary["scoreboard"]["ok"] and summary["settings"]["ok"])
        summary = self.snapshot(Yahoo(fail={"lineups": 503}), now=NOW.replace(day=11))
        self.assertEqual({r for r, v in summary.items() if not v["ok"]}, {"lineups"})
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_lineups"), [(0,)])
        self.assertNotIn(KEY, summary["lineups"]["error"])

    def test_settings_keep_the_starting_slots_and_every_scoring_rule(self):
        self.snapshot()
        self.assertEqual(league_data.roster_slots(self.con), [("QB", 1), ("RB", 1), ("WR", 1), ("W/R/T", 1)])     # not BN, not IR
        self.assertEqual(self.rows("SELECT name, value FROM lg_scoring_rules ORDER BY stat_id"),
                         [("Passing Yards", 0.04), ("Passing Touchdowns", 5.0), ("Sacks", -0.5), ("Pick Sixes Thrown", -4.0), ("Targets", 0.0)])

    def test_scoring_check_names_every_rule_the_tracker_has_wrong_and_the_gap_it_knows_about(self):
        self.assertFalse(league_data.scoring_check(self.con, {})["ok"])                     # no snapshot yet
        self.snapshot()
        good = {"pass_yd": 0.04, "pass_td": 5, "sack": -0.5}
        check = league_data.scoring_check(self.con, good)
        self.assertEqual((check["ok"], check["lines"]), (True, []))
        self.assertEqual(len(check["unsupported"]), 1)
        self.assertIn("Pick Sixes Thrown (-4): not scored by the tracker", check["unsupported"][0])
        check = league_data.scoring_check(self.con, {"pass_yd": 0.04, "pass_td": 4, "rush_td": 6})
        self.assertFalse(check["ok"])
        self.assertEqual(sorted(check["lines"]), ["pass_td: Yahoo 5, tracker 4", "rush_td: tracker 6, but Yahoo doesn't score it",
                                                  "sack: Yahoo -0.5, tracker has no rule"])

    def test_points_check_compares_yahoos_player_points_with_the_trackers(self):
        with open(self.roster, "a", newline="", encoding="utf-8") as f:              # two lineup players the tracker can find
            w = csv.DictWriter(f, fieldnames=["gsis_id", "full_name", "position", "yahoo_id"])
            w.writerow(dict(gsis_id="g-qb1", full_name="QB 1", position="QB", yahoo_id="1001"))
            w.writerow(dict(gsis_id="g-rb1", full_name="RB 1", position="RB", yahoo_id="1002"))
        self.snapshot()
        store = mock.Mock()
        store.week_rows.side_effect = lambda season, week: {"g-qb1": {"points": 40.2}, "g-rb1": {"points": 24.12}}
        got = league_data.points_check(self.con, store, 2026, weeks=[3])
        self.assertEqual((got["n"], got["same"]), (25, 1))                          # QB 1 agrees; everyone else is off or unknown
        self.assertIn((3, "RB 1", "RB", 25.12, 24.12), got["differ"])

    def test_nothing_is_hardcoded_to_my_team(self):
        self.snapshot()
        self.assertEqual(league_data.default_team(self.con), 3)                     # a default: the logged-in account's team
        self.assertTrue(league_data.import_bids(self.con, FIXTURE, self.dir / "raw", now=NOW)["ok"])
        mine = league_data.build_report(self.con, today=date(2026, 10, 10))
        other = league_data.build_report(self.con, today=date(2026, 10, 10), team_id=1)
        self.assertIn("## Team Name 3 (me)", mine)
        self.assertIn("## Team Name 1 (me)", other)
        self.assertNotIn("Team Name 3 (me)", other)
        self.assertEqual([ln for ln in other.split("## My winning bids")[1].split("\n## ")[0].splitlines() if ln.startswith("- ")],
                         ["- Wed 09-30 -- Other Claim (RB, KC): won at $10, next offer $3 (Team Name 4) -- **margin $7**"])
        tx = league_data.latest_ok(self.con, "transactions")[0]
        weeks = league_data.league_weeks(self.con, league_data.latest_ok(self.con, "scoreboard")[0])
        _f, _c, start, end = league_data.report_window(weeks, date(2026, 10, 10))
        activity = league_data.team_activity(self.con, tx, start, end)
        offers = league_data.losing_bids(self.con, tx, start, end)[0]
        self.assertEqual([(m["won_at"], m["next_offer"], m["next_team_id"], m["margin"]) for m in league_data.bid_margins(activity, offers, 3)],
                         [(16, 16, 2, 0), (27, 20, 1, 7), (0, None, None, None)])
        self.assertEqual(league_data.bid_margins(activity, offers, 4), [])          # a team that won nothing


class TestFailures(LeagueDataCase):
    def test_one_resource_failing_is_recorded_and_does_not_cost_the_others(self):
        summary = self.snapshot(Yahoo(fail={"rosters": 500}))
        self.assertEqual({r: s["ok"] for r, s in summary.items()},
                         dict(teams=True, transactions=True, rosters=False, scoreboard=True, settings=True, lineups=True))
        snap = self.con.execute("SELECT * FROM lg_snapshots WHERE resource='rosters'").fetchone()
        self.assertEqual((snap["ok"], snap["http_status"], snap["n_rows"], snap["error"]), (0, 500, 0, "RuntimeError: HTTP 500 on teams/roster"))
        self.assertIsNotNone(snap["raw_sha256"])            # what Yahoo did answer is kept
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_rosters"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_teams"), [(4,)])
        self.assertIn("Yahoo rosters snapshot FAILED", self.stderr)

    def test_an_unknown_shape_is_a_failed_snapshot_not_a_crash_or_half_a_table(self):
        summary = self.snapshot(Yahoo(odd={"transactions": {"transactions": numbered([{"transaction": [{"transaction_id": "1"}]}])}}))
        self.assertFalse(summary["transactions"]["ok"])
        self.assertIn("unexpected response shape (KeyError", summary["transactions"]["error"])
        self.assertEqual(self.rows("SELECT ok FROM lg_snapshots WHERE resource='transactions'"), [(0,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_transactions"), [(0,)])
        self.assertTrue(summary["scoreboard"]["ok"])

    def test_the_league_key_never_reaches_an_error_message(self):
        boom = mock.Mock()
        boom.get.side_effect = ConnectionError(f"Max retries exceeded with url: /fantasy/v2/league/{KEY}/teams")
        summary = self.snapshot(boom)
        for r in league_data.RESOURCES:
            self.assertFalse(summary[r]["ok"])
            self.assertNotIn(KEY, summary[r]["error"])
        self.assertNotIn(KEY, self.stderr)
        self.assertEqual(self.rows("SELECT COUNT(*), SUM(ok) FROM lg_snapshots"), [(6, 0)])

    def test_no_credentials_or_no_league_key_is_a_recorded_failure(self):
        def no_credentials():
            raise SystemExit("[ERROR] YAHOO_CLIENT_ID / YAHOO_CLIENT_SECRET not set")
        with mock.patch("sys.stderr"):
            out = league_data.take_snapshot(self.con, KEY, self.dir / "raw", self.roster, now=NOW,
                                            fetcher=lambda k, **kw: league_data.fetch(k, open_session=no_credentials, **kw))
        self.assertTrue(all("SystemExit" in s["error"] for s in out.values()))
        never = mock.Mock(side_effect=AssertionError("must not be fetched"))
        for key, why in ((None, "no league is configured"), ("470.1.123456", "not a Yahoo league key")):    # digit one, not an L
            with self.subTest(key=key), mock.patch("sys.stderr"):
                out = league_data.take_snapshot(self.con, key, self.dir / "raw", self.roster, now=NOW, fetcher=never)
                self.assertTrue(all(not s["ok"] and why in s["error"] for s in out.values()))

    def test_a_hang_is_cut_off(self):
        release = threading.Event()
        try:
            with mock.patch.object(league_data, "REQUEST_TIMEOUT_S", 0.05):
                got = league_data.fetch(KEY, open_session=lambda: release.wait(30), budget_s=0.05)
            self.assertTrue(all(not g["ok"] and "no answer from Yahoo" in g["error"] for g in got.values()))
        finally:
            release.set()

    def test_cli_exit_code_tells_a_scheduler_that_something_failed(self):
        db = self.dir / "t.db"
        for session, code, word in ((Yahoo(), 0, "rosters ok: 8 rows"), (Yahoo(fail={"rosters": 500}), 1, "rosters FAILED")):
            out = io.StringIO()
            with mock.patch.object(league_data, "fetch", side_effect=lambda k, s=session, **kw: league_data._fetch(k, s, None, 1e18, **kw)), \
                    mock.patch("sys.stderr"), contextlib.redirect_stdout(out):
                rc = league_data.main(["snapshot", "--db", str(db), "--raw-dir", str(self.dir / "raw"), "--league-key", KEY,
                                       "--roster", str(self.roster)])
            self.assertEqual(rc, code)
            self.assertIn(word, out.getvalue())
            self.assertNotIn("Nick", out.getvalue())          # the summary line names nobody


class TestReport(LeagueDataCase):
    def test_window_is_the_current_league_week_and_the_two_before_by_yahoos_boundaries(self):
        weeks = {w: (date.fromisoformat(a), date.fromisoformat(b)) for w, (a, b) in WEEKS.items()}
        first, current, start, end = league_data.report_window(weeks, date(2026, 10, 10))
        self.assertEqual((first, current), (3, 5))
        self.assertEqual((start.isoformat(), end.isoformat()), ("2026-09-22T00:00:00-07:00", "2026-10-13T00:00:00-07:00"))
        self.assertEqual(league_data.report_window(weeks, date(2026, 9, 16))[:2], (1, 2))      # early season: fewer weeks exist
        self.assertEqual(league_data.report_window(weeks, date(2026, 9, 1))[:2], (1, 1))       # before week 1
        self.assertEqual(league_data.report_window(weeks, date(2026, 12, 1))[:2], (3, 5))      # past the last week known

    def test_report_lists_each_teams_window_activity_faab_and_the_labelled_bidder_flag(self):
        self.snapshot()
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("# League activity: weeks 3-5", md)
        self.assertIn("LOCAL ONLY", md)
        self.assertIn("Basis: **winning waiver claims only -- no losing offers have been entered for this window.**", md)
        self.assertIn("Transaction ids in the window that Yahoo's list skips: 2 ", md)      # ids 7 and 8, between 6 and 9
        table = report_table(md)
        #                                  manager  left    season  won  lost  paid  typical  window  FA   drops trades active  funded
        self.assertEqual(table["Team Name 3 (me)"], ["Nick3", "$57", "$43", "3", "n/a", "2", "$21.5", "$43", "0", "2", "0", "**yes**", "**yes**"])  # a $0 claim is a bid, not a paid one
        self.assertEqual(table["Team Name 1"], ["Nick1", "$90", "$10", "1", "n/a", "1", "$10", "$10", "0", "1", "1", "no", "no"])
        self.assertEqual(table["Team Name 2"], ["Nick2", "$60", "$40", "0", "n/a", "0", "-", "$0", "1", "1", "1", "no", "no"])           # its $40 claim was in week 2
        self.assertEqual(table["Team Name 4"], ["Nick4", "$100", "$0", "0", "n/a", "0", "-", "$0", "0", "0", "0", "no", "no"])
        margins = md.split("## My winning bids")[1].split("\n## ")[0]
        self.assertEqual(margins.count("no losing offer entered (uncontested, or not typed in yet)"), 3)
        self.assertNotIn("## Check these entries", md)
        mine = md.split("## Team Name 3 (me)")[1].split("\n## ")[0]
        self.assertEqual([ln for ln in mine.splitlines() if ln.startswith("- ")],
                         ["- Wed 09-23 -- waiver claim, $16: +Week Three Claim (RB, KC) / -Cut Three (WR, DET)",
                          "- Wed 09-30 -- waiver claim, $27: +Big Claim (QB, KC)",
                          "- Wed 10-07 -- waiver claim, $0: +Late Claim (RB, KC) / -Cut Twelve (WR, DET)"])
        self.assertIn("- Sat 10-03 -- trade: got Trade Back (TE, SF) / gave Trade Away (TE, SF)", md.split("## Team Name 1")[1].split("\n## ")[0])
        self.assertIn("No transactions in the window.", md.split("## Team Name 4")[1])
        self.assertNotIn("Preseason", md)
        self.assertNotIn("Too Old", md)
        self.assertNotIn(KEY, md)

    def test_report_reads_the_newest_good_snapshot_and_needs_one_of_each(self):
        self.assertIsNone(league_data.build_report(self.con, today=date(2026, 10, 10)))
        self.snapshot()
        later = [tx(14, "add", "2026-10-09T07:35:00", [add(114, "Newest Claim", 4, "waivers")], faab_bid="5")] + TRANSACTIONS
        self.snapshot(Yahoo(transactions=later), now=NOW.replace(day=11))
        self.snapshot(Yahoo(fail={"transactions": 503}), now=NOW.replace(day=12))      # a failed run must not blank the report
        md = league_data.build_report(self.con, today=date(2026, 10, 12))
        self.assertIn("+Newest Claim", md)
        self.assertIn("transactions as of 2026-10-11 13:35 UTC, FAAB balances as of 2026-10-12 13:35 UTC", md)


def report_table(md):
    return {ln.split("|")[1].strip(): [c.strip() for c in ln.split("|")[2:-1]] for ln in md.splitlines() if ln.startswith("| Team Name")}


class TestBids(LeagueDataCase):
    """Losing FAAB offers typed into a CSV by hand (Yahoo's API doesn't have them)."""

    HEADER = "award_date,player,winning_team,winning_amount,losing_team,losing_amount\n"

    def setUp(self):
        super().setUp()
        self.snapshot()
        self.n = 0

    def bids(self, body=None, now=NOW):
        """Import the fixture, or a file made of the header plus `body`."""
        path = FIXTURE
        if body is not None:
            self.n += 1
            path = self.dir / "bids.csv"
            path.write_text(f"# save {self.n}\n" + self.HEADER + body, encoding="utf-8")
        with mock.patch("sys.stderr"):
            return league_data.import_bids(self.con, path, self.dir / "raw", now=now)

    def current(self):
        return sorted((b["award_date"], b["player"], b["losing_team_id"], b["losing_amount"]) for b in league_data.current_bids(self.con))

    FIXTURE_ROWS = ("2026-09-30,Big Claim,Team Name 3,27,Team Name 1,20\n2026-09-30,Big Claim,Team Name 3,27,Team Name 4,5\n"
                    "2026-09-23,Week Three Claim,Team Name 3,16,Team Name 2,16\n2026-09-29,Other Claim,Team Name 1,10,Team Name 4,3\n"
                    "2026-09-16,Too Old,Team Name 2,40,Team Name 1,39\n")

    def test_fixture_is_read_stored_and_each_award_is_linked_to_yahoos_transaction(self):
        out = self.bids()
        self.assertEqual((out["ok"], out["n_rows"], out["n_new"], out["rejected"], out["flagged"]), (True, 5, 5, [], []))
        self.assertEqual(self.rows("SELECT award_date, player, winning_team_id, winning_amount, losing_team_id, losing_amount, "
                                   "transaction_id, api_check, change FROM lg_bids ORDER BY bid_id"),
                         [("2026-09-30", "Big Claim", 3, 27, 1, 20, 6, "ok", "new"),
                          ("2026-09-30", "Big Claim", 3, 27, 4, 5, 6, "ok", "new"),
                          ("2026-09-23", "Week Three Claim", 3, 16, 2, 16, 3, "ok", "new"),   # M/D/YYYY, "$16", a nickname, a tie
                          ("2026-09-29", "Other Claim", 1, 10, 4, 3, 5, "ok", "new"),         # typed a day early: still the 09-30 award
                          ("2026-09-16", "Too Old", 2, 40, 1, 39, 2, "ok", "new")])
        self.assertEqual(self.rows("SELECT player_id FROM lg_bids WHERE player='Big Claim'"), [("g-id",), ("g-id",)])   # gsis id, via the link
        imp = self.con.execute("SELECT * FROM lg_bid_imports").fetchone()
        self.assertEqual((imp["ok"], imp["n_rows"], imp["n_new"], imp["n_rejected"], imp["n_flagged"], imp["file_name"]),
                         (1, 5, 5, 0, 0, "bids_synthetic.csv"))
        self.assertEqual(imp["file_sha256"], hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        self.assertEqual(gzip.decompress((self.dir / "raw" / imp["raw_file"]).read_bytes()), FIXTURE.read_bytes())

    def test_importing_again_adds_nothing_whether_or_not_the_file_itself_changed(self):
        self.bids()
        self.assertTrue(self.bids()["unchanged_file"])                       # the very same file: not even an import row
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_bid_imports"), [(1,)])
        out = self.bids(self.FIXTURE_ROWS)                                   # the same offers, written differently
        self.assertEqual((out["n_rows"], out["n_new"], out["n_unchanged"], out["n_corrected"], out["n_withdrawn"]), (5, 0, 5, 0, 0))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_bids"), [(5,)])
        out = self.bids(self.FIXTURE_ROWS + "2026-10-07,Late Claim,Team Name 3,0,Team Name 2,0\n")    # next week's save, one more offer
        self.assertEqual((out["n_new"], out["n_unchanged"]), (1, 5))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_bids"), [(6,)])

    def test_a_changed_row_is_a_reported_correction_and_the_newest_version_is_used(self):
        self.bids()
        out = self.bids(self.FIXTURE_ROWS.replace("Team Name 1,20", "Team Name 1,21"))
        self.assertEqual((out["n_corrected"], out["n_new"], out["n_unchanged"]), (1, 0, 4))
        self.assertEqual(out["corrections"], ["line 3: Big Claim (2026-09-30) -- losing offer $20 -> $21, winning $27 -> $27"])
        self.assertIn(("2026-09-30", "Big Claim", 1, 21), self.current())
        self.assertNotIn(("2026-09-30", "Big Claim", 1, 20), self.current())
        self.assertEqual(self.rows("SELECT losing_amount, change FROM lg_bids WHERE player='Big Claim' AND losing_team_id=1 ORDER BY bid_id"),
                         [(20, "new"), (21, "corrected")])                   # nothing was overwritten

    def test_a_row_taken_out_of_the_file_is_withdrawn_not_deleted(self):
        self.bids()
        out = self.bids(self.FIXTURE_ROWS.replace("2026-09-30,Big Claim,Team Name 3,27,Team Name 4,5\n", ""))
        self.assertEqual((out["n_withdrawn"], out["n_unchanged"]), (1, 4))
        self.assertEqual(out["withdrawn"], ["Big Claim (2026-09-30) -- a $5 losing offer is no longer in the file"])
        self.assertEqual(len(self.current()), 4)
        self.assertEqual(self.rows("SELECT change FROM lg_bids WHERE player='Big Claim' AND losing_team_id=4 ORDER BY bid_id"),
                         [("new",), ("withdrawn",)])
        self.assertEqual(self.bids(self.FIXTURE_ROWS)["n_new"], 1)           # typed back in: current again
        self.assertEqual(len(self.current()), 5)

    def test_an_award_that_does_not_match_yahoos_record_is_flagged(self):
        out = self.bids("2026-09-30,Big Claim,Team Name 3,25,Team Name 1,20\n"          # Yahoo has $27
                        "2026-09-30,Other Claim,Team Name 2,10,Team Name 4,3\n"         # Yahoo has team 1
                        "2026-09-30,Nobody Claimed,Team Name 2,8,Team Name 4,1\n"       # no such waiver add
                        "2026-10-07,Late Claim,Team Name 3,0,Team Name 4,0\n")          # fine
        self.assertEqual(out["rejected"], [])
        self.assertEqual(out["flagged"], [
            "line 3: Big Claim (2026-09-30) -- winner amount differs: typed $25, Yahoo has $27",
            "line 4: Other Claim (2026-09-30) -- winner team differs: typed $10, Yahoo has $10 and a different winning team",
            "line 5: Nobody Claimed (2026-09-30) -- no waiver add for this player within a day of that date in Yahoo's transactions"])
        self.assertEqual(self.rows("SELECT player, api_check, transaction_id FROM lg_bids ORDER BY bid_id"),      # stored, with the verdict
                         [("Big Claim", "winner_amount_differs", 6), ("Other Claim", "winner_team_differs", 5),
                          ("Nobody Claimed", "no_api_transaction", None), ("Late Claim", "ok", 12)])
        self.assertNotIn("Team Name", " ".join(out["flagged"]))             # what gets printed names no team
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        checks = md.split("## Check these entries in bids.csv")[1].split("\n## ")[0]
        self.assertIn("- 2026-09-30 Big Claim: typed Team Name 3 for $25; Yahoo has Team Name 3 for $27", checks)
        self.assertIn("- 2026-09-30 Other Claim: typed Team Name 2 for $10; Yahoo has Team Name 1 for $10", checks)
        self.assertIn("- 2026-09-30 Nobody Claimed: typed Team Name 2 for $8; Yahoo's transactions have no waiver add", checks)
        self.assertNotIn("Late Claim", checks)
        self.assertIn("CHECK: the typed winner doesn't match Yahoo's", md.split("## My winning bids")[1].split("\n## ")[0])

    def test_unreadable_rows_are_rejected_with_their_line_numbers_and_the_rest_still_lands(self):
        self.bids()
        out = self.bids("2026-09-30,Big Claim,Team Name 3,27,Team Name 1,20\n"          # 3  fine, already stored
                        "2026-09-30,Big Claim,Team Name 3,27,The Unknowns,5\n"          # 4  no such team
                        "30 Sept,Big Claim,Team Name 3,27,Team Name 2,5\n"              # 5  not a date
                        "2026-09-30,Big Claim,Team Name 3,27,Team Name 2,thirty\n"      # 6  not an amount
                        "2026-09-30,Big Claim,Team Name 3,27,Team Name 2,28\n"          # 7  loser above winner
                        "2026-09-30,Big Claim,Team Name 3,27,Team Name 3,5\n"           # 8  bidding against itself
                        "2026-09-30,Big Claim,Team Name 3,27,Team Name 1,19\n"          # 9  same offer twice
                        "2026-09-23,Week Three Claim,Team Name 3,16,Team Name 2,9\n"    # 10 \\ one award,
                        "2026-09-23,Week Three Claim,Team Name 3,17,Team Name 4,9\n"    # 11 /  two winning amounts
                        ",,,,,\n"                                                       # blank: ignored
                        "2026-10-07,Late Claim,Team Name 3,0,Team Name 2,0\n")          # 13 fine, new
        self.assertEqual([m.split(":")[0] for m in out["rejected"]], [f"line {n}" for n in (4, 5, 6, 7, 8, 9, 10, 11)])
        self.assertIn("losing team not recognised", out["rejected"][0])
        self.assertIn("the losing offer ($28) is above the winning one ($27)", out["rejected"][3])
        self.assertIn("the same offer is already on line 3", out["rejected"][5])
        self.assertIn("winning team/amount differs between its rows", out["rejected"][6])
        self.assertEqual((out["ok"], out["n_new"], out["n_unchanged"], out["n_withdrawn"]), (True, 1, 1, 0))
        self.assertEqual(len(self.current()), 6)             # nothing is called withdrawn while lines can't be read
        self.assertEqual(self.rows("SELECT n_rejected FROM lg_bid_imports ORDER BY import_id DESC LIMIT 1"), [(8,)])

    def test_a_file_saved_by_excel_with_a_utf8_byte_order_mark_reads_the_same(self):
        bom = b"\xef\xbb\xbf"
        path = self.dir / "excel.csv"       # the fixture as Excel's "CSV UTF-8" writes it: BOM first, CRLF line ends
        path.write_bytes(bom + FIXTURE.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        out = league_data.import_bids(self.con, path, self.dir / "raw", now=NOW)
        self.assertEqual((out["ok"], out["n_rows"], out["n_new"], out["rejected"], out["flagged"]), (True, 5, 5, [], []))
        # the hard case: no comment lines, so the mark sits directly in front of the header's first column name
        path.write_bytes(bom + (self.HEADER + self.FIXTURE_ROWS).encode("utf-8"))
        out = league_data.import_bids(self.con, path, self.dir / "raw", now=NOW)
        self.assertEqual((out["ok"], out["n_unchanged"], out["n_new"], out["rejected"]), (True, 5, 0, []))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM lg_bids"), [(5,)])
        text = (bom + (self.HEADER + self.FIXTURE_ROWS).encode("utf-8")).decode("utf-8")      # a caller that didn't strip it
        self.assertTrue(text.startswith("﻿"))
        self.assertEqual((len(league_data.read_bids_csv(text)[0]), league_data.read_bids_csv(text)[1]), (5, []))

    def test_a_blank_award_date_takes_the_date_of_the_row_above_only_for_the_same_award(self):
        out = self.bids("2026-09-30,Big Claim,Team Name 3,27,Team Name 1,20\n"      # 3
                        ",Big Claim,Team Name 3,27,Team Name 4,5\n"                # 4  same player and winner: 09-30 too
                        ",big claim,team name 3,$27,Team Name 2,1\n"               # 5  ...and again, however it is typed
                        ",Other Claim,Team Name 1,10,Team Name 4,3\n"              # 6  another award: a date is never guessed
                        ",Other Claim,Team Name 1,10,Team Name 2,2\n"              # 7  the row above has no date to give
                        "2026-10-07,Late Claim,Team Name 3,0,Team Name 2,0\n"      # 8
                        ",Late Claim,Team Name 3,1,Team Name 4,0\n"                # 9  same player, another winning amount
                        "10/7/2026,Late Claim,Team Name 3,0,Team Name 1,0\n"       # 10
                        ",Late Claim,Team Name 1,0,Team Name 4,0\n")               # 11 same player, another winning team
        self.assertEqual([m.split(":")[0] for m in out["rejected"]], ["line 6", "line 7", "line 9", "line 11"])
        self.assertTrue(all("award_date is blank, which is only allowed on a row that repeats the row above's player, winning team "
                            "and winning amount" in m for m in out["rejected"]))
        self.assertEqual((out["ok"], out["n_new"], out["flagged"]), (True, 5, []))     # the inherited date links to Yahoo's award too
        self.assertEqual(self.rows("SELECT award_date, player, losing_team_id, losing_amount, transaction_id, line_no FROM lg_bids "
                                   "ORDER BY bid_id"),
                         [("2026-09-30", "Big Claim", 1, 20, 6, 3), ("2026-09-30", "Big Claim", 4, 5, 6, 4),
                          ("2026-09-30", "big claim", 2, 1, 6, 5), ("2026-10-07", "Late Claim", 2, 0, 12, 8),
                          ("2026-10-07", "Late Claim", 1, 0, 12, 10)])
        # nothing above it at all
        rows, problems = league_data.read_bids_csv(self.HEADER + ",Big Claim,Team Name 3,27,Team Name 1,20\n")
        self.assertEqual((rows, [p.split(":")[0] for p in problems]), ([], ["line 2"]))
        # a comment or an empty line between two rows of one award doesn't break the chain
        rows, problems = league_data.read_bids_csv(self.HEADER + "2026-09-30,Big Claim,Team Name 3,27,Team Name 1,20\n"
                                                   "# two more on this one\n\n,Big Claim,Team Name 3,27,Team Name 4,5\n")
        self.assertEqual(([r["award_date"].isoformat() for r in rows], problems), (["2026-09-30", "2026-09-30"], []))

    def test_a_file_that_cannot_be_what_was_meant_changes_nothing(self):
        self.bids()
        for body, why in (("", "no data rows but 5 offer(s) are stored"),):
            out = self.bids(body)
            self.assertEqual((out["ok"], len(self.current())), (False, 5))
            self.assertIn(why, out["error"])
        path = self.dir / "headerless.csv"
        path.write_text("2026-09-30,Big Claim,Team Name 3,27,Team Name 1,20\n", encoding="utf-8")
        out = league_data.import_bids(self.con, path, self.dir / "raw", now=NOW)
        self.assertIn("the header must be award_date,player,winning_team", out["rejected"][0])
        self.assertEqual(len(self.current()), 5)
        self.assertEqual(self.rows("SELECT COUNT(*), SUM(ok) FROM lg_bid_imports"), [(3, 2)])      # both failures are on record
        empty = sqlite3.connect(":memory:")
        empty.row_factory = sqlite3.Row
        self.addCleanup(empty.close)
        league_data.init_schema(empty)
        self.assertIn("run `snapshot` first", league_data.import_bids(empty, FIXTURE, self.dir / "raw", now=NOW)["error"])

    # ---- a team renamed mid-season (2026-10-20: Yahoo stores the new name with a curly apostrophe)

    def rename_team_3(self):
        self.snapshot(Yahoo(team_names={3: "Team O\u2019Brien"}), now=NOW.replace(day=11))

    def test_a_renamed_team_answers_to_its_old_name_its_new_name_and_either_apostrophe(self):
        self.rename_team_3()
        idx = league_data.team_index(self.con)
        for typed in ("Team Name 3", "team name 3", "Team O\u2019Brien", "Team O'Brien", "TEAM  O'BRIEN", "Nick3"):
            self.assertEqual(idx.get(league_data.name_key(typed)), 3, typed)
        self.assertEqual(league_data.name_key("Who’s  Got  Next"), league_data.name_key("who's got next"))

    def test_rows_typed_before_and_after_a_rename_stay_with_the_same_team(self):
        self.bids(self.FIXTURE_ROWS)                                    # typed while the team was "Team Name 3"
        before = self.current()
        self.rename_team_3()
        out = self.bids(self.FIXTURE_ROWS.replace("Big Claim,Team Name 3,27", "Big Claim,Team O'Brien,27")
                        + "2026-10-07,Late Claim,Team O'Brien,0,Team Name 2,0\n")   # new name, typed with a plain apostrophe
        self.assertEqual((out["rejected"], out["n_new"], out["n_corrected"], out["n_withdrawn"]), ([], 1, 0, 0))
        self.assertEqual(self.current(), sorted(before + [("2026-10-07", "Late Claim", 2, 0)]))
        self.assertTrue(all(b["winning_team_id"] == 3 for b in league_data.current_bids(self.con)
                            if b["player"] in ("Big Claim", "Week Three Claim", "Late Claim")))
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("## Team O\u2019Brien (me)", md)                   # shown under the name Yahoo has now
        self.assertNotIn("Team Name 3", md)

    def test_report_counts_all_bids_and_shows_my_margin_over_the_next_offer(self):
        self.bids()
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("Basis: **all bids -- winning claims from Yahoo's API plus 4 hand-entered losing offer(s) on 3 award(s) in the "
                      "window (bids.csv, imported 2026-10-10; newest award entered 2026-09-30).", md)
        table = report_table(md)
        #                                  manager  left    season  won  lost  paid  typical  window  FA   drops trades active  funded
        self.assertEqual(table["Team Name 3 (me)"], ["Nick3", "$57", "$43", "3", "0", "2", "$21.5", "$43", "0", "2", "0", "**yes**", "**yes**"])
        self.assertEqual(table["Team Name 1"], ["Nick1", "$90", "$10", "1", "1", "2", "$15", "$10", "0", "1", "1", "**yes**", "**yes**"])  # one won + one lost
        self.assertEqual(table["Team Name 2"], ["Nick2", "$60", "$40", "0", "1", "1", "$16", "$0", "1", "1", "1", "no", "no"])
        self.assertEqual(table["Team Name 4"], ["Nick4", "$100", "$0", "0", "2", "2", "$4", "$0", "0", "0", "0", "**yes**", "**yes**"])  # never won, bid twice
        self.assertEqual([ln for ln in md.split("## My winning bids")[1].split("\n## ")[0].splitlines() if ln.startswith("- ")], [
            "- Wed 09-23 -- Week Three Claim (RB, KC): won at $16, next offer $16 (Team Name 2) -- **margin $0**",
            "- Wed 09-30 -- Big Claim (QB, KC): won at $27, next offer $20 (Team Name 1) -- **margin $7**, 2 losing offers",
            "- Wed 10-07 -- Late Claim (RB, KC): won at $0, no losing offer entered (uncontested, or not typed in yet)"])
        self.assertEqual([ln for ln in md.split("## Team Name 4")[1].splitlines() if ln.startswith("- ")],
                         ["- Wed 09-30 -- losing bid, $5: Big Claim (went to Team Name 3 for $27)",
                          "- Wed 09-30 -- losing bid, $3: Other Claim (went to Team Name 1 for $10)"])
        self.assertNotIn("Too Old", md)                      # its award was in week 2
        self.assertNotIn("## Check these entries", md)

    def test_zero_dollar_bids_are_interest_they_make_a_bidder_active_but_not_paid_or_funded(self):
        # Team 2 bids $0 twice; the $0 award (Late Claim) has a $0 next offer, so priority decided it. Week Three Claim's
        # winning amount is also mistyped as $0 (Yahoo: $16)
        self.bids(self.FIXTURE_ROWS.replace("Team Name 2,16\n", "Team Name 2,0\n").replace("Week Three Claim,Team Name 3,16",
                                                                                                "Week Three Claim,Team Name 3,0")
                  + "2026-10-07,Late Claim,Team Name 3,0,Team Name 2,0\n")
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("$0 bids included", md)
        table = report_table(md)
        #                                  manager  left    season  won  lost  paid  typical  window  FA   drops trades active  funded
        self.assertEqual(table["Team Name 2"], ["Nick2", "$60", "$40", "0", "2", "0", "-", "$0", "1", "1", "1", "**yes**", "no"])
        self.assertEqual(table["Team Name 3 (me)"][5:7], ["2", "$21.5"])     # Yahoo's $16 and $27; the $0 claim is left out
        margins = [ln for ln in md.split("## My winning bids")[1].split("\n## ")[0].splitlines() if ln.startswith("- ")]
        self.assertIn("- Wed 10-07 -- Late Claim (RB, KC): won at $0, next offer $0 (Team Name 2) -- decided by waiver priority", margins)
        self.assertNotIn("margin $0", "\n".join(ln for ln in margins if "Late Claim" in ln))
        mine = md.split("## Team Name 3 (me)")[1].split("\n## ")[0]
        self.assertIn("- Wed 10-07 -- waiver claim, $0: +Late Claim (RB, KC) / -Cut Twelve (WR, DET) -- decided by waiver priority", mine)
        self.assertIn("- Wed 09-30 -- waiver claim, $27: +Big Claim (QB, KC)\n", mine)          # a paid award is not marked
        two = md.split("## Team Name 2")[1].split("\n## ")[0]
        self.assertIn("- Wed 10-07 -- losing bid, $0: Late Claim (went to Team Name 3 for $0) -- decided by waiver priority", two)
        # Week Three Claim is typed as won at $0, but Yahoo has it at $16: flagged, and not called a priority decision
        self.assertIn("- 2026-09-23 Week Three Claim: typed Team Name 3 for $0; Yahoo has Team Name 3 for $16", md)
        self.assertEqual(md.count("decided by waiver priority"), 3)
        # the newsletter's bid margins read the same flag
        late = [m for m in league_data.bid_margins(*self.activity_and_offers(), 3) if m["won_at"] == 0]
        self.assertEqual([m["by_priority"] for m in late], [True])

    def test_a_zero_dollar_award_with_no_next_offer_entered_is_not_called_a_priority_decision(self):
        self.bids()                                  # nothing typed for Late Claim, Team 3's $0 claim
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertNotIn("decided by waiver priority", md)
        self.assertIn("Late Claim (RB, KC): won at $0, no losing offer entered (uncontested, or not typed in yet)", md)

    def test_funded_active_bidder_needs_faab_left_at_the_threshold_which_is_configurable(self):
        self.bids()
        md = league_data.build_report(self.con, today=date(2026, 10, 10), funded_min=60)
        self.assertIn("at least one paid bid in the window and at least $60 FAAB left", md)
        table = report_table(md)
        self.assertEqual({t: row[-2:] for t, row in table.items()},
                         {"Team Name 3 (me)": ["**yes**", "no"],       # $57 left: active, paid, but under $60
                          "Team Name 1": ["**yes**", "**yes**"],       # $90
                          "Team Name 2": ["no", "no"],
                          "Team Name 4": ["**yes**", "**yes**"]})      # $100
        self.assertEqual(report_table(league_data.build_report(self.con, today=date(2026, 10, 10), funded_min=57))["Team Name 3 (me)"][-1],
                         "**yes**")                                    # at the threshold counts
        self.assertEqual(league_data.FUNDED_MIN_FAAB, 10)
        self.assertEqual(league_data.build_parser().parse_args(["report"]).funded_min, 10)
        self.assertEqual(league_data.build_parser().parse_args(["report", "--funded-min", "25"]).funded_min, 25)

    def margin_lines(self, md):
        return [ln for ln in md.split("## My winning bids")[1].split("\n## ")[0].splitlines() if ln.startswith("- ")]

    def test_checked_through_line_is_read_strictly(self):
        today = date(2026, 10, 10)
        read = league_data.read_checked_through
        self.assertEqual(read(self.HEADER, today), (None, []))                                   # no line: not set, no complaint
        self.assertEqual(read("# checked_through:\n" + self.HEADER, today), (None, []))           # blank (the template's): not set
        self.assertEqual(read("# checked_through: 2026-10-07\n" + self.HEADER, today), (date(2026, 10, 7), []))
        self.assertEqual(read("\ufeff#Checked_Through : 10/7/2026\n" + self.HEADER, today), (date(2026, 10, 7), []))
        self.assertEqual(read("# checked_through: 2026-10-10\n", today), (date(2026, 10, 10), []))   # today itself is fine
        self.assertEqual(read("# checked_through: 2026-10-07,,,,,\n" + self.HEADER, today), (date(2026, 10, 7), []))   # saved by Excel
        self.assertEqual(read('"# checked_through: 2026-10-07",,\n', today), (date(2026, 10, 7), []))
        for text, why in (("# checked_through: Oct 7\n", "line 1: checked_through must be YYYY-MM-DD"),
                          ("# checked_through: 2026-10-11\n", "line 1: checked_through 2026-10-11 is after today (2026-10-10)"),
                          ("# checked_through: 2026-10-01\n# checked_through: 2026-10-07\n",
                           "line 2: a second checked_through line (line 1 has one) -- neither is used")):
            day, problems = read(text, today)
            self.assertIsNone(day)
            self.assertEqual(len(problems), 1)
            self.assertTrue(problems[0].startswith(why), problems)
        self.assertEqual(read(league_data.BIDS_TEMPLATE, today), (None, []))
        self.assertEqual(league_data.read_bids_csv(league_data.BIDS_TEMPLATE), ([], []))     # the template still reads cleanly

    def test_checked_through_turns_an_award_with_no_rows_into_uncontested_or_not_typed_in_yet(self):
        # Late Claim (Team 3's $0 claim) was awarded 10-07 and has no rows
        out = self.bids("# checked_through: 2026-10-06\n" + self.FIXTURE_ROWS)
        self.assertEqual(out["checked_through"], date(2026, 10, 6))
        self.assertEqual(self.rows("SELECT checked_through FROM lg_bid_imports ORDER BY import_id DESC LIMIT 1"), [("2026-10-06",)])
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("- Losing offers (bids.csv) checked through: **2026-10-06**", md)
        self.assertIn("Losing offers are checked through 2026-10-06: an award up to then with no rows was uncontested; a later one may "
                      "not be typed in yet", md)
        self.assertNotIn("whether or not it has been entered yet", md)
        self.assertIn("- Wed 10-07 -- Late Claim (RB, KC): won at $0, no losing offer entered (not typed in yet)", self.margin_lines(md))

        self.bids("# checked_through: 2026-10-07\n" + self.FIXTURE_ROWS)          # moved forward once the page was checked
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("- Wed 10-07 -- Late Claim (RB, KC): won at $0, no losing offer entered (uncontested)", self.margin_lines(md))
        self.assertEqual(len(self.current()), 5)                                   # the offers themselves didn't change
        self.assertEqual(self.rows("SELECT n_new, n_unchanged, checked_through FROM lg_bid_imports ORDER BY import_id DESC LIMIT 1"),
                         [(0, 5, "2026-10-07")])

        self.bids(self.FIXTURE_ROWS)                                               # line taken out: back to today's wording
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("checked through: not set", md)
        self.assertIn("- Wed 10-07 -- Late Claim (RB, KC): won at $0, no losing offer entered (uncontested, or not typed in yet)",
                      self.margin_lines(md))

    def test_with_the_window_checked_and_no_losing_offers_in_it_the_counts_are_all_bids(self):
        self.bids("# checked_through: 2026-10-08\n2026-09-16,Too Old,Team Name 2,40,Team Name 1,39\n")   # week 2: outside the window
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("Basis: **all bids -- winning claims from Yahoo's API; no losing offers in the window.", md)
        self.assertEqual(report_table(md)["Team Name 1"][4], "0")                  # known to be none, not "n/a"
        self.bids("# checked_through: 2026-09-21\n2026-09-16,Too Old,Team Name 2,40,Team Name 1,39\n")   # checked only up to week 2
        md = league_data.build_report(self.con, today=date(2026, 10, 10))
        self.assertIn("Basis: **winning waiver claims only -- no losing offers have been entered for this window.**", md)
        self.assertEqual(report_table(md)["Team Name 1"][4], "n/a")

    def test_a_bad_checked_through_line_is_rejected_and_stored_as_not_set_but_blocks_nothing_else(self):
        self.bids(self.FIXTURE_ROWS)
        out = self.bids("# checked_through: 2026-10-07\n# checked_through: 2026-10-08\n" + self.FIXTURE_ROWS.split("\n", 1)[1])
        self.assertTrue(out["ok"])
        self.assertIsNone(out["checked_through"])
        self.assertEqual(out["rejected"], ["line 4: a second checked_through line (line 3 has one) -- neither is used"])
        self.assertEqual(out["n_withdrawn"], 1)                                     # the dropped first row is still withdrawn
        self.assertEqual(self.rows("SELECT checked_through, n_rejected FROM lg_bid_imports ORDER BY import_id DESC LIMIT 1"), [(None, 1)])

    def test_an_older_db_gains_the_checked_through_column(self):
        old = sqlite3.connect(":memory:")
        self.addCleanup(old.close)
        old.execute("CREATE TABLE lg_bid_imports (import_id INTEGER PRIMARY KEY AUTOINCREMENT, imported_at TEXT NOT NULL, file_name TEXT, "
                    "file_sha256 TEXT, ok INTEGER NOT NULL, error TEXT, n_rows INTEGER, n_new INTEGER, n_corrected INTEGER, "
                    "n_withdrawn INTEGER, n_unchanged INTEGER, n_rejected INTEGER, n_flagged INTEGER, raw_file TEXT)")
        old.execute("INSERT INTO lg_bid_imports (imported_at, ok) VALUES ('2026-10-10', 1)")
        league_data.init_schema(old)
        league_data.init_schema(old)                                               # and running it again is harmless
        self.assertEqual(old.execute("SELECT imported_at, checked_through FROM lg_bid_imports").fetchall(), [("2026-10-10", None)])
        self.assertIsNone(league_data.checked_through(old))

    def activity_and_offers(self):
        weeks = league_data.league_weeks(self.con, league_data.latest_ok(self.con, "scoreboard")[0])
        _f, _c, start, end = league_data.report_window(weeks, date(2026, 10, 10))
        tx_snap = league_data.latest_ok(self.con, "transactions")[0]
        return league_data.team_activity(self.con, tx_snap, start, end), league_data.losing_bids(self.con, tx_snap, start, end)[0]

    def test_cli_creates_the_template_then_imports_and_its_exit_code_reports_trouble(self):
        db, path = self.dir / "t.db", self.dir / "manual" / "bids.csv"
        with league_data.connect(db) as con:
            self.con.backup(con)                              # the snapshot taken in setUp, in a file the CLI can open
        con.close()
        args = ["import-bids", "--db", str(db), "--file", str(path), "--raw-dir", str(self.dir / "raw")]

        def run():
            out = io.StringIO()
            with contextlib.redirect_stdout(out), mock.patch("sys.stderr"):
                return league_data.main(args), out.getvalue()

        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertIn("created", out)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(league_data.read_bids_csv(text), ([], []))          # header present, the example line is a comment
        self.assertIn("# 2026-10-07,Example Player,Winning Team Name,16,Losing Team Name,9", text)
        rc, out = run()
        self.assertEqual(rc, 0)                                              # nothing typed in yet: nothing to do, no failure
        path.write_text(text + "2026-09-30,Big Claim,Team Name 3,27,Team Name 1,20\n", encoding="utf-8")
        rc, out = run()
        self.assertEqual(rc, 0)
        self.assertIn("1 losing offer(s) in the file: 1 new", out)
        path.write_text(text + "2026-09-30,Big Claim,Team Name 3,26,Team Name 1,20\n", encoding="utf-8")
        rc, out = run()
        self.assertEqual(rc, 1)                                              # flagged: the typed winner isn't Yahoo's
        self.assertIn("FLAGGED", out)
        self.assertNotIn("Team Name", out)
        self.assertEqual(run()[1].strip(), "[BIDS] the file hasn't changed since the last import -- nothing to do")


class TestPrivacy(unittest.TestCase):
    def test_everything_this_layer_writes_is_gitignored(self):
        """The DB, the raw responses and the report all name the league or its managers."""
        try:
            out = subprocess.run(["git", "check-ignore", "league_data/league_activity_week05.md", "league_data/manual/bids.csv",
                                  "raw/league/x.json.gz", "faab_history_core_v0_1.db", "league/.env"],
                                 cwd=str(league_data.REPO_ROOT), capture_output=True, text=True)
        except OSError:
            self.skipTest("git is not available")
        self.assertEqual(len(out.stdout.split()), 5, out.stdout + out.stderr)
        self.assertEqual(league_data.DEFAULT_BIDS_CSV.relative_to(league_data.REPO_ROOT).parts[:2], ("league_data", "manual"))
        for default in (league_data.DEFAULT_REPORT_DIR, league_data.DEFAULT_RAW_DIR, league_data.DEFAULT_DB):
            self.assertIn(default.relative_to(league_data.REPO_ROOT).parts[0].split(".")[-1], ("league_data", "raw", "db"))


if __name__ == "__main__":
    unittest.main()
