#!/usr/bin/env python3
"""League newsletter, v1: a weekly recap of the user's own Yahoo league, built
from the league data layer's snapshots after the Tuesday cycle.

  python league/newsletter.py build --week 5        # facts, then every rendering, each checked against the facts
  python league/newsletter.py check --week 5 --file prose.md --audience league

FACTS FIRST. Code computes every number and name into a facts file
(facts.json, with a readable facts.md beside it). Prose is written ONLY from
that file and may not add a fact: the renderings here are templates that print
fact fields, and check_text() verifies any text -- a template's output or
something written by hand later -- by looking every number and every known
name in it up in the facts its audience may see. The check is a net, not a
proof: it cannot tell a right number in the wrong sentence.

AUDIENCES. Every fact carries `audience`:
  league      what every manager can already see on Yahoo (scores, standings,
              lineups, transactions, FAB Offers bids) and arithmetic on it
  team:<id>   analysis written for that one manager (which bench player he
              should have started, his margin over the next bid)
  owner       the installation owner's private analysis (other managers'
              bidding profiles, what O.D.D.S. liked on the waiver wire)
A rendering takes an audience and is handed only the facts visible to it
(visible()): `league` sees league facts; `team:<id>` adds that team's; `owner`
sees league facts, the owner's own team's, and owner facts. A template never
sees a fact outside its audience, so it cannot print one. v1 writes the league
recap, a short group-chat version of it, and the owner's view.

NO HARDCODED "ME". Everything computed per team takes a team id and is
computed for every team. "My team" is only the default for `--team`: the team
Yahoo says the logged-in account owns (league_data.default_team).

SCORES. The official result of a matchup is Yahoo's scoreboard score. Player
points come from each team's final lineup; where a team's starters don't add
up to its scoreboard score (3 of 60 team-weeks on 2026-10-13, all under a
point) the scoreboard score is used and the difference is recorded as a fact.

PRIVACY. Everything written here names teams and managers and goes under
league_data/newsletter/ (gitignored); nothing is committed. The tests run on
a made-up league.
"""
import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

LEAGUE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LEAGUE_DIR))

import league_data as ld  # noqa: E402

DEFAULT_OUT_DIR = ld.DEFAULT_REPORT_DIR / "newsletter"
TOP_STARTERS = 3
ODDS_TOP_N = 10          # the tracker's headline pick set
FLEX_LETTERS = {"Q": "QB", "W": "WR", "R": "RB", "T": "TE"}
# fact fields whose values are names (a string or a list of strings): what check_text() treats as "a known name"
NAME_KEYS = ("team", "manager", "player", "opponent", "winner", "team_a", "team_b", "next_team", "biggest_bid_team", "biggest_bid_added",
             "sit", "start", "added", "dropped", "old_name")
PRINTED_TEXT_KEYS = NAME_KEYS + ("day",)      # strings a template prints verbatim, so digits inside them may appear in prose
NUMBER = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")


class NewsletterError(Exception):
    """The recap can't be built from what is on record (exit code 2)."""


def fact(kind, audience, **fields):
    return {"kind": kind, "audience": audience, **fields}


# ----------------------------------------------------------------- per-team math

def best_lineup(rows, slots):
    """The highest-scoring legal lineup from one team's roster of a week.
    rows: lg_lineups rows; slots: [(position, count)] of the starting slots,
    e.g. ("W/R/T", 1) for a flex. -> (total, [row]). A player on IR can't be
    started; a slot is left empty rather than filled with a player who scored
    nothing or less. Dedicated slots are filled before flex slots, which is
    the best possible lineup when every flex slot takes the same positions."""
    avail = sorted((r for r in rows if r["selected_position"] != "IR" and r["points"] > 0), key=lambda r: (-r["points"], r["external_id"]))
    chosen = []
    for pos, n in sorted(slots, key=lambda s: "/" in s[0]):
        ok = {FLEX_LETTERS[x] for x in pos.split("/")} if "/" in pos else {pos}
        chosen += [r for r in avail if r["position"] in ok and r not in chosen][:n]
    return round(sum(r["points"] for r in chosen), 2), chosen


def bench_report(rows, slots):
    """What one team left on its bench in one week: dict(lineup_points,
    best_possible, left_on_bench, empty_slots, swaps). A swap names the bench
    player who should have started and the starter he should have replaced:
    `sit` is None when the slot was simply empty, and `start` is None when
    the better move was to leave the slot empty (a starter who scored below
    zero)."""
    starters = [r for r in rows if r["selected_position"] not in ld.BENCH_SLOTS]
    best, chosen = best_lineup(rows, slots)
    lineup_points = round(sum(r["points"] for r in starters), 2)
    ins = sorted((r for r in chosen if r not in starters), key=lambda r: -r["points"])
    outs = sorted((r for r in starters if r not in chosen), key=lambda r: r["points"])
    swaps = [{"start": a["name"], "start_points": a["points"], "sit": outs[i]["name"] if i < len(outs) else None,
              "sit_points": outs[i]["points"] if i < len(outs) else None,
              "gain": round(a["points"] - (outs[i]["points"] if i < len(outs) else 0), 2)} for i, a in enumerate(ins)]
    swaps += [{"start": None, "start_points": None, "sit": o["name"], "sit_points": o["points"], "gain": round(-o["points"], 2)}
              for o in outs[len(ins):]]
    return {"lineup_points": lineup_points, "best_possible": best, "left_on_bench": round(best - lineup_points, 2),
            "empty_slots": sum(n for _p, n in slots) - len(starters), "swaps": swaps}


