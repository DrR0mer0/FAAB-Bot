#!/usr/bin/env python3
"""Build a start/sit week file (lineup_sim's contract, schema_version 1) from
the league data the project already has.

  python startsit/build_week.py --season 2026 --week 7 --stage thursday
  python startsit/build_week.py --season 2026 --week 7 --stage sunday --run
  python startsit/build_week.py ... --projections csv --projections-file my.csv

Writes league_data/<season>_week<NN>_<stage>.json (gitignored: it names the
league's teams), and with --run also <same>_report.md / _report.json from
lineup_sim.run_week.

WHERE EACH FIELD COMES FROM
  league.slots        lg_roster_slots, newest settings snapshot, passed straight
                      through (BN/IR included; lineup_sim ignores them).
  my_team.players     the whole roster -- bench and IR too -- from the newest
                      roster snapshot if it is for this week, else this week's
                      final lineup (lg_lineups: past weeks).
  opponent.starters   this week's opponent (lg_matchups), non-BN/IR slots only,
                      from the same source. Missing: left out, with a warning --
                      never guessed.
  team / opp          nflverse's weekly roster team when it has one (else
                      Yahoo's, LAR -> LA), and that week's opponent from
                      nfl_games. No game that week: status BYE. A Yahoo team
                      code the schedule doesn't know stops the build.
  status              BYE, else a long-term status from nflverse's weekly roster
                      (RES -> IR, PUP, SUS -> SUSP, EXE -> EXEMPT, CUT/RET -> O),
                      else the week's injury designation (tracker/injuries.py,
                      resolved like a hand-run score_week.py: Out -> O,
                      Doubtful -> D, Questionable -> Q). play_prob is left to
                      lineup_sim's own STATUS_PLAY_PROB.
                      A long-term EXE / RES / PUP / SUS stays authoritative
                      unless YAHOO's current status for the player contradicts
                      it (lg_rosters.status, captured with the roster snapshot):
                      then Yahoo's status is used -- no status = available,
                      subject to the week's injury report -- and the conflict
                      is a STATUS CONFLICT warning and a _meta entry. ESPN is a
                      cross-check only: a disagreement adds a warning, never a
                      decision. Without a Yahoo status (a past week built from
                      final lineups, or a snapshot from before 2026-10-22) the
                      long-term status stands. Found 2026-10-22: nflverse kept
                      Josh Jacobs on the exempt list after his reinstatement;
                      Yahoo had tracked it (CEL on 10-15, nothing on 10-22) while
                      ESPN said DAY_TO_DAY throughout. CUT / RET stay as is.
                      The other direction, for MY players, in every stage (from
                      2026-10-28): if Yahoo's current status means he can't play
                      (play chance 0: O, NA, IR, PUP, NFI, SUSP, CEL -- e.g. a
                      game-day inactive), it wins over a healthier nflverse /
                      injury-report reading, also as a STATUS CONFLICT (kind
                      "yahoo out"). Opponent starters keep the usual reading.
  p10 / p50 / p90     a projection provider (startsit/projections.py). A player
                      of MINE with no p50 is DROPPED with a warning: the
                      simulator must never start or bench someone on an invented
                      number -- decide him by hand, or give him a csv row. An
                      OPPONENT starter with no p50 keeps a labelled fallback (the
                      median p50 of that position in this file): he is starting,
                      and leaving him out would overstate the win probability.
                      Out / bye players need no p50.
  locked / started /  --stage sunday only. A game whose scheduled kickoff has
  locked_slot /       passed is locked; started / locked_slot come from the
  actual              newest roster snapshot, which must have been taken AFTER
                      that kickoff -- otherwise the build STOPS (a recommendation
                      that moves a locked player is worse than none). actual =
                      Yahoo's points for a completed week (lg_lineups), else the
                      league-scored nflverse stats once they are published.
  _meta               sources, ages and every flagged player (ignored by lineup_sim).
"""
import argparse
import json
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
for d in ("league", "tracker", "model", "data"):
    sys.path.insert(0, str(REPO_ROOT / d))
sys.path.insert(0, str(HERE))

import injuries  # noqa: E402
import league_data as ld  # noqa: E402
import lineup_sim  # noqa: E402
import projections  # noqa: E402
import scoring  # noqa: E402
from score_week import load_current_roster, parse_kickoff_utc  # noqa: E402 -- the one place kickoff timezone logic lives
from team_crosswalk import norm_team  # noqa: E402

