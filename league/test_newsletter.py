#!/usr/bin/env python3
"""Tests for the league newsletter. From the repo root:

    python -m unittest discover -s league -v

A made-up four-team league (the fixtures of test_league_data.py, with scores
chosen so every season stat can be worked out by hand), a stand-in Yahoo and
hand-made tracker rows; everything in memory or in temp dirs.
"""
import contextlib
import copy
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import league_data
import newsletter
from test_league_data import FIXTURE, NOW, LeagueDataCase, Yahoo, lineup

# 1 plays 2 and 3 plays 4 every week. Week 4 is the one being recapped; week 5 is in progress.
#            week 1                 week 2 (3 and 4 tie)    week 3                      week 4 (4 loses by half a point)
SCORES = {1: {1: 100.0, 2: 90.0, 3: 80.0, 4: 120.0}, 2: {1: 95.0, 2: 105.0, 3: 80.0, 4: 80.0},
          3: {1: 110.5, 2: 70.25, 3: 99.0, 4: 60.0}, 4: {1: 88.8, 2: 101.3, 3: 120.4, 4: 119.9}, 5: {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0}}
# team 4 left a 30-point running back on its bench in the week it lost by half a point
BENCH = {(4, 4): [(4005, "Bench RB 4", "RB", "BN", 30.0), (4006, "Bench TE 4", "TE", "BN", 2.0)]}
DELTA = {(4, 1): -0.5}      # team 1's starters add up to half a point less than its official week-4 score
OWNER_ONLY_PLAYERS = ("Secret Sleeper", "Hidden Gem", "Quiet Stash")


class NewsletterCase(LeagueDataCase):
    def setUp(self):
        super().setUp()
        league_data.ledger.init_schema(self.con)          # the tracker's tables, in the same DB as in real life
        self.snapshot(Yahoo(scores=SCORES, bench=BENCH, delta=DELTA))
        self.assertTrue(league_data.import_bids(self.con, FIXTURE, self.dir / "raw", now=NOW)["ok"])
        self.add_tracker_week()
        self.data = newsletter.build_facts(self.con, 4)
        self.facts = self.data["facts"]

    def add_tracker_week(self):
        """What the tracker would hold for week 4: a thu run (the official first snapshot) and a sun run that carries
        the league-availability snapshot, one scoring, and results for the three players O.D.D.S. liked on the wire."""
        c, ins = self.con, league_data.ledger.insert_rows
        sid = c.execute("INSERT INTO tr_scorings (season, week, scored_at, hit_config_hash, hit_config_json) VALUES (2026, 4, 't', 'h', ?)",
                        (json.dumps({"hit": {"top_n_per_position": 24}}),)).lastrowid
        for run_id, slot in (("r-thu", "thu"), ("r-sun", "sun")):
            ins(c, "tr_runs", [dict(run_id=run_id, run_timestamp="2026-10-01T14:00:00+00:00", season=2026, week=4, run_slot=slot)])
        for model, spikes, top, pct in (("odds_prod", 3, 4, 96.4), ("odds_shadow", 2, 3, 81.0), ("dart", 1.14, 2.06, None)):
            ins(c, "tr_run_results", [dict(scoring_id=sid, run_id="r-thu", model_name=model, pick_set="all_k10", n_picks=10,
                                           top24_hits=top, spike_hits=spikes, total_points=1.0)])
            if pct is not None:
                ins(c, "tr_percentiles", [dict(scoring_id=sid, run_id="r-thu", model_name=model, pick_set="all_k10", metric="spike_hits",
                                               percentile=pct)])
        snap = c.execute("INSERT INTO tr_league_snapshots (run_id, taken_at, source, ok) VALUES ('r-sun', '2026-10-04T14:00:00+00:00', 'yahoo', 1)").lastrowid
        for i, (pid, player, score, status) in enumerate((("a1", "Secret Sleeper", 0.9, "FA"), ("b1", "Rostered Star", 0.8, None),
                                                          ("a2", "Hidden Gem", 0.7, "W"), ("a3", "Quiet Stash", 0.6, "FA"))):
            ins(c, "tr_pool", [dict(run_id="r-sun", player_id=pid, name=player, position="WR", team="KC", odds_prod_score=score)])
            if status:
                ins(c, "tr_league_availability", [dict(snapshot_id=snap, external_id=str(i), player_id=pid, status=status)])
        ins(c, "tr_player_results", [dict(scoring_id=sid, player_id="a1", position="WR", points=22.57, top24=1, spike=1),
                                     dict(scoring_id=sid, player_id="a2", position="WR", points=3.31, top24=0, spike=0)])
        c.commit()

    def of(self, kind, **where):
        return [f for f in self.facts if f["kind"] == kind and all(f.get(k) == v for k, v in where.items())]

    def one(self, kind, **where):
        (f,) = self.of(kind, **where)
        return f