def streaks(results):
    """results: 'W'/'L'/'T' per week in order -> (current type, current length, longest winning streak)."""
    longest = run = 0
    for r in results:
        run = run + 1 if r == "W" else 0
        longest = max(longest, run)
    current = 0
    for r in reversed(results):
        if r != results[-1]:
            break
        current += 1
    return (results[-1] if results else None), current, longest


# ------------------------------------------------------------------------ facts

def build_facts(con, week, owner_team=None):
    """Every fact of the recap for league week `week`, as {"facts": [...]}.
    owner_team: the team id the owner's view is for (default: default_team).
    Raises NewsletterError when a needed snapshot is missing or the week
    isn't final."""
    snaps = {r: ld.latest_ok(con, r) for r in ("teams", "transactions", "scoreboard", "settings")}
    if any(v is None for v in snaps.values()):
        raise NewsletterError("a league snapshot is missing (" + ", ".join(r for r, v in snaps.items() if v is None)
                              + ") -- run `python league/league_data.py snapshot` first")
    owner_team = owner_team if owner_team is not None else ld.default_team(con)
    teams = {r["team_id"]: dict(r) for r in con.execute("SELECT * FROM lg_teams WHERE snapshot_id=?", (snaps["teams"][0],))}
    name = {t: teams[t]["team_name"] for t in teams}
    boss = {t: teams[t]["manager_nickname"] or "" for t in teams}
    if owner_team not in teams:
        raise NewsletterError(f"team {owner_team} is not in the league (teams: {sorted(teams)})")
    sb = snaps["scoreboard"][0]
    games = defaultdict(dict)       # week -> team -> matchup row
    for r in con.execute("SELECT * FROM lg_matchups WHERE snapshot_id=? AND week<=?", (sb, week)):
        games[r["week"]][r["team_id"]] = dict(r)
    played = sorted(games)
    if week not in games or any(g["status"] != "postevent" for w in played for g in games[w].values()):
        raise NewsletterError(f"week {week} (or a week before it) isn't final in the newest scoreboard snapshot")
    weeks = ld.league_weeks(con, sb)
    season = weeks[min(weeks)][0].year
    slots = ld.roster_slots(con)
    lineups = {w: ld.week_lineups(con, w) for w in played}
    if not lineups[week]:
        raise NewsletterError(f"no final lineups on record for week {week} -- run `python league/league_data.py snapshot`")
    facts = [fact("meta", "league", season=season, week=week, weeks_played=len(played), teams=len(teams),
                  data_as_of={r: v[1][:16].replace("T", " ") + " UTC" for r, v in snaps.items()})]

    # ---- this week's results
    score = {w: {t: g["points"] for t, g in games[w].items()} for w in played}
    pairs = sorted({tuple(sorted((t, g["opponent_team_id"]))) for t, g in games[week].items()})
    margins = {p: round(abs(score[week][p[0]] - score[week][p[1]]), 2) for p in pairs}
    for a, b in pairs:
        ga = games[week][a]
        if not ga["is_tied"] and ga["winner_team_id"] == b:
            a, b = b, a
        facts.append(fact("matchup", "league", week=week, team_a=name[a], score_a=score[week][a], team_b=name[b], score_b=score[week][b],
                          tied=bool(ga["is_tied"]), margin=margins[tuple(sorted((a, b)))],
                          closest=margins[tuple(sorted((a, b)))] == min(margins.values()),
                          blowout=margins[tuple(sorted((a, b)))] == max(margins.values()) and len(pairs) > 1))
    ranked = sorted(score[week], key=lambda t: (-score[week][t], t))
    for t in ranked:
        facts.append(fact("weekly_score", "league", week=week, team=name[t], manager=boss[t], score=score[week][t],
                          week_rank=1 + sum(1 for o in ranked if score[week][o] > score[week][t]),
                          is_high=score[week][t] == max(score[week].values()), is_low=score[week][t] == min(score[week].values())))
    everyone = [dict(r, fantasy_team=t) for t, rows in lineups[week].items() for r in rows]
    starters = sorted((r for r in everyone if r["selected_position"] not in ld.BENCH_SLOTS), key=lambda r: (-r["points"], r["name"]))
    for place, r in enumerate(starters[:TOP_STARTERS], start=1):
        facts.append(fact("top_starter", "league", week=week, place=place, player=r["name"], position=r["position"], nfl_team=r["team"],
                          team=name[r["fantasy_team"]], points=r["points"]))
    benched = sorted((r for r in everyone if r["selected_position"] == "BN"), key=lambda r: (-r["points"], r["name"]))
    if benched:
        r = benched[0]
        facts.append(fact("top_bench", "league", week=week, player=r["name"], position=r["position"], nfl_team=r["team"],
                          team=name[r["fantasy_team"]], points=r["points"]))

    # ---- season to date: standings, all-play, luck, highs and lows, streaks
    season_rows = {}
    for t in teams:
        res = ["T" if games[w][t]["is_tied"] else "W" if games[w][t]["winner_team_id"] == t else "L" for w in played]
        ap = [(sum(1 for o in score[w] if o != t and score[w][o] < score[w][t]), sum(1 for o in score[w] if o != t and score[w][o] > score[w][t]),
               sum(1 for o in score[w] if o != t and score[w][o] == score[w][t])) for w in played]
        cur, cur_len, longest = streaks(res)
        season_rows[t] = {
            "wins": res.count("W"), "losses": res.count("L"), "ties": res.count("T"),
            "points_for": round(sum(score[w][t] for w in played), 2),
            "points_against": round(sum(score[w][games[w][t]["opponent_team_id"]] for w in played), 2),
            "streak_type": {"W": "win", "L": "loss", "T": "tie"}[cur], "streak_length": cur_len, "longest_win_streak": longest,
            "all_play_wins": sum(a[0] for a in ap), "all_play_losses": sum(a[1] for a in ap), "all_play_ties": sum(a[2] for a in ap),
            "expected_wins": round(sum((a[0] + 0.5 * a[2]) / (len(teams) - 1) for a in ap), 2),
            "weekly_highs": sum(1 for w in played if score[w][t] == max(score[w].values())),
            "weekly_lows": sum(1 for w in played if score[w][t] == min(score[w].values()))}
        row = season_rows[t]
        row["actual_wins"] = row["wins"] + 0.5 * row["ties"]
        row["luck"] = round(row["actual_wins"] - row["expected_wins"], 2)
    order = sorted(teams, key=lambda t: (-season_rows[t]["actual_wins"], -season_rows[t]["points_for"], t))
    for rank, t in enumerate(order, start=1):
        r = season_rows[t]
        facts.append(fact("standing", "league", through_week=week, rank=rank, team=name[t], manager=boss[t], wins=r["wins"], losses=r["losses"],
                          ties=r["ties"], points_for=r["points_for"], points_against=r["points_against"], streak_type=r["streak_type"],
                          streak_length=r["streak_length"]))

    # ---- name changes: a team whose name differs from the one it had when the week began gets a one-time note
    for t, old in renamed_since(con, weeks[week][0]).items():
        if t in name and old != name[t]:
            facts.append(fact("team_renamed", "league", week=week, team=name[t], old_name=old))

    # ---- bench: league gets the totals, each team gets the detail
    bench = {w: {t: bench_report(rows, slots) for t, rows in lineups[w].items()} for w in played if lineups[w]}
    for t in order:
        b = bench[week][t]
        facts.append(fact("bench", "league", week=week, team=name[t], lineup_points=b["lineup_points"], best_possible=b["best_possible"],
                          left_on_bench=b["left_on_bench"], empty_slots=b["empty_slots"]))
        g = games[week][t]
        opp = g["opponent_team_id"]
        lost = not g["is_tied"] and g["winner_team_id"] != t
        facts.append(fact("bench_detail", f"team:{t}", week=week, team=name[t], opponent=name[opp], score=score[week][t],
                          opponent_score=score[week][opp], result="tied" if g["is_tied"] else "lost" if lost else "won",
                          left_on_bench=b["left_on_bench"], swaps=b["swaps"],
                          would_have_flipped=bool(lost and round(score[week][t] + b["left_on_bench"], 2) > score[week][opp])))
    for t in order:
        r = season_rows[t]
        have = [w for w in played if w in bench and t in bench[w]]
        left = round(sum(bench[w][t]["left_on_bench"] for w in have), 2)
        got, best = sum(bench[w][t]["lineup_points"] for w in have), sum(bench[w][t]["best_possible"] for w in have)
        facts.append(fact("season_stat", "league", through_week=week, team=name[t], all_play_wins=r["all_play_wins"],
                          all_play_losses=r["all_play_losses"], all_play_ties=r["all_play_ties"], expected_wins=r["expected_wins"],
                          actual_wins=r["actual_wins"], luck=r["luck"], weekly_highs=r["weekly_highs"], weekly_lows=r["weekly_lows"],
                          longest_win_streak=r["longest_win_streak"], bench_left_season=left, bench_weeks=len(have),
                          lineup_efficiency_pct=round(100 * got / best, 1) if best else None))
    for w in played:      # where the lineup doesn't add up to the official score, say so rather than hide it
        for t, b in bench.get(w, {}).items():
            if abs(b["lineup_points"] - score[w][t]) >= 0.005:
                facts.append(fact("score_check", "league", week=w, team=name[t], scoreboard_score=score[w][t],
                                  lineup_points=b["lineup_points"], difference=round(score[w][t] - b["lineup_points"], 2)))

    # ---- the week's transactions and FAAB
    tx = snaps["transactions"][0]
    start = datetime.combine(weeks[week][0], datetime.min.time(), ld.LEAGUE_TZ)
    end = datetime.combine(weeks[week][1] + timedelta(days=1), datetime.min.time(), ld.LEAGUE_TZ)
    activity = ld.team_activity(con, tx, start, end)
    offers, in_week = ld.losing_bids(con, tx, start, end)
    spent_season = {r[0]: r[1] for r in con.execute(
        "SELECT p.destination_team_id, SUM(COALESCE(t.faab_bid, 0)) FROM lg_transactions t JOIN lg_transaction_players p "
        "ON p.snapshot_id = t.snapshot_id AND p.transaction_id = t.transaction_id WHERE t.snapshot_id=? AND p.action='add' "
        "AND p.source_type='waivers' GROUP BY 1", (tx,))}
    claims = []
    for when, _tid, t, e in sorted((e["when"], e["transaction_id"], t, e) for t in order for e in activity.get(t, [])):
        how = "waiver claim" if e["waiver"] else "trade" if e["type"] == "trade" else "free agent" if e["added"] else "drop"
        facts.append(fact("transaction", "league", week=week, team=name[t], day=f"{when.astimezone(ld.LEAGUE_TZ):%a %m-%d}", how=how,
                          added=e["added"], dropped=e["dropped"], bid=e["faab_bid"] if e["waiver"] else None))
        if e["waiver"]:
            claims.append((e["faab_bid"], name[t], e["added"]))
    for t in order:
        facts.append(fact("faab", "league", week=week, team=name[t], remaining=teams[t]["faab_balance"],
                          spent_week=sum(e["faab_bid"] for e in activity.get(t, []) if e["waiver"]), spent_season=spent_season.get(t, 0)))
    top_claim = max(claims, key=lambda c: c[0]) if claims else None
    facts.append(fact("transactions_summary", "league", week=week,
                      moves=len({e["transaction_id"] for evs in activity.values() for e in evs}), waiver_claims=len(claims),
                      faab_spent=sum(c[0] for c in claims), biggest_bid=top_claim[0] if top_claim else None,
                      biggest_bid_team=top_claim[1] if top_claim else None, biggest_bid_added=top_claim[2] if top_claim else []))
    for o in sorted(in_week, key=lambda o: (o["when"], -o["losing_amount"])):
        facts.append(fact("losing_bid", "league", week=week, team=name.get(o["losing_team_id"], "?"), player=o["player"],
                          amount=o["losing_amount"], winner=name.get(o["winning_team_id"], "?"), winning_amount=o["winning_amount"],
                          source="typed by hand from Yahoo's FAB Offers tab"))
    for t in order:
        for m in ld.bid_margins(activity, offers, t):
            facts.append(fact("bid_margin", f"team:{t}", week=week, team=name[t], added=m["added"], won_at=m["won_at"],
                              next_offer=m["next_offer"], next_team=name.get(m["next_team_id"]) if m["next_team_id"] else None,
                              margin=m["margin"], losing_offers=m["n_offers"]))

    # ---- owner only: who has been bidding, and what O.D.D.S. liked on this league's waiver wire
    facts.append(fact("owner_team", "owner", team_id=owner_team, team=name[owner_team], manager=boss[owner_team]))
    first = max(min(weeks), week - ld.REPORT_WEEKS + 1)
    w_start = datetime.combine(weeks[first][0], datetime.min.time(), ld.LEAGUE_TZ)
    window = ld.team_activity(con, tx, w_start, end)
    _all, window_offers = ld.losing_bids(con, tx, w_start, end)
    basis, bids = ld.bidder_activity(con, window, window_offers)
    for t in order:
        if t == owner_team:
            continue
        won = sum(1 for e in window.get(t, []) if e["waiver"])
        facts.append(fact("bidder_profile", "owner", team=name[t], manager=boss[t], from_week=first, to_week=week, claims_won=won,
                          losing_bids=sum(1 for o in window_offers if o["losing_team_id"] == t), bids=bids.get(t, 0),
                          active=bids.get(t, 0) >= ld.ACTIVE_BIDDER_MIN, faab_remaining=teams[t]["faab_balance"],
                          basis="all bids" if window_offers else "winning claims only"))
    facts += odds_facts(con, season, week)
    return {"facts": facts}