DEFAULT_DB = REPO_ROOT / "faab_history_core_v0_1.db"
DEFAULT_RAW_DIR = REPO_ROOT / "nflverse_raw"
DEFAULT_OUT_DIR = REPO_ROOT / "league_data"
STAGES = ("thursday", "sunday")
BENCH = {"BN", "IR", "IR+", "NA"}
YAHOO_TEAM_CODES = {"LAR": "LA"}                 # Yahoo's code -> nflverse's; checked 2026-10-22: the only difference
NFLVERSE_STATUS = {"RES": "IR", "PUP": "PUP", "SUS": "SUSP", "EXE": "EXEMPT", "CUT": "O", "RET": "O"}
DESIGNATION = {"Out": "O", "Doubtful": "D", "Questionable": "Q"}
# Long-term statuses a current-week source may contradict (2026-10-22: nflverse still had Josh Jacobs on the exempt list in
# week 7, three days after his reinstatement). CUT / RET (not on any roster) stay authoritative.
OVERRIDABLE = {"EXE", "RES", "PUP", "SUS"}
ESPN_CURRENT = {"ACTIVE": "ACTIVE", "DAY_TO_DAY": "GTD", "QUESTIONABLE": "Q", "DOUBTFUL": "D", "OUT": "O",
                "INJURY_RESERVE": "IR", "SUSPENSION": "SUSP"}
# Yahoo's player status -> lineup_sim's; "" (no status) = available. CEL = commissioner exempt list; NA = not active.
YAHOO_STATUS = {"": "ACTIVE", "Q": "Q", "D": "D", "O": "O", "GTD": "GTD", "DTD": "GTD", "IR": "IR", "IR-R": "IR", "IR-NR": "IR",
                "PUP-R": "PUP", "PUP-P": "PUP", "PUP": "PUP", "NFI-R": "NFI", "NFI-A": "NFI", "NFI": "NFI", "SUSP": "SUSP",
                "CEL": "EXEMPT", "NA": "O", "COVID-19": "O"}


def play_chance(code):
    """lineup_sim's own reading of a status: 0 for an out status, its STATUS_PLAY_PROB, else 1."""
    if code in lineup_sim.OUT_STATUSES:
        return 0.0
    return lineup_sim.STATUS_PLAY_PROB.get(code, 1.0)
SLOT_ORDER = ["QB", "WR", "RB", "TE", "W/R/T", "W/R", "W/T", "Q/W/R/T", "K", "DEF", "BN", "IR", "IR+", "NA"]


class BuildError(Exception):
    """The week file can't be built honestly (exit code 2)."""


def connect_ro(path):
    # a busy timeout like the writers' (ledger / league_data: 30 s), so a reader never gives up while a log job writes
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def league_slots(con):
    snap = ld.latest_ok(con, "settings")
    if snap is None:
        raise BuildError("no league settings snapshot -- run `python league/league_data.py snapshot` first")
    rows = con.execute("SELECT position, slot_count FROM lg_roster_slots WHERE snapshot_id=?", (snap[0],)).fetchall()
    order = {p: i for i, p in enumerate(SLOT_ORDER)}
    return [r["position"] for r in sorted(rows, key=lambda r: (order.get(r["position"], 50), r["position"]))
            for _ in range(r["slot_count"])]


def roster_source(con, week):
    """(rows by team, label, snapshot taken_at or None): the newest roster snapshot if it is for `week`, else that week's
    final lineups. Each row: external_id, player_id (gsis), name, position, team, selected_position[, points]."""
    snap = ld.latest_ok(con, "rosters")
    if snap:
        rows = con.execute("SELECT * FROM lg_rosters WHERE snapshot_id=?", (snap[0],)).fetchall()
        if rows and all(r["week"] == week for r in rows):
            out = {}
            for r in rows:
                out.setdefault(r["team_id"], []).append(dict(r))
            return out, f"roster snapshot {snap[1][:16]} UTC", snap[1]
    lineups = ld.week_lineups(con, week)
    if lineups:
        return lineups, f"final lineups of week {week} (lg_lineups)", None
    raise BuildError(f"no roster for week {week}: the newest roster snapshot is for another week and no final lineup of week "
                     f"{week} is stored")


def opponent_of(con, week, team_id):
    snap = ld.latest_ok(con, "scoreboard")
    row = con.execute("SELECT opponent_team_id FROM lg_matchups WHERE snapshot_id=? AND week=? AND team_id=?",
                      (snap[0], week, team_id)).fetchone() if snap else None
    return row["opponent_team_id"] if row else None