class TestFacts(NewsletterCase):
    def test_every_fact_carries_an_audience_and_each_kind_belongs_to_one(self):
        self.assertTrue(all(f["audience"] == "league" or f["audience"] == "owner" or f["audience"] in {f"team:{t}" for t in (1, 2, 3, 4)}
                            for f in self.facts))
        by_kind = {}
        for f in self.facts:
            by_kind.setdefault(f["kind"], set()).add(f["audience"].split(":")[0])
        self.assertEqual({k for k, v in by_kind.items() if v == {"team"}}, {"bench_detail", "bid_margin"})
        self.assertEqual({k for k, v in by_kind.items() if v == {"owner"}},
                         {"owner_team", "bidder_profile", "odds_available_pick", "odds_available_summary"})
        self.assertTrue(all(len(v) == 1 for v in by_kind.values()))               # no kind straddles two audiences
        self.assertEqual({k for k, v in by_kind.items() if v == {"league"}},
                         {"meta", "matchup", "weekly_score", "top_starter", "top_bench", "standing", "bench", "season_stat", "score_check",
                          "transaction", "faab", "transactions_summary", "losing_bid", "odds_line"})
        json.dumps(self.data)                                                     # and all of it is plain JSON

    def test_results_use_the_scoreboard_scores(self):
        close, other = sorted(self.of("matchup"), key=lambda m: m["margin"])
        self.assertEqual((close["team_a"], close["score_a"], close["team_b"], close["score_b"], close["margin"], close["closest"], close["tied"]),
                         ("Team Name 3", 120.4, "Team Name 4", 119.9, 0.5, True, False))
        self.assertEqual((other["team_a"], other["score_a"], other["team_b"], other["score_b"], other["margin"], other["blowout"]),
                         ("Team Name 2", 101.3, "Team Name 1", 88.8, 12.5, True))
        self.assertEqual([(s["team"], s["week_rank"]) for s in self.of("weekly_score")],
                         [("Team Name 3", 1), ("Team Name 4", 2), ("Team Name 2", 3), ("Team Name 1", 4)])
        self.assertTrue(self.one("weekly_score", team="Team Name 3")["is_high"] and self.one("weekly_score", team="Team Name 1")["is_low"])
        # team 1's starters add up to 88.30, but the official 88.80 is what the result and the standings use
        self.assertEqual(self.one("score_check"), dict(kind="score_check", audience="league", week=4, team="Team Name 1", scoreboard_score=88.8,
                                                       lineup_points=88.3, difference=0.5))

    def test_standings_streaks_all_play_and_luck(self):
        self.assertEqual([(s["rank"], s["team"], s["wins"], s["losses"], s["ties"], s["points_for"], s["points_against"], s["streak_type"],
                           s["streak_length"]) for s in self.of("standing")],
                         [(1, "Team Name 3", 2, 1, 1, 379.4, 379.9, "win", 2), (2, "Team Name 1", 2, 2, 0, 394.3, 366.55, "loss", 1),
                          (3, "Team Name 2", 2, 2, 0, 366.55, 394.3, "win", 1), (4, "Team Name 4", 1, 2, 1, 379.9, 379.4, "loss", 2)])
        stat = {s["team"][-1]: s for s in self.of("season_stat")}
        #                    all-play W-L-T  expected  actual  luck   highs lows longest
        for team, expect in (("1", (7, 5, 0, 2.33, 2.0, -0.33, 1, 1, 1)), ("2", (6, 6, 0, 2.0, 2.0, 0.0, 1, 0, 1)),
                             ("3", (5, 6, 1, 1.83, 2.5, 0.67, 1, 2, 2)), ("4", (5, 6, 1, 1.83, 1.5, -0.33, 1, 2, 1))):
            s = stat[team]
            self.assertEqual((s["all_play_wins"], s["all_play_losses"], s["all_play_ties"], s["expected_wins"], s["actual_wins"], s["luck"],
                              s["weekly_highs"], s["weekly_lows"], s["longest_win_streak"]), expect, team)

    def test_bench_totals_are_for_everyone_and_the_swap_detail_is_for_that_team(self):
        flex = dict((r[1], r[4]) for r in lineup(4, 4, scores=SCORES))["Flex 4"]
        left = round(30.0 - flex, 2)                      # the 30-point back should have played instead of the flex receiver
        b = self.one("bench", team="Team Name 4")
        self.assertEqual((b["audience"], b["lineup_points"], b["best_possible"], b["left_on_bench"]), ("league", 119.9, round(119.9 + left, 2), left))
        self.assertEqual(self.one("bench", team="Team Name 2")["left_on_bench"], 0.0)
        d = self.one("bench_detail", team="Team Name 4")
        self.assertEqual(d["audience"], "team:4")
        self.assertEqual(d["swaps"], [dict(start="Bench RB 4", start_points=30.0, sit="Flex 4", sit_points=flex, gain=left)])
        self.assertEqual((d["result"], d["opponent"], d["would_have_flipped"]), ("lost", "Team Name 3", True))   # lost by 0.5, left 12 on the bench
        self.assertFalse(self.one("bench_detail", team="Team Name 1")["would_have_flipped"])
        self.assertEqual({f["audience"] for f in self.of("bench_detail")}, {"team:1", "team:2", "team:3", "team:4"})    # computed for every team
        s4 = self.one("season_stat", team="Team Name 4")
        self.assertEqual((s4["bench_left_season"], s4["bench_weeks"]), (left, 4))
        self.assertEqual(s4["lineup_efficiency_pct"], round(100 * 379.9 / (379.9 + left), 1))

    def test_best_lineup_rules(self):
        slots = [("QB", 1), ("RB", 2), ("WR", 1), ("W/R/T", 1)]

        def row(i, pos, slot, pts):
            return dict(external_id=str(i), name=f"P{i}", position=pos, selected_position=slot, points=pts)
        rows = [row(1, "QB", "QB", 20.0), row(2, "RB", "RB", 10.0), row(3, "WR", "WR", -1.5), row(4, "TE", "W/R/T", 4.0),
                row(5, "RB", "BN", 12.0), row(6, "WR", "BN", 6.0), row(7, "QB", "BN", 25.0), row(8, "RB", "IR", 40.0), row(9, "TE", "BN", 0.0)]
        best, chosen = newsletter.best_lineup(rows, slots)
        self.assertEqual(sorted(r["name"] for r in chosen), ["P2", "P4", "P5", "P6", "P7"])       # never the IR player; flex takes the tight end
        self.assertEqual(best, 25.0 + 12.0 + 10.0 + 6.0 + 4.0)
        rep = newsletter.bench_report(rows, slots)
        self.assertEqual((rep["lineup_points"], rep["best_possible"], rep["left_on_bench"], rep["empty_slots"]), (32.5, 57.0, 24.5, 1))
        self.assertEqual([(s["start"], s["sit"], s["gain"]) for s in rep["swaps"]], [("P7", "P3", 26.5), ("P5", "P1", -8.0), ("P6", None, 6.0)])
        self.assertEqual(round(sum(s["gain"] for s in rep["swaps"]), 2), rep["left_on_bench"])
        # a starter below zero and nobody better: the best move is an empty slot
        solo = newsletter.bench_report([row(1, "QB", "QB", 20.0), row(3, "WR", "WR", -0.7)], slots)
        self.assertEqual((solo["left_on_bench"], solo["swaps"]), (0.7, [dict(start=None, start_points=None, sit="P3", sit_points=-0.7, gain=0.7)]))

    def test_transactions_faab_and_bids_of_the_week(self):
        s = self.one("transactions_summary")
        self.assertEqual((s["moves"], s["waiver_claims"], s["faab_spent"], s["biggest_bid"], s["biggest_bid_team"], s["biggest_bid_added"]),
                         (5, 2, 37, 27, "Team Name 3", ["Big Claim (QB, KC)"]))
        self.assertEqual([(t["day"], t["team"], t["how"], t["bid"]) for t in self.of("transaction")],
                         [("Wed 09-30", "Team Name 1", "waiver claim", 10), ("Wed 09-30", "Team Name 3", "waiver claim", 27),
                          ("Wed 09-30", "Team Name 2", "free agent", None), ("Thu 10-01", "Team Name 2", "drop", None),
                          ("Sat 10-03", "Team Name 1", "trade", None), ("Sat 10-03", "Team Name 2", "trade", None)])
        self.assertEqual({f["team"][-1]: (f["remaining"], f["spent_week"], f["spent_season"]) for f in self.of("faab")},
                         {"1": (90, 10, 10), "2": (60, 0, 40), "3": (57, 27, 43), "4": (100, 0, 0)})
        self.assertEqual(sorted((b["team"], b["player"], b["amount"], b["winner"], b["winning_amount"]) for b in self.of("losing_bid")),
                         [("Team Name 1", "Big Claim", 20, "Team Name 3", 27), ("Team Name 4", "Big Claim", 5, "Team Name 3", 27),
                          ("Team Name 4", "Other Claim", 3, "Team Name 1", 10)])
        self.assertTrue(all(b["audience"] == "league" and "typed by hand" in b["source"] for b in self.of("losing_bid")))
        m = self.one("bid_margin", team="Team Name 3")                             # a team's own margin is for that team
        self.assertEqual((m["audience"], m["won_at"], m["next_offer"], m["next_team"], m["margin"], m["losing_offers"]),
                         ("team:3", 27, 20, "Team Name 1", 7, 2))
        self.assertEqual(self.one("bid_margin", team="Team Name 1")["audience"], "team:1")

    def test_owner_facts_bidding_profiles_and_what_odds_liked_on_the_wire(self):
        self.assertEqual(self.one("owner_team")["team_id"], 3)                     # the default: the logged-in account's team
        prof = {p["team"][-1]: (p["claims_won"], p["losing_bids"], p["bids"], p["active"], p["faab_remaining"]) for p in self.of("bidder_profile")}
        self.assertEqual(prof, {"1": (1, 2, 3, True, 90), "2": (1, 1, 2, True, 60), "4": (0, 2, 2, True, 100)})     # weeks 2-4, not the owner
        self.assertEqual({(p["from_week"], p["to_week"], p["basis"]) for p in self.of("bidder_profile")}, {(2, 4, "all bids")})
        line = self.one("odds_line", model="O.D.D.S.")
        self.assertEqual((line["audience"], line["run_slot"], line["spike_weeks"], line["top_finishes"], line["dart_average_spike_weeks"],
                          line["beat_pct_of_dart_throws"], line["top_finish_cutoff"]), ("league", "thu", 3, 4, 1.1, 96, 24))
        picks = self.of("odds_available_pick")
        self.assertEqual([(p["place"], p["player"], p["status"], p["points"], p["played"], p["spike_week"]) for p in picks],
                         [(1, "Secret Sleeper", "free agent", 22.57, True, True), (2, "Hidden Gem", "waivers", 3.31, True, False),
                          (3, "Quiet Stash", "free agent", 0.0, False, False)])       # the rostered star above them is not "available"
        s = self.one("odds_available_summary")
        self.assertEqual((s["run_slot"], s["listed"], s["spike_weeks"], s["top_finishes"]), ("sun", 3, 1, 1))

    def test_nothing_is_hardcoded_to_one_team(self):
        other = newsletter.build_facts(self.con, 4, owner_team=1)["facts"]
        self.assertEqual([f["team_id"] for f in other if f["kind"] == "owner_team"], [1])
        self.assertEqual(sorted(f["team"] for f in other if f["kind"] == "bidder_profile"), ["Team Name 2", "Team Name 3", "Team Name 4"])
        strip = lambda facts: [f for f in facts if f["audience"] != "owner"]
        self.assertEqual(strip(other), strip(self.facts))                          # league and per-team facts don't depend on whose view it is
        text = newsletter.render_recap({"facts": other}, "owner")
        self.assertIn("## For this team only: Team Name 1", text)
        self.assertNotIn("## For this team only: Team Name 3", text)
        with self.assertRaises(newsletter.NewsletterError):
            newsletter.build_facts(self.con, 4, owner_team=99)

    def test_a_week_that_is_not_final_or_has_no_data_is_refused(self):
        with self.assertRaises(newsletter.NewsletterError) as cm:
            newsletter.build_facts(self.con, 5)                                    # still being played
        self.assertIn("isn't final", str(cm.exception))
        empty = league_data.connect(":memory:")
        self.addCleanup(empty.close)
        with self.assertRaises(newsletter.NewsletterError) as cm:
            newsletter.build_facts(empty, 4)
        self.assertIn("run `python league/league_data.py snapshot` first", str(cm.exception))