def odds_facts(con, season, week):
    """The tracker's side of the recap, when it has scored the week: the
    headline line for everyone (how O.D.D.S.'s top picks did against random
    darts -- for fun, not advice), and for the owner the names it ranked
    highest among players who were available in this league. [] if the
    tracker's tables hold nothing for the week."""
    try:
        sc = con.execute("SELECT scoring_id, hit_config_json FROM tr_scorings WHERE season=? AND week=? ORDER BY scoring_id DESC LIMIT 1",
                         (season, week)).fetchone()
        if sc is None:
            return []
        sid = sc["scoring_id"]
        run = con.execute("SELECT run_id, run_slot FROM tr_runs WHERE season=? AND week=? ORDER BY CASE run_slot WHEN 'thu' THEN 0 ELSE 1 END "
                          "LIMIT 1", (season, week)).fetchone()
        ps = f"all_k{ODDS_TOP_N}"
        res = {r["model_name"]: dict(r) for r in con.execute(
            "SELECT * FROM tr_run_results WHERE scoring_id=? AND run_id=? AND pick_set=?", (sid, run["run_id"], ps))}
        pct = {r["model_name"]: r["percentile"] for r in con.execute(
            "SELECT model_name, percentile FROM tr_percentiles WHERE scoring_id=? AND run_id=? AND pick_set=? AND metric='spike_hits'",
            (sid, run["run_id"], ps))}
        out = []
        if "dart" in res:
            cut = json.loads(sc["hit_config_json"]).get("hit", {}).get("top_n_per_position")
            for model, label in (("odds_prod", "O.D.D.S."), ("odds_shadow", "O.D.D.S. shadow")):
                if model in res:
                    out.append(fact("odds_line", "league", week=week, model=label, run_slot=run["run_slot"], picks=ODDS_TOP_N,
                                    spike_weeks=int(res[model]["spike_hits"]), top_finishes=int(res[model]["top24_hits"]), top_finish_cutoff=cut,
                                    dart_average_spike_weeks=round(res["dart"]["spike_hits"], 1),
                                    dart_average_top_finishes=round(res["dart"]["top24_hits"], 1),
                                    beat_pct_of_dart_throws=round(pct[model]) if pct.get(model) is not None else None))
        snap = con.execute("SELECT s.snapshot_id, r.run_id, r.run_slot FROM tr_league_snapshots s JOIN tr_runs r ON r.run_id = s.run_id "
                           "WHERE r.season=? AND r.week=? AND s.ok=1 ORDER BY s.taken_at DESC LIMIT 1", (season, week)).fetchone()
        if snap is not None:
            status = {r["player_id"]: r["status"] for r in con.execute(
                "SELECT player_id, status FROM tr_league_availability WHERE snapshot_id=? AND player_id IS NOT NULL", (snap["snapshot_id"],))}
            results = {r["player_id"]: dict(r) for r in con.execute("SELECT * FROM tr_player_results WHERE scoring_id=?", (sid,))}
            picks = [dict(r) for r in con.execute("SELECT player_id, name, position, team, odds_prod_score FROM tr_pool WHERE run_id=? "
                                                  "ORDER BY odds_prod_score DESC, player_id", (snap["run_id"],)) if r["player_id"] in status]
            listed = picks[:ODDS_TOP_N]
            for place, p in enumerate(listed, start=1):
                r = results.get(p["player_id"])
                out.append(fact("odds_available_pick", "owner", week=week, run_slot=snap["run_slot"], place=place, player=p["name"],
                                position=p["position"], nfl_team=p["team"], status="waivers" if status[p["player_id"]] == "W" else "free agent",
                                points=round(r["points"], 2) if r else 0.0, played=r is not None, spike_week=bool(r and r["spike"]),
                                top_finish=bool(r and r["top24"])))
            if listed:
                got = [results.get(p["player_id"]) for p in listed]
                out.append(fact("odds_available_summary", "owner", week=week, run_slot=snap["run_slot"], listed=len(listed),
                                spike_weeks=sum(1 for r in got if r and r["spike"]), top_finishes=sum(1 for r in got if r and r["top24"])))
        return out
    except sqlite3.OperationalError:      # a DB without the tracker's tables: the recap just has no O.D.D.S. corner
        return []