def schedule(con, season, week):
    """({team: (opponent, kickoff_utc)}, every team code of the season)."""
    games, teams = {}, set()
    for r in con.execute("SELECT week, kickoff_utc, home_team, away_team FROM nfl_games WHERE season=? AND is_playoffs=0", (season,)):
        h, a = norm_team(r["home_team"]), norm_team(r["away_team"])
        teams |= {h, a}
        if r["week"] == week:
            k = parse_kickoff_utc(r["kickoff_utc"])
            games[h], games[a] = (a, k), (h, k)
    return games, teams


def team_names(con):
    snap = ld.latest_ok(con, "teams")
    return {r["team_id"]: r["team_name"] for r in con.execute("SELECT team_id, team_name FROM lg_teams WHERE snapshot_id=?", (snap[0],))} if snap else {}


def build_week(con, season, week, stage, team_id=None, provider=None, as_of=None, raw_dir=DEFAULT_RAW_DIR,
               injury_fetch=injuries.fetch_nflverse, weekly_roster=None):
    """-> (week dict, warnings). Raises BuildError."""
    if stage not in STAGES:
        raise BuildError(f"--stage must be one of {STAGES}")
    as_of = as_of or datetime.now(timezone.utc)
    warnings = []
    team_id = team_id if team_id is not None else ld.default_team(con)
    if team_id is None:
        raise BuildError("no team id given and none is marked as mine in the newest teams snapshot")
    slots = league_slots(con)
    rosters, source, roster_taken = roster_source(con, week)
    if team_id not in rosters:
        raise BuildError(f"team {team_id} has no roster in the {source}")
    games, season_teams = schedule(con, season, week)
    if not season_teams:
        raise BuildError(f"no {season} schedule in nfl_games")
    weekly_roster = Path(weekly_roster or Path(raw_dir) / f"roster_weekly_{season}.csv")
    nfl_team, nfl_status = ({}, {})
    if weekly_roster.exists():
        nfl_team, _tpr, _draft, nfl_status = load_current_roster(str(weekly_roster), season, week)
    else:
        warnings.append(f"no nflverse weekly roster at {weekly_roster}: teams are Yahoo's and long-term statuses are unknown")
    inj = injuries.resolve_designations(con, season, week, as_of, fetch=injury_fetch)
    store = scoring.StatsStore(raw_dir, scoring.load_config()["scoring"])
    provider = provider or projections.BaselineProvider(store, scoring_label=f", scoring hash {scoring.config_hash(scoring.load_config())[:10]}")

    opp_id = opponent_of(con, week, team_id)
    names = team_names(con)
    sides = {"mine": rosters[team_id]}
    if opp_id is None:
        warnings.append(f"no week-{week} opponent in the newest scoreboard snapshot: the file has no opponent, so lineup_sim "
                        f"maximizes points instead of win probability")
    elif opp_id not in rosters:
        warnings.append(f"the opponent's week-{week} lineup is not in the {source}: opponent left out, not guessed")
        opp_id = None
    else:
        sides["opp"] = [r for r in rosters[opp_id] if (r.get("selected_position") or "BN") not in BENCH]

    mismatches, unknown_codes, players, conflicts = [], set(), {"mine": [], "opp": []}, []
    espn_raw = inj.get("espn_raw") or {}
    espn_notes = []
    for side, rows in sides.items():
        for r in rows:
            yahoo_team = YAHOO_TEAM_CODES.get(r["team"], r["team"]) if r["team"] else None
            if yahoo_team and norm_team(yahoo_team) not in season_teams:
                unknown_codes.add(r["team"])
            gsis = r.get("player_id")
            team = norm_team(nfl_team.get(gsis) or yahoo_team) if (gsis or yahoo_team) else None
            if gsis in nfl_team and yahoo_team and norm_team(yahoo_team) != norm_team(nfl_team[gsis]):
                mismatches.append(f"{r['name']}: Yahoo {r['team']}, nflverse {nfl_team[gsis]} (nflverse used)")
            opp, kickoff = games.get(team, (None, None))
            status = None
            designation = DESIGNATION.get(inj["status"].get(gsis))
            espn = ESPN_CURRENT.get(espn_raw.get(gsis))
            long_term = nfl_status.get(gsis)
            raw_yahoo = r.get("status")                      # None: not captured (says nothing); "": no status
            yahoo = YAHOO_STATUS.get(raw_yahoo) if raw_yahoo is not None else None
            if raw_yahoo is not None and raw_yahoo not in YAHOO_STATUS:
                warnings.append(f"{r['name']}: unknown Yahoo status {raw_yahoo!r} -- ignored; add it to YAHOO_STATUS")
            if team and team not in games:
                status = "BYE"
            elif long_term in NFLVERSE_STATUS:
                status = NFLVERSE_STATUS[long_term]
                if long_term in OVERRIDABLE and yahoo is not None and play_chance(yahoo) > 0:
                    # Yahoo's current status says he may play: it wins over the weekly roster, never silently
                    status = designation if yahoo == "ACTIVE" else yahoo
                    conflicts.append({"name": r["name"], "gsis_id": gsis, "kind": "long-term overruled",
                                      "nflverse_weekly_roster": long_term,
                                      "yahoo": raw_yahoo, "injury_report": inj["status"].get(gsis), "espn": espn_raw.get(gsis),
                                      "used": status or "available", "play_chance": play_chance(status)})
                if espn is not None and (play_chance(espn) > 0) != (play_chance(status) > 0):
                    espn_notes.append(f"{r['name']}: ESPN says {espn_raw.get(gsis)}, the file has him "
                                      f"{'in' if play_chance(status) > 0 else 'out'} ({status or 'available'}) -- cross-check only")
            elif designation:
                status = designation
            if side == "mine" and yahoo is not None and play_chance(yahoo) == 0 and play_chance(status) > 0:
                # Yahoo's current status says one of MY players can't play (O, NA, IR, ...; e.g. a game-day inactive):
                # it wins over a healthier nflverse / injury-report reading, in every stage, and never silently (2026-10-28)
                conflicts.append({"name": r["name"], "gsis_id": gsis, "kind": "yahoo out", "nflverse_weekly_roster": long_term,
                                  "yahoo": raw_yahoo, "injury_report": inj["status"].get(gsis), "espn": espn_raw.get(gsis),
                                  "was": status or "available", "used": yahoo, "play_chance": 0.0})
                status = yahoo
            players[side].append({"id": r["external_id"], "name": r["name"], "pos": r["position"], "team": team, "opp": opp,
                                  "status": status, "gsis_id": gsis, "yahoo_slot": r.get("selected_position"),
                                  "_kickoff": kickoff, "_points": r.get("points")})
    for c in conflicts:
        yahoo_said = f"Yahoo shows {'no status' if c['yahoo'] == '' else c['yahoo']}"
        extra = "; ".join(f"{k} {c[k2]}" for k, k2 in (("injury report", "injury_report"), ("ESPN", "espn")) if c[k2])
        if c["kind"] == "yahoo out":
            nfl = c["nflverse_weekly_roster"]
            warnings.append(f"STATUS CONFLICT: {c['name']} -- {yahoo_said}, but the file had him {c['was']} (nflverse's weekly "
                            f"roster {nfl or 'no row'}" + (f"; {extra}" if extra else "") + f"). Using Yahoo: {c['used']} "
                            f"(play chance 0.00) -- check him before relying on this lineup")
            continue
        warnings.append(f"STATUS CONFLICT: {c['name']} -- nflverse's weekly roster says {c['nflverse_weekly_roster']}, but {yahoo_said}"
                        + (f" ({extra})" if extra else "") + f". Using Yahoo: {c['used']} (play chance {c['play_chance']:.2f}) -- "
                        f"check him before relying on this lineup")
    for n in espn_notes:
        warnings.append(f"ESPN CROSS-CHECK: {n}")
    if unknown_codes:
        raise BuildError(f"Yahoo team code(s) {sorted(unknown_codes)} are not in the {season} nflverse schedule -- add them to "
                         f"YAHOO_TEAM_CODES after checking")

    # ---- Sunday: locks from real kickoff times and a roster snapshot taken after them
    lock_notes = []
    if stage == "sunday":
        started_games = sorted({(p["team"], p["_kickoff"]) for side in players.values() for p in side
                                if p["_kickoff"] and p["_kickoff"] <= as_of}, key=lambda x: x[1])
        if started_games and roster_taken is not None:
            taken = datetime.fromisoformat(roster_taken)
            late = [f"{t} (kicked off {k:%a %H:%M} UTC)" for t, k in started_games if k >= taken]
            if late:
                raise BuildError(f"game(s) of {', '.join(late)} have kicked off but the newest roster snapshot was taken "
                                 f"{taken:%a %m-%d %H:%M} UTC, before that -- the lock state of those players isn't on record. "
                                 f"Run `python league/league_data.py snapshot` and build again")
        week_stats = store.week_rows(season, week)
        for side in players.values():
            for p in side:
                if p["_kickoff"] and p["_kickoff"] <= as_of:
                    p["locked"] = True
                    p["started"] = (p["yahoo_slot"] or "BN") not in BENCH
                    if p["started"]:
                        p["locked_slot"] = p["yahoo_slot"]
                    if p["_points"] is not None:
                        p["actual"] = round(float(p["_points"]), 2)
                    elif p["gsis_id"] in week_stats:
                        p["actual"] = round(week_stats[p["gsis_id"]]["points"], 2)
        lock_notes.append(f"{len(started_games)} game(s) kicked off by {as_of:%a %m-%d %H:%M} UTC; lock state from the "
                          f"{source}")
    else:
        if any(p["_kickoff"] and p["_kickoff"] <= as_of for side in players.values() for p in side):
            warnings.append("some games have already kicked off: a thursday-stage file ignores that -- use --stage sunday")

    # ---- projections
    need = [p for side in players.values() for p in side if p.get("actual") is None]
    proj = provider.project(season, week, need)
    warnings += proj.warnings
    no_proj, dropped, fallback = [], [], []
    for side in ("mine", "opp"):
        for p in players[side]:
            v = proj.values.get(p["id"])
            if v:
                p.update(v)
            flags = proj.flags.get(p["id"], [])
            if flags:
                p["note"] = "; ".join(flags)
    manual = []
    for side in ("mine", "opp"):
        for p in players[side]:
            if p["id"] in proj.play_prob:
                x = proj.play_prob[p["id"]]
                was = p.get("status")
                p["play_prob"] = x
                p["status"] = None              # lineup_sim zeroes any out status: the manual chance must not be undone
                note = f"manual play_prob {x:g}" + (f" (status was {was})" if was else "")
                p["note"] = "; ".join(filter(None, [p.get("note"), note]))
                manual.append({"name": p["name"], "play_prob": x, "status_was": was, "source": "csv override"})
    for m in manual:
        set_aside = f" (status {m['status_was']} set aside)" if m["status_was"] else ""
        warnings.append(f"MANUAL play_prob: {m['name']} {m['play_prob']:g}{set_aside}")
    pos_p50 = {}
    for p in players["mine"] + players["opp"]:
        if p.get("p50") is not None:
            pos_p50.setdefault(p["pos"], []).append(p["p50"])
    for side in ("mine", "opp"):
        kept = []
        for p in players[side]:
            out = p["status"] in lineup_sim.OUT_STATUSES
            if p.get("p50") is None and p.get("actual") is None and not out:
                why = "; ".join(proj.flags.get(p["id"], ["no projection"]))
                if side == "mine":
                    dropped.append(f"{p['name']} ({p['pos']}): {why}")
                    continue
                if not pos_p50.get(p["pos"]):
                    dropped.append(f"opponent {p['name']} ({p['pos']}): {why}, and no {p['pos']} projection to fall back on")
                    continue
                p["p50"] = round(statistics.median(pos_p50[p["pos"]]), 2)
                p["note"] = f"FALLBACK projection: {p['pos']} median in this file ({why})"
                fallback.append(f"{p['name']} ({p['pos']}) -> {p['p50']}")
            if p.get("p50") is None and p.get("actual") is None:
                no_proj.append(p["name"])
            kept.append(p)
        players[side] = kept
    for d in dropped:
        warnings.append(f"NO PROJECTION, left out of the week file: {d} -- decide this player by hand, or add a csv row")
    for f in fallback:
        warnings.append(f"NO PROJECTION for opponent starter {f} (fallback, labelled in the file)")

    def clean(p):
        return {k: v for k, v in p.items() if not k.startswith("_") and v is not None}

    lm = inj.get("last_modified")
    week_file = {
        "schema_version": lineup_sim.SCHEMA_VERSION, "season": season, "week": week, "stage": stage,
        "league": {"slots": slots},
        "my_team": {"name": names.get(team_id, f"team {team_id}"), "players": [clean(p) for p in players["mine"]]},
        "_meta": {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "as_of": as_of.isoformat(timespec="seconds"),
            "team_id": team_id, "opponent_team_id": opp_id, "roster_source": source,
            "injuries": {"source": inj["source"], "reason": inj.get("reason"), "nflverse_sha256": inj.get("sha256"),
                         "nflverse_last_modified": lm.isoformat() if lm else None,
                         "age_hours": round((as_of - lm).total_seconds() / 3600, 1) if lm else None,
                         "resolved_by": "injuries.resolve_designations (as a hand-run score_week.py resolves them)",
                         "espn_snapshot_taken_at": inj.get("espn_taken_at")},
            "status_conflicts": conflicts, "espn_cross_check": espn_notes, "manual_overrides": manual,
            "yahoo_status": ("captured with the roster snapshot" if any(r.get("status") is not None for rows in sides.values() for r in rows)
                             else "not available (a past week, or a snapshot from before 2026-10-22): long-term statuses stand"),
            "projections": {"provider": getattr(provider, "name", "?"), "provenance": proj.provenance,
                            "flagged": {p["name"]: proj.flags[p["id"]] for side in players.values() for p in side if p["id"] in proj.flags},
                            "dropped_no_projection": dropped, "fallback": fallback},
            "team_mismatches": mismatches, "locks": lock_notes, "warnings": list(warnings),
        },
    }
    if opp_id is not None:
        week_file["opponent"] = {"name": names.get(opp_id, f"team {opp_id}"), "starters": [clean(p) for p in players["opp"]]}
    try:
        lineup_sim.parse_week(week_file)
    except lineup_sim.WeekFileError as e:
        raise BuildError("the built file does not satisfy lineup_sim's contract:\n  " + "\n  ".join(e.problems)) from e
    return week_file, warnings