class TestAudiences(NewsletterCase):
    def test_visible_is_league_then_one_team_then_owner(self):
        aud = lambda a: {f["audience"] for f in newsletter.visible(self.facts, a)}
        self.assertEqual(aud("league"), {"league"})
        self.assertEqual(aud("team:4"), {"league", "team:4"})
        self.assertEqual(aud("owner"), {"league", "team:3", "owner"})              # the owner's own team, nobody else's
        for bad in ("everyone", "team:", "team:x", None):
            with self.assertRaises(ValueError):
                newsletter.visible(self.facts, bad)

    def marked(self):
        """The facts with every owner fact and every team fact rewritten to carry strings and numbers found nowhere else."""
        data = copy.deepcopy(self.data)
        n = [0]

        def mark(value, tag, base):
            n[0] += 1
            if isinstance(value, bool) or value is None:
                return value
            if isinstance(value, (int, float)):
                return base + n[0]
            if isinstance(value, str):
                return f"{tag}{n[0]}X"
            if isinstance(value, list):
                return [mark(v, tag, base) for v in value]
            return {k: mark(v, tag, base) for k, v in value.items()}
        for f in data["facts"]:
            if f["audience"] == "league":
                continue
            team = None if f["audience"] == "owner" else int(f["audience"][5:])
            tag, base = ("OWNERONLY", 9000000) if team is None else (f"TEAMONLY{team}N", 7000000 + 100000 * team)   # 9000xxx / 7T00xxx
            keep = ("kind", "audience", "team_id", "result", "how")               # what selects or words a line, not what it says
            f.update({k: mark(v, tag, base) for k, v in f.items() if k not in keep})
        return data

    def test_a_league_or_team_rendering_can_never_contain_an_owner_fact(self):
        data = self.marked()
        self.assertIn("OWNERONLY", newsletter.render_recap(data, "owner"))        # the marks do reach a rendering that may show them
        self.assertIn("TEAMONLY3N", newsletter.render_recap(data, "owner"))
        for audience in ("league", "team:1", "team:2", "team:3", "team:4"):
            for text in (newsletter.render_recap(data, audience), newsletter.render_group_chat(data)):
                with self.subTest(audience=audience):
                    self.assertNotIn("OWNERONLY", text)
                    self.assertNotIn("9000", text)                                 # nor a number that only an owner fact holds
                    self.assertNotIn("## Owner only", text)
                    others = [t for t in (1, 2, 3, 4) if audience != f"team:{t}"]
                    self.assertFalse([t for t in others if f"TEAMONLY{t}N" in text or f"7{t}00" in text])    # ...nor another team's
        self.assertIn("TEAMONLY4N", newsletter.render_recap(data, "team:4"))      # a team does get its own
        self.assertIn("7400", newsletter.render_recap(data, "team:4"))
        self.assertIn("9000", newsletter.render_recap(data, "owner"))
        # and with the real facts: the names only the owner's analysis knows appear in the owner's view alone
        owner, league = newsletter.render_recap(self.data, "owner"), newsletter.render_recap(self.data, "league")
        for player in OWNER_ONLY_PLAYERS:
            self.assertIn(player, owner)
            self.assertNotIn(player, league + newsletter.render_group_chat(self.data) + newsletter.render_recap(self.data, "team:1"))
        self.assertNotIn("Active bidder", league)

    def test_the_owner_view_is_the_league_recap_plus_the_owners_team_plus_the_owner_section(self):
        league, owner = newsletter.render_recap(self.data, "league"), newsletter.render_recap(self.data, "owner")
        self.assertTrue(owner.startswith(league.rstrip("\n")))
        extra = owner[len(league.rstrip("\n")):]
        self.assertIn("## For this team only: Team Name 3", extra)
        self.assertIn("Big Claim (QB, KC): won at $27, next offer $20 (Team Name 1), margin $7", extra)
        self.assertIn("| Team Name 1 | Nick1 | 1 | 2 | 3 | yes | $90 |", extra)
        self.assertIn("- No. 1: Secret Sleeper (WR, KC; free agent) -- 22.57 points, spike week", extra)
        self.assertIn("- No. 3: Quiet Stash (WR, KC; free agent) -- 0.00 points, did not play", extra)
        self.assertNotIn("Bench RB 4", extra)                                      # team 4's swap is team 4's business
        team4 = newsletter.render_recap(self.data, "team:4")
        self.assertIn("- Start Bench RB 4 (30.00) over Flex 4", team4)
        self.assertIn("The best lineup would have won this matchup.", team4)