# -------------------------------------------------------------------- rendering

def renamed_since(con, week_start):
    """{team_id: name} as of the newest teams snapshot taken before the league
    week starting on `week_start` (a date, Pacific). The recap compares it
    with today's names, so a rename gets its "formerly" note in the recap of
    the week it happened -- once: next week's starting point already has the
    new name. {} when nothing that old is on record."""
    start = datetime.combine(week_start, time.min, ld.LEAGUE_TZ).astimezone(timezone.utc).isoformat()
    snap = con.execute("SELECT snapshot_id FROM lg_snapshots WHERE resource='teams' AND ok=1 AND taken_at < ? "
                       "ORDER BY taken_at DESC, snapshot_id DESC LIMIT 1", (start,)).fetchone()
    if snap is None:
        return {}
    return {r[0]: r[1] for r in con.execute("SELECT team_id, team_name FROM lg_teams WHERE snapshot_id=?", (snap[0],))}


def owner_team_of(facts):
    return next((f["team_id"] for f in facts if f["kind"] == "owner_team"), None)


def visible(facts, audience):
    """The facts a rendering for `audience` may use -- and so the only ones a
    template is ever handed. league < team:<id> < owner; nothing addressed to
    another team, and nothing owner-only outside the owner's view."""
    if audience == "league":
        ok = {"league"}
    elif audience == "owner":
        ok = {"league", "owner", f"team:{owner_team_of(facts)}"}
    elif re.fullmatch(r"team:\d+", audience or ""):
        ok = {"league", audience}
    else:
        raise ValueError(f"unknown audience {audience!r} (league, team:<id> or owner)")
    return [f for f in facts if f["audience"] in ok]