def default_out(season, week, stage):
    return DEFAULT_OUT_DIR / f"{season}_week{week:02d}_{stage}.json"


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--stage", choices=STAGES, required=True)
    ap.add_argument("--team-id", type=int, default=None, help="default: the team the logged-in Yahoo account owns")
    ap.add_argument("--projections", choices=("baseline", "csv"), default="baseline")
    ap.add_argument("--projections-file", default=None, help="with --projections csv: id/name,p50[,p10,p90] overrides")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    ap.add_argument("--out", default=None, help="default: league_data/<season>_week<NN>_<stage>.json")
    ap.add_argument("--as-of", default=None, help="ISO instant to build as of (default now; testing / backtests)")
    ap.add_argument("--run", action="store_true", help="also run lineup_sim and save <out>_report.md / _report.json")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sims", type=int, default=lineup_sim.DEFAULT_SIMS)
    return ap


def main(argv=None, injury_fetch=injuries.fetch_nflverse):
    args = build_parser().parse_args(argv)
    as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
    if as_of is not None and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    con = connect_ro(args.db)
    try:
        store = scoring.StatsStore(args.raw_dir, scoring.load_config()["scoring"])
        provider = projections.BaselineProvider(store, scoring_label=f", scoring hash {scoring.config_hash(scoring.load_config())[:10]}")
        if args.projections == "csv":
            if not args.projections_file:
                print("[WEEK FILE] --projections csv needs --projections-file", file=sys.stderr)
                return 2
            provider = projections.CsvProvider(args.projections_file, provider)
        week_file, warnings = build_week(con, args.season, args.week, args.stage, args.team_id, provider, as_of, args.raw_dir,
                                         injury_fetch)
    except BuildError as e:
        print(f"[WEEK FILE] NOT BUILT: {e}", file=sys.stderr)
        return 2
    finally:
        con.close()
    out = Path(args.out) if args.out else default_out(args.season, args.week, args.stage)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(week_file, indent=1, ensure_ascii=False), encoding="utf-8")
    for w in warnings:
        print(f"[WEEK FILE] WARNING: {w}")
    meta = week_file["_meta"]
    print(f"[WEEK FILE] {out}: {len(week_file['my_team']['players'])} of my players, "
          f"{len(week_file.get('opponent', {}).get('starters', []))} opponent starters; injuries {meta['injuries']['source']}; "
          f"{len(meta['projections']['flagged'])} flagged, {len(meta['projections']['dropped_no_projection'])} dropped")
    if args.run:
        result = lineup_sim.run_week(week_file, n_sims=args.sims, seed=args.seed)
        stem = out.with_suffix("")
        Path(f"{stem}_report.md").write_text(result["report_md"], encoding="utf-8")
        Path(f"{stem}_report.json").write_text(json.dumps({k: v for k, v in result.items() if k != "report_md"}, indent=1,
                                                          ensure_ascii=False), encoding="utf-8")
        print(f"[WEEK FILE] report: {stem}_report.md (win probability {result['win_prob']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