class TestRenderingsAndCheck(NewsletterCase):
    def test_league_recap_prints_the_facts(self):
        md = newsletter.render_recap(self.data, "league")
        self.assertIn("# Week 4 recap", md)
        self.assertIn("- **Team Name 3** 120.40 def. **Team Name 4** 119.90 (margin 0.50) -- the closest game of the week", md)
        self.assertIn("| 1 | Team Name 3 | Nick3 | 2-1-1 | 379.40 | 379.90 | 2 wins |", md)
        self.assertIn("| 4 | Team Name 4 | Nick4 | 1-2-1 | 379.90 | 379.40 | 2 losses |", md)
        self.assertIn("| Team Name 3 | 5-6-1 | 1.83 | 2.5 | +0.67 | 1 | 2 | 2 |", md)
        self.assertIn("5 transaction(s) this week, 2 of them waiver claims for a total of $37. Biggest winning bid: $27 by Team Name 3 "
                      "for Big Claim (QB, KC).", md)
        self.assertIn("- Team Name 1 offered $20 for Big Claim (went to Team Name 3 for $27)", md)
        self.assertIn("## O.D.D.S. corner (for fun, not advice)", md)
        self.assertIn("- O.D.D.S.: 3 of its top 10 picks had a spike week and 4 finished top-24 at their position; random darts average "
                      "1.1 and 2.1 -- better than 96% of dart throws.", md)
        self.assertIn("- Team Name 1: the official score is 88.80; its starters' individual points add up to 88.30 (a difference of 0.50).", md)
        chat = newsletter.render_group_chat(self.data)
        self.assertLess(len(chat), 1200)
        self.assertIn("Standings: 1. Team Name 3 (2-1-1), 2. Team Name 1 (2-2), 3. Team Name 2 (2-2), 4. Team Name 4 (1-2-1)", chat)
        self.assertIn("O.D.D.S. corner (for fun, not advice): 3 of its top 10 had a spike week; random darts average 1.1.", chat)

    def test_every_rendering_passes_the_check_and_an_added_fact_does_not(self):
        for audience, text in (("league", newsletter.render_recap(self.data, "league")), ("league", newsletter.render_group_chat(self.data)),
                               ("owner", newsletter.render_recap(self.data, "owner")), ("team:4", newsletter.render_recap(self.data, "team:4"))):
            self.assertEqual(newsletter.check_text(text, self.data, audience), [], audience)
        ok = "Team Name 3 edged Team Name 4 120.40 to 119.90 in week 4, the closest game of the week (margin 0.5)."
        self.assertEqual(newsletter.check_text(ok, self.data, "league"), [])
        made_up = "Team Name 3 edged Team Name 4 121.40 to 119.90 and is on pace for 13 wins."
        self.assertEqual(newsletter.check_text(made_up, self.data, "league"),
                         ["the number 121.40 is not in the facts for audience 'league'", "the number 13 is not in the facts for audience 'league'"])
        leak = "Team Name 3 won. Secret Sleeper was sitting on waivers the whole time."
        self.assertEqual(newsletter.check_text(leak, self.data, "league"), ["'Secret Sleeper' is a fact for another audience, not for 'league'"])
        self.assertEqual(newsletter.check_text(leak, self.data, "owner"), [])
        swap = "Team Name 4 should have started Bench RB 4 (30.00)."              # true, and written for team 4 only
        self.assertEqual(newsletter.check_text(swap, self.data, "team:4"), [])
        self.assertEqual(newsletter.check_text(swap, self.data, "team:1"), [])    # the name and the points are in the league's lineup facts
        margin = "Team Name 3 paid $7 more than it had to."
        self.assertEqual(newsletter.check_text(margin, self.data, "team:3"), [])
        self.assertEqual(newsletter.check_text("The gap was 12.02.", self.data, "league"),
                         [])                                                       # team 4's bench total is a league fact
        self.assertTrue(newsletter.check_text("The owner's top wire pick scored 22.57.", self.data, "league"))

    def test_build_writes_the_facts_and_every_rendering_and_check_reads_them_back(self):
        db, out_dir = self.dir / "t.db", self.dir / "newsletter"
        disk = league_data.connect(db)
        self.con.commit()
        self.con.backup(disk)
        disk.close()

        def run(*argv):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                return newsletter.main(list(argv)), out.getvalue()
        rc, out = run("build", "--week", "4", "--db", str(db), "--out-dir", str(out_dir))
        self.assertEqual(rc, 0, out)
        self.assertNotIn("Team Name", out)                                         # the console line names nobody
        folder = out_dir / "2026_week04"
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ["facts.json", "facts.md", "league_groupchat.txt", "league_recap.md", "owner_recap.md"])
        saved = json.loads((folder / "facts.json").read_text(encoding="utf-8"))
        self.assertTrue(all("audience" in f for f in saved["facts"]))
        self.assertEqual((folder / "league_recap.md").read_text(encoding="utf-8"), newsletter.render_recap(saved, "league"))
        self.assertIn("## owner / bidder_profile", (folder / "facts.md").read_text(encoding="utf-8"))
        prose = self.dir / "prose.md"
        prose.write_text("Team Name 2 beat Team Name 1 101.30 to 88.80.\n", encoding="utf-8")
        self.assertEqual(run("check", "--week", "4", "--file", str(prose), "--out-dir", str(out_dir))[0], 0)
        prose.write_text("Team Name 2 beat Team Name 1 by 41.5.\n", encoding="utf-8")
        rc, out = run("check", "--week", "4", "--file", str(prose), "--out-dir", str(out_dir))
        self.assertEqual(rc, 1)
        self.assertIn("the number 41.5 is not in the facts", out)
        self.assertEqual(run("build", "--week", "5", "--db", str(db), "--out-dir", str(out_dir))[0], 2)     # not final: refused
        with mock.patch.object(newsletter, "render_group_chat", return_value="Week 4: somebody scored 999.99\n"):
            rc, out = run("build", "--week", "4", "--db", str(db), "--out-dir", str(out_dir))            # a template gone wrong
        self.assertEqual(rc, 1)
        self.assertIn("CHECK FAILED in league_groupchat.txt: the number 999.99", out)


class TestPrivacy(unittest.TestCase):
    def test_newsletter_output_goes_under_a_gitignored_folder(self):
        target = newsletter.DEFAULT_OUT_DIR / "2026_week05" / "facts.json"
        self.assertEqual(target.relative_to(league_data.REPO_ROOT).parts[:2], ("league_data", "newsletter"))
        try:
            out = subprocess.run(["git", "check-ignore", str(target.relative_to(league_data.REPO_ROOT)).replace("\\", "/")],
                                 cwd=str(league_data.REPO_ROOT), capture_output=True, text=True)
        except OSError:
            self.skipTest("git is not available")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