def _of(facts, kind):
    return [f for f in facts if f["kind"] == kind]


def _pts(x):
    return "-" if x is None else f"{x:.2f}"


def _record(f):
    return f"{f['wins']}-{f['losses']}" + (f"-{f['ties']}" if f["ties"] else "")


def _streak(f):
    plural = {"win": "wins", "loss": "losses", "tie": "ties"}[f["streak_type"]]
    return f"{f['streak_length']} {f['streak_type'] if f['streak_length'] == 1 else plural}"


def _moved(f):
    return " / ".join(([("got " if f["how"] == "trade" else "+") + ", ".join(f["added"])] if f["added"] else [])
                      + ([("gave " if f["how"] == "trade" else "-") + ", ".join(f["dropped"])] if f["dropped"] else []))


def _league_sections(v):
    meta = _of(v, "meta")[0]
    week = meta["week"]
    out = [f"# Week {week} recap", ""]
    out += ["## Results", ""]
    for m in sorted(_of(v, "matchup"), key=lambda m: -m["margin"]):
        tag = " -- the closest game of the week" if m["closest"] else " -- the biggest blowout of the week" if m["blowout"] else ""
        verb = "tied" if m["tied"] else "def."
        out.append(f"- **{m['team_a']}** {_pts(m['score_a'])} {verb} **{m['team_b']}** {_pts(m['score_b'])} (margin {_pts(m['margin'])}){tag}")
    out += ["", "## Top and bottom", ""]
    scores = _of(v, "weekly_score")
    out.append("- High score: " + ", ".join(f"**{s['team']}** ({_pts(s['score'])})" for s in scores if s["is_high"]))
    out.append("- Low score: " + ", ".join(f"**{s['team']}** ({_pts(s['score'])})" for s in scores if s["is_low"]))
    for s in _of(v, "top_starter"):
        out.append(f"- Starter no. {s['place']}: {s['player']} ({s['position']}, {s['nfl_team']}) with {_pts(s['points'])} for {s['team']}")
    for s in _of(v, "top_bench"):
        out.append(f"- Best player left on a bench: {s['player']} ({s['position']}, {s['nfl_team']}), {_pts(s['points'])} for {s['team']}")
    stat = {f["team"]: f for f in _of(v, "season_stat")}
    out += ["", f"## Standings after week {week}", "",
            "| Rank | Team | Manager | Record | Points for | Points against | Streak |", "|---:|---|---|---|---:|---:|---|"]
    formerly = {f["team"]: f["old_name"] for f in _of(v, "team_renamed")}
    for f in _of(v, "standing"):
        shown = f["team"] + (f" (formerly {formerly[f['team']]})" if f["team"] in formerly else "")
        out.append(f"| {f['rank']} | {shown} | {f['manager']} | {_record(f)} | {_pts(f['points_for'])} | {_pts(f['points_against'])} | "
                   f"{_streak(f)} |")
    out += ["", "## Luck: record against the whole league", "",
            "All-play is each team's record if it had played every other team every week. Expected wins is what that all-play rate "
            "would have earned in the games actually played; luck is actual wins minus expected.", "",
            "| Team | All-play | Expected wins | Actual wins | Luck | Weekly highs | Weekly lows | Longest win streak |",
            "|---|---|---:|---:|---:|---:|---:|---:|"]
    for f in sorted(stat.values(), key=lambda f: -f["luck"]):
        ap = f"{f['all_play_wins']}-{f['all_play_losses']}" + (f"-{f['all_play_ties']}" if f["all_play_ties"] else "")
        out.append(f"| {f['team']} | {ap} | {_pts(f['expected_wins'])} | {f['actual_wins']:g} | {f['luck']:+.2f} | {f['weekly_highs']} | "
                   f"{f['weekly_lows']} | {f['longest_win_streak']} |")
    out += ["", "## Points left on the bench", "",
            "Best possible is the highest-scoring lineup the team could have set from its own roster that week.", "",
            "| Team | Lineup scored | Best possible | Left on bench | Season total left | Lineup efficiency |", "|---|---:|---:|---:|---:|---:|"]
    for f in sorted(_of(v, "bench"), key=lambda f: -f["left_on_bench"]):
        s = stat[f["team"]]
        eff = "-" if s["lineup_efficiency_pct"] is None else f"{s['lineup_efficiency_pct']:.1f}%"
        empty = f" ({f['empty_slots']} starting slot(s) left empty)" if f["empty_slots"] else ""
        out.append(f"| {f['team']}{empty} | {_pts(f['lineup_points'])} | {_pts(f['best_possible'])} | {_pts(f['left_on_bench'])} | "
                   f"{_pts(s['bench_left_season'])} | {eff} |")
    summary = _of(v, "transactions_summary")[0]
    out += ["", "## Transactions and FAAB", "",
            f"{summary['moves']} transaction(s) this week, {summary['waiver_claims']} of them waiver claims for a total of ${summary['faab_spent']}."
            + (f" Biggest winning bid: ${summary['biggest_bid']} by {summary['biggest_bid_team']} for {', '.join(summary['biggest_bid_added'])}."
               if summary["biggest_bid"] is not None else ""), ""]
    for f in _of(v, "transaction"):
        how = f"waiver claim, ${f['bid']}" if f["how"] == "waiver claim" else f["how"]
        out.append(f"- {f['day']} -- {f['team']} ({how}): {_moved(f)}")
    losing = _of(v, "losing_bid")
    if losing:
        out += ["", "Losing offers (typed by hand from Yahoo's FAB Offers tab):", ""]
        out += [f"- {f['team']} offered ${f['amount']} for {f['player']} (went to {f['winner']} for ${f['winning_amount']})" for f in losing]
    out += ["", "| Team | FAAB left | Spent this week | Spent this season |", "|---|---:|---:|---:|"]
    for f in sorted(_of(v, "faab"), key=lambda f: (f["remaining"] is None, -(f["remaining"] or 0))):
        out.append(f"| {f['team']} | {'?' if f['remaining'] is None else '$' + str(f['remaining'])} | ${f['spent_week']} | ${f['spent_season']} |")
    odds = _of(v, "odds_line")
    if odds:
        out += ["", "## O.D.D.S. corner (for fun, not advice)", ""]
        for f in odds:
            beat = "" if f["beat_pct_of_dart_throws"] is None else f" -- better than {f['beat_pct_of_dart_throws']}% of dart throws"
            out.append(f"- {f['model']}: {f['spike_weeks']} of its top {f['picks']} picks had a spike week and {f['top_finishes']} finished "
                       f"top-{f['top_finish_cutoff']} at their position; random darts average {f['dart_average_spike_weeks']} and "
                       f"{f['dart_average_top_finishes']}{beat}.")
    checks = [f for f in _of(v, "score_check") if f["week"] == week]
    if checks:
        out += ["", "## Notes", ""]
        out += [f"- {f['team']}: the official score is {_pts(f['scoreboard_score'])}; its starters' individual points add up to "
                f"{_pts(f['lineup_points'])} (a difference of {_pts(abs(f['difference']))}). The official score is used for results." for f in checks]
    return out


def _team_sections(v, title):
    out = []
    for f in _of(v, "bench_detail"):
        out += ["", f"## {title}: {f['team']}", "",
                f"Week result: {f['result']} {_pts(f['score'])} to {_pts(f['opponent_score'])} against {f['opponent']}. "
                f"Points left on the bench: {_pts(f['left_on_bench'])}."
                + (" The best lineup would have won this matchup." if f["would_have_flipped"] else "")]
        for s in f["swaps"]:
            if s["start"] is None:
                out.append(f"- Leave the slot empty rather than start {s['sit']} ({_pts(s['sit_points'])}): +{_pts(s['gain'])}")
                continue
            out.append(f"- Start {s['start']} ({_pts(s['start_points'])})" + (f" over {s['sit']} ({_pts(s['sit_points'])})" if s["sit"] else
                                                                                " in the slot left empty") + f": +{_pts(s['gain'])}")
    margins = _of(v, "bid_margin")
    if margins:
        out += ["", "Winning bids this week:", ""]
        for m in margins:
            out.append(f"- {', '.join(m['added'])}: won at ${m['won_at']}"
                       + (f", next offer ${m['next_offer']} ({m['next_team']}), margin ${m['margin']}" if m["next_offer"] is not None else
                          ", no losing offer entered (uncontested, or not typed in yet)"))
    return out


def _owner_sections(v):
    out = ["", "## Owner only", ""]
    profiles = _of(v, "bidder_profile")
    if profiles:
        p0 = profiles[0]
        out += [f"Bidding over weeks {p0['from_week']}-{p0['to_week']} ({p0['basis']}):", "",
                "| Team | Manager | Claims won | Losing bids | Bids | Active bidder | FAAB left |", "|---|---|---:|---:|---:|---|---:|"]
        for p in sorted(profiles, key=lambda p: -p["bids"]):
            out.append(f"| {p['team']} | {p['manager']} | {p['claims_won']} | {p['losing_bids']} | {p['bids']} | {'yes' if p['active'] else 'no'} | "
                       f"{'?' if p['faab_remaining'] is None else '$' + str(p['faab_remaining'])} |")
    for s in _of(v, "odds_available_summary"):
        out += ["", f"What O.D.D.S. ranked highest among players available in this league ({s['run_slot']} run): {s['spike_weeks']} of "
                    f"{s['listed']} had a spike week, {s['top_finishes']} a top finish.", ""]
        for p in _of(v, "odds_available_pick"):
            how = "spike week" if p["spike_week"] else "top finish" if p["top_finish"] else "did not play" if not p["played"] else "no hit"
            out.append(f"- No. {p['place']}: {p['player']} ({p['position']}, {p['nfl_team']}; {p['status']}) -- {_pts(p['points'])} points, {how}")
    return out


def render_recap(data, audience):
    """The full markdown recap for one audience, from the facts it may see and nothing else."""
    v = visible(data["facts"], audience)
    out = _league_sections(v)
    if audience != "league":
        out += _team_sections(v, "For this team only")
    if audience == "owner":
        out += _owner_sections(v)
    return "\n".join(out) + "\n"


def render_group_chat(data):
    """The short version for the league's group chat: league facts only."""
    v = visible(data["facts"], "league")
    meta, scores = _of(v, "meta")[0], _of(v, "weekly_score")
    out = [f"Week {meta['week']} recap"]
    out.append("Results: " + "; ".join(f"{m['team_a']} {_pts(m['score_a'])} {'tied' if m['tied'] else 'def.'} {m['team_b']} {_pts(m['score_b'])}"
                                       for m in sorted(_of(v, "matchup"), key=lambda m: -m["margin"])))
    out.append("High score: " + ", ".join(f"{s['team']} ({_pts(s['score'])})" for s in scores if s["is_high"])
               + ". Low score: " + ", ".join(f"{s['team']} ({_pts(s['score'])})" for s in scores if s["is_low"]) + ".")
    for s in _of(v, "top_starter")[:1]:
        out.append(f"Player of the week: {s['player']}, {_pts(s['points'])} for {s['team']}.")
    out.append("Standings: " + ", ".join(f"{f['rank']}. {f['team']} ({_record(f)})" for f in _of(v, "standing")))
    for f in _of(v, "team_renamed"):
        out.append(f"New name: {f['team']} is the team formerly known as {f['old_name']}.")
    worst = max(_of(v, "bench"), key=lambda f: f["left_on_bench"])
    out.append(f"Most points left on the bench: {worst['team']} ({_pts(worst['left_on_bench'])}).")
    lucky = sorted(_of(v, "season_stat"), key=lambda f: f["luck"])
    out.append(f"Luckiest so far: {lucky[-1]['team']} ({lucky[-1]['luck']:+.2f} wins vs all-play). Unluckiest: {lucky[0]['team']} ({lucky[0]['luck']:+.2f}).")
    summary = _of(v, "transactions_summary")[0]
    out.append(f"Waivers: {summary['waiver_claims']} claim(s), ${summary['faab_spent']} spent"
               + (f"; biggest bid ${summary['biggest_bid']} by {summary['biggest_bid_team']}." if summary["biggest_bid"] is not None else "."))
    for f in _of(v, "odds_line")[:1]:
        out.append(f"O.D.D.S. corner (for fun, not advice): {f['spike_weeks']} of its top {f['picks']} had a spike week; random darts "
                   f"average {f['dart_average_spike_weeks']}.")
    return "\n".join(out) + "\n"


def render_facts_md(data):
    """Every fact, grouped by audience and kind, for reading."""
    out = ["# Facts", "", "LOCAL ONLY. Every number in the recap comes from this list; prose may not add to it.", ""]
    by = defaultdict(list)
    for f in data["facts"]:
        by[(f["audience"], f["kind"])].append(f)
    for (audience, kind), rows in sorted(by.items(), key=lambda kv: (0 if kv[0][0] == "league" else 2 if kv[0][0] == "owner" else 1, kv[0])):
        out += [f"## {audience} / {kind}", ""]
        out += ["- " + "; ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in f.items() if k not in ("kind", "audience")) for f in rows]
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------------------ check

def _numbers(obj, out=None, printed=False):
    """Every number in a fact structure, as absolute values rounded to cents:
    numeric fields, and digit runs inside the strings a template prints as
    they are (a date like 'Wed 10-07', a team with a number in its name) -- but
    not inside other strings, such as the time a snapshot was taken."""
    out = set() if out is None else out
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out.add(round(abs(float(obj)), 2))
    elif isinstance(obj, str):
        if printed:
            out.update(round(float(t), 2) for t in NUMBER.findall(obj))
    elif isinstance(obj, dict):
        for key, value in obj.items():
            _numbers(value, out, printed=key in PRINTED_TEXT_KEYS)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _numbers(value, out, printed=printed)
    return out


def _names(obj, out=None, named=False):
    out = set() if out is None else out
    if isinstance(obj, str):
        if named and len(obj) >= 3:
            out.add(obj)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            _names(value, out, named=key in NAME_KEYS)
    elif isinstance(obj, (list, tuple)):
        for value in obj:
            _names(value, out, named=named)
    return out


def check_text(text, data, audience):
    """Problems with a piece of prose for `audience`, [] if none: a number that
    is not in the facts that audience may see, or the name of a team, manager
    or player that is known to the facts but only outside that audience."""
    allowed = visible(data["facts"], audience)
    numbers, ok_names = _numbers(allowed), _names(allowed)
    problems = [f"the number {t} is not in the facts for audience '{audience}'" for t in dict.fromkeys(NUMBER.findall(text))
                if round(float(t), 2) not in numbers]
    for name in sorted(_names(data["facts"]) - ok_names):
        if name in text and not any(name in ok for ok in ok_names):
            problems.append(f"'{name}' is a fact for another audience, not for '{audience}'")
    return problems


# -------------------------------------------------------------------------- CLI

def week_dir(out_dir, data):
    meta = _of(data["facts"], "meta")[0]
    return Path(out_dir) / f"{meta['season']}_week{meta['week']:02d}"


def cmd_build(args):
    con = ld.connect(args.db)
    try:
        data = build_facts(con, args.week, owner_team=args.team)
    finally:
        con.close()
    folder = week_dir(args.out_dir, data)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "facts.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "facts.md").write_text(render_facts_md(data), encoding="utf-8")
    outputs = (("league_recap.md", "league", render_recap(data, "league")), ("league_groupchat.txt", "league", render_group_chat(data)),
               ("owner_recap.md", "owner", render_recap(data, "owner")))
    bad = 0
    for file_name, audience, text in outputs:
        (folder / file_name).write_text(text, encoding="utf-8")
        problems = check_text(text, data, audience)
        bad += len(problems)
        for problem in problems:
            print(f"[NEWSLETTER] CHECK FAILED in {file_name}: {problem}")
    counts = defaultdict(int)
    for f in data["facts"]:
        counts[f["audience"].split(":")[0]] += 1
    print(f"[NEWSLETTER] week {args.week}: {len(data['facts'])} facts ({counts['league']} league, {counts['team']} team, {counts['owner']} owner); "
          f"wrote facts.json, facts.md and {len(outputs)} renderings to {folder}" + ("" if not bad else f" -- {bad} CHECK FAILURE(S)"))
    return 1 if bad else 0


def cmd_check(args):
    folder = Path(args.out_dir)
    matches = sorted(folder.glob(f"*_week{args.week:02d}/facts.json"))
    if not matches:
        raise NewsletterError(f"no facts.json for week {args.week} under {folder} -- run `build` first")
    data = json.loads(matches[-1].read_text(encoding="utf-8"))
    problems = check_text(Path(args.file).read_text(encoding="utf-8"), data, args.audience)
    for problem in problems:
        print(f"[NEWSLETTER] {problem}")
    print(f"[NEWSLETTER] {args.file}: " + ("every number and name is in the facts for this audience" if not problems else
                                           f"{len(problems)} problem(s)"))
    return 1 if problems else 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build", help="compute the facts for a finished week and write every rendering")
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--team", type=int, default=None, help="team id the owner's view is for (default: the logged-in account's team)")
    p.add_argument("--db", default=str(ld.DEFAULT_DB))
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.set_defaults(fn=cmd_build)
    p = sub.add_parser("check", help="check a piece of prose against a week's facts")
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--audience", default="league", help="league, team:<id> or owner")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p.set_defaults(fn=cmd_check)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except NewsletterError as e:
        print(f"[NEWSLETTER] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
