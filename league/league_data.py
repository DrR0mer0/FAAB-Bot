#!/usr/bin/env python3
"""Yahoo league data layer: append-only snapshots of the user's own league, and
a short "league activity" report built from them.

  python league/league_data.py snapshot       # fetch + store (the daily job)
  python league/league_data.py check-scoring  # Yahoo's scoring rules and player points vs the tracker's
  python league/league_data.py import-bids    # losing FAAB offers typed into league_data/manual/bids.csv
  python league/league_data.py report         # write league_data/league_activity_week<NN>.md

WHAT IS FETCHED (Yahoo Fantasy Sports API, read-only; league/yahoo_auth.py
holds the OAuth side; the league key is YAHOO_LEAGUE_KEY in league/.env):
  teams         every team's remaining FAAB, waiver priority, move/trade counts
  transactions  the whole season: adds, drops, add/drops, trades, commissioner
                actions. A waiver add carries the WINNING faab_bid.
  rosters       every team's current roster with lineup slots
  scoreboard    every week so far: matchups, points, winner, status
  settings      the league's roster slots and scoring rules
  lineups       each team's FINAL lineup of a completed week with every
                player's fantasy points -- one call per team per week (the
                league-wide roster call returns no points). Each run fetches
                the latest completed week again (stat corrections) and any
                completed week not stored yet, so the first run backfills.
                Starters summed to the scoreboard score exactly in 57 of 60
                team-weeks on 2026-10-13 (three were off by under a point);
                the scoreboard score is the official one.

WHAT YAHOO'S API DOES NOT GIVE (checked 2026-10-10 against ~35 routes):
losing FAAB bids. Only successful transactions are listed; a failed claim is
at most a transaction id missing from the list. Other teams' pending claims
are refused outright. Losing bids are visible on the website's "FAB Offers"
tab only, and Yahoo's terms don't allow tools that extract data from its
pages, so nothing here reads that page: the user types the losing offers into
a CSV by hand (see LOSING BIDS below). Where none are entered, the report's
"active bidder" flag rests on winning claims alone and says so.
$0 BIDS are interest, not price competition: they count toward the active-
bidder flag, but the "paid bids" count, the "typical paid bid" (median) and
the funded-active-bidder flag (active, at least one paid bid in the window,
FAAB left >= FUNDED_MIN_FAAB) leave them out, and an award won at $0 over a
$0 next offer is marked "decided by waiver priority".
Also current-only: FAAB balances (no history), though balance + winning bids
reconciled to the $100 budget for all 12 teams on 2026-10-10.

STORAGE -- lg_* tables in the history DB, all append-only (the tracker's own
UPDATE/DELETE-blocking triggers). One lg_snapshots row per resource per run
(status, counts, SHA-256 and file name of the raw response) and the parsed
rows keyed to it; a resource that fails is stored as an ok=0 snapshot and the
others still land. Every run stores the full current picture, so "the latest
ok snapshot" of a resource is always complete on its own -- except lineups,
which hold whichever weeks that run fetched: read a week with week_lineups(). Raw responses are
gzip files under raw/league/ (gitignored).

LOSING BIDS -- league_data/manual/bids.csv (gitignored), one row per LOSING
offer: award_date, player, winning_team, winning_amount, losing_team,
losing_amount. An award nobody else bid on needs no row; lines starting with #
are comments; a file saved from Excel with a UTF-8 byte-order mark reads the
same; and award_date may be left blank on a row that repeats the row above's
player and winner (see read_bids_csv). The file is the whole truth every time
it is imported:
  * a row not seen before is added; one already stored is left alone;
  * a row whose amounts or winner changed is appended as a CORRECTION and the
    newest version is the one used (hand typing needs a way to fix a typo --
    the earlier row stays on record);
  * a stored offer no longer in the file is appended as WITHDRAWN.
CHECKED THROUGH -- a "# checked_through: YYYY-MM-DD" line in the file says
the user has gone through Yahoo's FAB Offers page up to that award date. It is
stored with the import (lg_bid_imports.checked_through), and the report then
reads an award on or before it with no losing rows as "uncontested" and a
later one as "not typed in yet". Without the line (or with it left blank) the
report keeps saying "uncontested, or not typed in yet". A date that can't be
read, a second such line, or a date after the import day is reported as
rejected and stored as not set -- never guessed.
Nothing is ever updated or deleted: lg_bids holds every version, and
current_bids() reads the newest of each offer. Every import is checked
against the API: an award's winner is linked to the waiver add for the same
player within a day of award_date, and an award whose winning team or amount
differs from Yahoo's -- or that matches no waiver add at all -- is FLAGGED,
loudly and in the report, because one of the two was typed wrong. Rows that
can't be read (unknown team, bad amount, a losing offer above the winning
one ...) are rejected with their line numbers and stored nowhere.

PRIVACY -- the repo is public; the league is not. The league key, team names,
manager nicknames and everything else that identifies a manager exist only in
the local DB, the raw files and the report, all under gitignored paths
(*.db, raw/, league_data/). Nothing this module produces is committed, error
messages are scrubbed of the league key, and manager guids / profile images
are never parsed out of the raw response. Tests use made-up leagues.

FAILURE -- like the roster-% job: nothing raises, a hang is cut off, a failed
resource is loud and recorded, and the command exits non-zero so a scheduler
notices. Nothing else depends on this job.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import sqlite3
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

LEAGUE_DIR = Path(__file__).resolve().parent
REPO_ROOT = LEAGUE_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "tracker"))

import league_availability  # noqa: E402 -- league key, Yahoo session, Yahoo player -> gsis id
import ledger  # noqa: E402 -- the append-only triggers
import ownership  # noqa: E402 -- raw-response files, name index

DEFAULT_DB = REPO_ROOT / "faab_history_core_v0_1.db"
DEFAULT_RAW_DIR = league_availability.DEFAULT_RAW_DIR
DEFAULT_REPORT_DIR = REPO_ROOT / "league_data"
API = "https://fantasysports.yahooapis.com/fantasy/v2/"
RESOURCES = ("teams", "transactions", "rosters", "scoreboard", "settings", "lineups")
BENCH_SLOTS = ("BN", "IR")
# Yahoo's name for a scoring category -> the tracker's rule (tracker/hit_config.json, "scoring")
YAHOO_RULES = {"Passing Yards": "pass_yd", "Passing Touchdowns": "pass_td", "Interceptions": "pass_int", "Sacks": "sack",
               "Rushing Yards": "rush_yd", "Rushing Touchdowns": "rush_td", "Receptions": "reception", "Receiving Yards": "rec_yd",
               "Receiving Touchdowns": "rec_td", "Return Yards": "return_yd", "Return Touchdowns": "return_td",
               "2-Point Conversions": "two_pt_conversion", "Fumbles Lost": "fumble_lost",
               "Offensive Fumble Return TD": "fumble_recovery_td", "Pick Sixes Thrown": "pick_six"}
TX_PAGE = 50              # transactions per request; one unpaged call returned all 78 on 2026-10-10, but don't count on it
MAX_TX_PAGES = 100
REQUEST_TIMEOUT_S = 30
FETCH_BUDGET_S = 180      # for the whole fetch, token refresh included
LEAGUE_TZ = ZoneInfo("America/Los_Angeles")   # Yahoo's week_start / week_end are calendar dates; read them as Pacific days
REPORT_WEEKS = 3          # the current league week and the two before it
ACTIVE_BIDDER_MIN = 2     # bids in the window that make a manager an "active bidder" ($0 bids count)
FUNDED_MIN_FAAB = 10      # FAAB left (in $) for an active bidder with a paid bid to count as "funded"; report --funded-min overrides

DDL = """
CREATE TABLE IF NOT EXISTS lg_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT NOT NULL,
  taken_at TEXT NOT NULL,
  resource TEXT NOT NULL,
  league_key TEXT,
  ok INTEGER NOT NULL,
  error TEXT,
  http_status INTEGER,
  n_pages INTEGER,
  n_rows INTEGER,
  raw_sha256 TEXT,
  raw_file TEXT
);

CREATE TABLE IF NOT EXISTS lg_teams (
  snapshot_id INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT,
  manager_nickname TEXT,
  is_mine INTEGER NOT NULL DEFAULT 0,
  is_commissioner INTEGER NOT NULL DEFAULT 0,
  faab_balance INTEGER,
  waiver_priority INTEGER,
  number_of_moves INTEGER,
  number_of_trades INTEGER,
  PRIMARY KEY (snapshot_id, team_id)
);

CREATE TABLE IF NOT EXISTS lg_transactions (
  snapshot_id INTEGER NOT NULL,
  transaction_id INTEGER NOT NULL,
  type TEXT,
  status TEXT,
  happened_at TEXT,
  faab_bid INTEGER,
  trader_team_id INTEGER,
  tradee_team_id INTEGER,
  n_players INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (snapshot_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS lg_transaction_players (
  snapshot_id INTEGER NOT NULL,
  transaction_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  external_id TEXT NOT NULL,
  player_id TEXT,
  match_method TEXT,
  name TEXT,
  position TEXT,
  team TEXT,
  action TEXT NOT NULL,
  source_type TEXT,
  source_team_id INTEGER,
  destination_type TEXT,
  destination_team_id INTEGER,
  PRIMARY KEY (snapshot_id, transaction_id, seq)
);

CREATE TABLE IF NOT EXISTS lg_rosters (
  snapshot_id INTEGER NOT NULL,
  week INTEGER,
  team_id INTEGER NOT NULL,
  external_id TEXT NOT NULL,
  player_id TEXT,
  match_method TEXT,
  name TEXT,
  position TEXT,
  team TEXT,
  selected_position TEXT,
  PRIMARY KEY (snapshot_id, team_id, external_id)
);

CREATE TABLE IF NOT EXISTS lg_matchups (
  snapshot_id INTEGER NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  opponent_team_id INTEGER,
  week_start TEXT,
  week_end TEXT,
  status TEXT,
  is_playoffs INTEGER,
  is_consolation INTEGER,
  is_tied INTEGER,
  winner_team_id INTEGER,
  points REAL,
  projected_points REAL,
  PRIMARY KEY (snapshot_id, week, team_id)
);

CREATE TABLE IF NOT EXISTS lg_roster_slots (
  snapshot_id INTEGER NOT NULL,
  position TEXT NOT NULL,
  slot_count INTEGER NOT NULL,
  is_starting INTEGER NOT NULL,
  PRIMARY KEY (snapshot_id, position)
);

CREATE TABLE IF NOT EXISTS lg_scoring_rules (
  snapshot_id INTEGER NOT NULL,
  stat_id INTEGER NOT NULL,
  name TEXT,
  display_name TEXT,
  value REAL,
  PRIMARY KEY (snapshot_id, stat_id)
);

CREATE TABLE IF NOT EXISTS lg_lineups (
  snapshot_id INTEGER NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  external_id TEXT NOT NULL,
  player_id TEXT,
  match_method TEXT,
  name TEXT,
  position TEXT,
  team TEXT,
  selected_position TEXT,
  points REAL,
  PRIMARY KEY (snapshot_id, week, team_id, external_id)
);

CREATE TABLE IF NOT EXISTS lg_bid_imports (
  import_id INTEGER PRIMARY KEY AUTOINCREMENT,
  imported_at TEXT NOT NULL,
  file_name TEXT,
  file_sha256 TEXT,
  ok INTEGER NOT NULL,
  error TEXT,
  n_rows INTEGER,
  n_new INTEGER,
  n_corrected INTEGER,
  n_withdrawn INTEGER,
  n_unchanged INTEGER,
  n_rejected INTEGER,
  n_flagged INTEGER,
  raw_file TEXT,
  checked_through TEXT
);

CREATE TABLE IF NOT EXISTS lg_bids (
  bid_id INTEGER PRIMARY KEY AUTOINCREMENT,
  import_id INTEGER NOT NULL,
  change TEXT NOT NULL CHECK (change IN ('new','corrected','withdrawn')),
  line_no INTEGER,
  award_date TEXT NOT NULL,
  player_key TEXT NOT NULL,
  player TEXT,
  losing_team_id INTEGER NOT NULL,
  losing_amount INTEGER,
  winning_team_id INTEGER,
  winning_amount INTEGER,
  transaction_id INTEGER,
  player_id TEXT,
  api_check TEXT
);
"""
TABLES = ("lg_snapshots", "lg_teams", "lg_transactions", "lg_transaction_players", "lg_rosters", "lg_matchups",
          "lg_roster_slots", "lg_scoring_rules", "lg_lineups", "lg_bid_imports", "lg_bids")
DEFAULT_BIDS_CSV = DEFAULT_REPORT_DIR / "manual" / "bids.csv"
BID_COLUMNS = ("award_date", "player", "winning_team", "winning_amount", "losing_team", "losing_amount")
BIDS_TEMPLATE = """\
# Losing FAAB offers, typed by hand from Yahoo's "FAB Offers" tab (League > Transactions).
# One row per LOSING offer. An award nobody else bid on needs no row. Lines starting with # are ignored.
# award_date is the day the waiver was awarded (YYYY-MM-DD, or M/D/YYYY). Teams by team name, as Yahoo shows them.
# award_date may be left blank on a row that repeats the row above's player, winning team and winning amount.
# After editing:  python league/league_data.py import-bids
#
# The checked_through line below is the last award date you have gone through on the FAB Offers page. Awards up to
# that date with no rows here count as uncontested; later ones as not typed in yet. Move it forward only after checking.
# Left blank, the report can't tell the two apart.
# checked_through:
award_date,player,winning_team,winning_amount,losing_team,losing_amount
# 2026-10-07,Example Player,Winning Team Name,16,Losing Team Name,9
"""


def _banner(msg):
    bar = "!" * 78
    print(f"\n{bar}\n[LEAGUE DATA WARNING] {msg}\n{bar}\n", file=sys.stderr)


def utcnow():
    return datetime.now(timezone.utc)


def init_schema(con):
    con.executescript(DDL)
    # the file's "# checked_through:" date (NULL before 2026-10-14, or when the line is missing)
    if "checked_through" not in ledger.table_columns(con, "lg_bid_imports"):
        con.execute("ALTER TABLE lg_bid_imports ADD COLUMN checked_through TEXT")
    for t in TABLES:
        ledger.create_append_only_triggers(con, t)
    con.commit()


def connect(db_path):
    con = sqlite3.connect(str(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    init_schema(con)
    return con


# --------------------------------------------------------------------- fetch

def flat(parts):
    """Yahoo writes a record as a list of one-key dicts (and nested lists of
    them, and stray empty lists); -> one dict."""
    out = {}
    for p in parts if isinstance(parts, list) else [parts]:
        if isinstance(p, dict):
            out.update(p)
        elif isinstance(p, list):
            out.update(flat(p))
    return out


def numbered(collection):
    """Yahoo writes a list as {"0": .., "1": .., "count": n} (count sometimes a
    string), and an EMPTY list as [] -> the items, in order."""
    if not collection:
        return []
    return [collection[str(i)] for i in range(int(collection["count"]))]


def team_id_of(team_key):
    """'470.l.123.t.7' -> 7; None stays None. Only the id is stored: the key holds the league."""
    return int(team_key.rsplit(".t.", 1)[1]) if team_key else None


def _fetch(league_key, session, open_session, deadline, skip_lineup_weeks=()):
    out = {r: {"ok": False, "error": None, "http_status": None, "n_pages": 0, "raw_bytes": None, "payloads": []} for r in RESOURCES}
    pages = defaultdict(list)
    meta = {}

    def get(resource, path, base=None):
        """`path` under the league by default; `base` names another resource of the same league (a team)."""
        if time.monotonic() > deadline:
            raise TimeoutError("ran out of time")
        resp = session.get(f"{API}{base or 'league/' + league_key}/{path}?format=json", timeout=REQUEST_TIMEOUT_S)
        shown = path if base is None else f"{base}/{path}".replace(league_key, "<league>")
        out[resource]["http_status"] = resp.status_code
        pages[resource].append({"path": shown, "http_status": resp.status_code, "body": resp.text})
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} on {shown}")
        payload = resp.json()
        if base is None:
            meta.update({k: v for k, v in payload["fantasy_content"]["league"][0].items()
                         if k in ("current_week", "start_week", "end_week")})
        out[resource]["payloads"].append(payload)
        return payload

    def lineups():
        """The final lineup of the latest completed week, and of any completed week not stored yet: one call per team."""
        if not (out["teams"]["ok"] and out["scoreboard"]["ok"]):
            raise RuntimeError("needs the teams and the scoreboard, and one of them could not be read")
        team_ids = [r["team_id"] for r in parse_teams(out["teams"]["payloads"])]
        status = defaultdict(set)
        for r in parse_scoreboard(out["scoreboard"]["payloads"]):
            status[r["week"]].add(r["status"])
        done = sorted(w for w, seen in status.items() if seen == {"postevent"})
        for week in [w for w in done if w == done[-1] or w not in skip_lineup_weeks]:
            for team_id in team_ids:
                get("lineups", f"roster;week={week}/players/stats;type=week;week={week}", base=f"team/{league_key}.t.{team_id}")

    def transactions():
        for page in range(MAX_TX_PAGES):
            got = get("transactions", f"transactions;start={page * TX_PAGE};count={TX_PAGE}")
            if len(numbered(got["fantasy_content"]["league"][1]["transactions"])) < TX_PAGE:
                return
        raise RuntimeError(f"no end of the transaction list after {MAX_TX_PAGES} pages")

    def scoreboard():
        if "current_week" not in meta:
            raise RuntimeError("the league's current week is unknown (no other resource could be read)")
        weeks = range(int(meta.get("start_week", 1)), int(meta["current_week"]) + 1)
        get("scoreboard", "scoreboard;week=" + ",".join(str(w) for w in weeks))

    try:
        session = session or open_session()
    except (Exception, SystemExit) as e:  # noqa: BLE001 -- yahoo_auth exits on missing credentials; nothing here may raise
        for r in RESOURCES:
            out[r]["error"] = f"{type(e).__name__}: {e}".replace(league_key, "<league>")
        return out
    for resource, work in (("teams", lambda: get("teams", "teams")), ("transactions", transactions),
                           ("rosters", lambda: get("rosters", "teams/roster")), ("scoreboard", scoreboard),
                           ("settings", lambda: get("settings", "settings")), ("lineups", lineups)):
        try:
            work()
            out[resource]["ok"] = True
        except Exception as e:  # noqa: BLE001 -- one resource failing must not cost the others
            out[resource]["error"] = f"{type(e).__name__}: {e}".replace(league_key, "<league>")   # the key stays out of messages
        out[resource]["n_pages"] = len(pages[resource])
        if pages[resource]:
            out[resource]["raw_bytes"] = json.dumps({"league_key": league_key, "resource": resource, "pages": pages[resource]},
                                                    sort_keys=True).encode("utf-8")
    return out


def fetch(league_key, session=None, open_session=league_availability.open_session, budget_s=FETCH_BUDGET_S, skip_lineup_weeks=()):
    """-> {resource: dict(ok, error, http_status, n_pages, raw_bytes, payloads)}
    for every resource in RESOURCES. skip_lineup_weeks: completed weeks whose
    lineups are already stored (the latest completed week is fetched anyway).
    Never raises, and never takes much longer than budget_s: the work runs in
    a thread that is left behind if Yahoo (or the token refresh, which has no
    timeout of its own) hangs."""
    box = {}
    worker = threading.Thread(target=lambda: box.update(out=_fetch(league_key, session, open_session, time.monotonic() + budget_s,
                                                                    skip_lineup_weeks)), daemon=True)
    worker.start()
    worker.join(budget_s + REQUEST_TIMEOUT_S)
    return box.get("out") or {r: {"ok": False, "error": f"no answer from Yahoo within {budget_s + REQUEST_TIMEOUT_S}s",
                                  "http_status": None, "n_pages": 0, "raw_bytes": None, "payloads": []} for r in RESOURCES}


# --------------------------------------------------------------------- parse
# Strict on purpose: an unrecognised shape raises and the caller records that resource as failed.

def _league_body(payload, key):
    return payload["fantasy_content"]["league"][1][key]


def _player(parts):
    info = flat(parts[0])
    return {"external_id": str(info["player_id"]), "name": info["name"]["full"],
            "position": info.get("primary_position") or (info.get("display_position") or "").split(",")[0] or None,
            "team": (info.get("editorial_team_abbr") or "").upper() or None}


def parse_teams(payloads):
    rows = []
    for t in numbered(_league_body(payloads[0], "teams")):
        info = flat(t["team"][0])
        managers = [m["manager"] for m in info.get("managers") or []]
        rows.append({
            "team_id": int(info["team_id"]), "team_name": info.get("name"),
            "manager_nickname": " / ".join(m.get("nickname") or "?" for m in managers) or None,
            "is_mine": 1 if info.get("is_owned_by_current_login") else 0,
            "is_commissioner": 1 if any(str(m.get("is_commissioner", "0")) == "1" for m in managers) else 0,
            "faab_balance": int(info["faab_balance"]) if info.get("faab_balance") not in (None, "") else None,
            "waiver_priority": int(info["waiver_priority"]) if info.get("waiver_priority") not in (None, "") else None,
            "number_of_moves": int(info.get("number_of_moves") or 0), "number_of_trades": int(info.get("number_of_trades") or 0)})
    return rows


def parse_transactions(payloads):
    """-> (transactions, transaction players). Pages can overlap when a
    transaction lands mid-fetch; a transaction is kept once."""
    txs, players, seen = [], [], set()
    for payload in payloads:
        for t in numbered(_league_body(payload, "transactions")):
            parts = t["transaction"]
            head = parts[0]
            tid = int(head["transaction_id"])
            if tid in seen:
                continue
            seen.add(tid)
            listed = numbered(next((p["players"] for p in parts[1:] if isinstance(p, dict) and "players" in p), None))
            txs.append({
                "transaction_id": tid, "type": head.get("type"), "status": head.get("status"),
                "happened_at": datetime.fromtimestamp(int(head["timestamp"]), timezone.utc).isoformat(),
                "faab_bid": int(head["faab_bid"]) if head.get("faab_bid") not in (None, "") else None,
                "trader_team_id": team_id_of(head.get("trader_team_key")), "tradee_team_id": team_id_of(head.get("tradee_team_key")),
                "n_players": len(listed)})
            for seq, p in enumerate(listed):
                data = flat(next(x["transaction_data"] for x in p["player"][1:] if isinstance(x, dict) and "transaction_data" in x))
                players.append(dict(_player(p["player"]), transaction_id=tid, seq=seq, action=data["type"],
                                    source_type=data.get("source_type"), source_team_id=team_id_of(data.get("source_team_key")),
                                    destination_type=data.get("destination_type"),
                                    destination_team_id=team_id_of(data.get("destination_team_key"))))
    return txs, players


def parse_rosters(payloads):
    rows = []
    for t in numbered(_league_body(payloads[0], "teams")):
        team_id = int(flat(t["team"][0])["team_id"])
        roster = next(p["roster"] for p in t["team"][1:] if isinstance(p, dict) and "roster" in p)
        for p in numbered(roster["0"]["players"]):
            slot = flat(next(x["selected_position"] for x in p["player"][1:] if isinstance(x, dict) and "selected_position" in x))
            rows.append(dict(_player(p["player"]), team_id=team_id, week=int(roster["week"]) if roster.get("week") else None,
                             selected_position=slot.get("position")))
    return rows


def parse_scoreboard(payloads):
    """One row per team per matchup."""
    rows = []
    for m in numbered(_league_body(payloads[0], "scoreboard")["0"]["matchups"]):
        m = m["matchup"]
        sides = []
        for t in numbered(m["0"]["teams"]):
            stats = flat(t["team"][1:])
            sides.append((int(flat(t["team"][0])["team_id"]), float(stats["team_points"]["total"]),
                          float(stats["team_projected_points"]["total"]) if stats.get("team_projected_points") else None))
        for i, (team_id, points, projected) in enumerate(sides):
            rows.append({
                "week": int(m["week"]), "team_id": team_id,
                "opponent_team_id": sides[1 - i][0] if len(sides) == 2 else None,
                "week_start": m.get("week_start"), "week_end": m.get("week_end"), "status": m.get("status"),
                "is_playoffs": int(m.get("is_playoffs") or 0), "is_consolation": int(m.get("is_consolation") or 0),
                "is_tied": int(m.get("is_tied") or 0), "winner_team_id": team_id_of(m.get("winner_team_key")),
                "points": points, "projected_points": projected})
    return rows


def parse_settings(payloads):
    """-> (roster slots, scoring rules): how many of each lineup slot a team
    has, and every stat category that is worth points."""
    st = _league_body(payloads[0], "settings")[0]
    slots = [{"position": p["roster_position"]["position"], "slot_count": int(p["roster_position"]["count"]),
              "is_starting": int(p["roster_position"].get("is_starting_position") or 0)} for p in st["roster_positions"]]
    names = {int(c["stat"]["stat_id"]): c["stat"] for c in st["stat_categories"]["stats"]}
    rules = [{"stat_id": int(m["stat"]["stat_id"]), "name": names.get(int(m["stat"]["stat_id"]), {}).get("name"),
              "display_name": names.get(int(m["stat"]["stat_id"]), {}).get("display_name"), "value": float(m["stat"]["value"])}
             for m in st["stat_modifiers"]["stats"]]
    return slots, rules


def parse_lineups(payloads):
    """One row per rostered player per team per week: lineup slot and the
    fantasy points Yahoo gave him that week. One payload per team-week."""
    rows = []
    for payload in payloads:
        team = payload["fantasy_content"]["team"]
        team_id = int(flat(team[0])["team_id"])
        roster = next(p["roster"] for p in team[1:] if isinstance(p, dict) and "roster" in p)
        for p in numbered(roster["0"]["players"]):
            extra = {}
            for x in p["player"][1:]:
                if isinstance(x, dict):
                    extra.update(x)
            rows.append(dict(_player(p["player"]), week=int(roster["week"]), team_id=team_id,
                             selected_position=flat(extra["selected_position"]).get("position"),
                             points=float(extra["player_points"]["total"])))
    return rows


# ------------------------------------------------------------------ snapshot

def lineup_weeks_stored(con):
    """Weeks that have a final lineup on record."""
    return {r[0] for r in con.execute("SELECT DISTINCT l.week FROM lg_lineups l JOIN lg_snapshots s ON s.snapshot_id = l.snapshot_id "
                                      "WHERE s.ok=1")}


def week_lineups(con, week):
    """{team_id: [row]} -- every team's final lineup of `week` with player
    points, from the newest snapshot that holds that week; {} if none does."""
    sid = con.execute("SELECT MAX(l.snapshot_id) FROM lg_lineups l JOIN lg_snapshots s ON s.snapshot_id = l.snapshot_id "
                      "WHERE s.ok=1 AND l.week=?", (week,)).fetchone()[0]
    out = defaultdict(list)
    for r in con.execute("SELECT * FROM lg_lineups WHERE snapshot_id=? AND week=? ORDER BY team_id, external_id", (sid, week)):
        out[r["team_id"]].append(dict(r))
    return dict(out)


def roster_slots(con):
    """[(position, count)] of the STARTING slots, from the newest settings snapshot; [] before one exists."""
    snap = latest_ok(con, "settings")
    return [] if snap is None else [(r[0], r[1]) for r in con.execute(
        "SELECT position, slot_count FROM lg_roster_slots WHERE snapshot_id=? AND is_starting=1 ORDER BY rowid", (snap[0],))]


def default_team(con):
    """The team this installation is "for": the one Yahoo says the logged-in
    account owns. A default only -- everything computed per team takes a team
    id, and any command that needs one accepts another."""
    snap = latest_ok(con, "teams")
    row = con.execute("SELECT team_id FROM lg_teams WHERE snapshot_id=? AND is_mine=1 ORDER BY team_id LIMIT 1",
                      (snap[0],)).fetchone() if snap else None
    return row[0] if row else None


def take_snapshot(con, league_key, raw_dir, roster_csv, now=None, fetcher=None):
    """Fetch every resource and append it: one lg_snapshots row each plus
    the parsed rows. raw_dir has no default on purpose (a test can't write
    into the real raw/league/). -> {resource: dict(ok, error, n_rows, snapshot_id)}.
    Never raises."""
    taken_at = (now or utcnow()).isoformat()
    summary = {}
    if not league_key or not league_availability.LEAGUE_KEY_RE.match(league_key):
        why = (f"no league is configured ({league_availability.LEAGUE_KEY_VAR} in league/.env)" if not league_key else
               f"{league_availability.LEAGUE_KEY_VAR} is not a Yahoo league key (expected <game>.l.<league id>, with a lower-case L)")
        got = {r: {"ok": False, "error": why, "http_status": None, "n_pages": 0, "raw_bytes": None, "payloads": []} for r in RESOURCES}
        league_key = None
    else:
        got = (fetcher or fetch)(league_key, skip_lineup_weeks=lineup_weeks_stored(con))   # looked up at call time
    try:
        yahoo_ids, roster_names = league_availability.load_roster_index(roster_csv)
        ref_names = ownership.build_name_index(con)
    except Exception as e:  # noqa: BLE001 -- unmatched players are still stored, under their Yahoo ids
        yahoo_ids, roster_names, ref_names = {}, {}, {}
        _banner(f"players could not be matched to gsis ids this run ({type(e).__name__}: {e})")

    def mapped(rows):
        return league_availability.map_players(rows, yahoo_ids, roster_names, ref_names)

    def transactions(payloads):
        txs, players = parse_transactions(payloads)
        return {"lg_transactions": txs, "lg_transaction_players": mapped(players)}

    def store(resource, res, ok, error, tables, raw_file, n_rows):
        with con:
            sid = con.execute(
                "INSERT INTO lg_snapshots (batch_id, taken_at, resource, league_key, ok, error, http_status, n_pages, n_rows, "
                "raw_sha256, raw_file) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (taken_at, taken_at, resource, league_key, 1 if ok else 0, error, res["http_status"], res["n_pages"], n_rows,
                 hashlib.sha256(res["raw_bytes"]).hexdigest() if res["raw_bytes"] else None, raw_file)).lastrowid
            for table, rows in tables.items():
                ledger.insert_rows(con, table, [dict(r, snapshot_id=sid) for r in rows])
        return sid

    def settings(payloads):
        slots, rules = parse_settings(payloads)
        return {"lg_roster_slots": slots, "lg_scoring_rules": rules}

    parsers = {"teams": lambda p: {"lg_teams": parse_teams(p)}, "transactions": transactions,
               "rosters": lambda p: {"lg_rosters": mapped(parse_rosters(p))},
               "scoreboard": lambda p: {"lg_matchups": parse_scoreboard(p)}, "settings": settings,
               "lineups": lambda p: {"lg_lineups": mapped(parse_lineups(p))}}
    for resource in RESOURCES:
        res, tables = got[resource], {}
        ok, error = bool(res["ok"]), res["error"]
        if ok:
            try:
                tables = parsers[resource](res["payloads"])
            except Exception as e:  # noqa: BLE001 -- Yahoo changed shape: a failed snapshot, not a crash
                ok, error, tables = False, f"unexpected response shape ({type(e).__name__}: {e})", {}
        raw, raw_file = res["raw_bytes"], None
        if raw:
            try:
                raw_file = ownership.write_raw(raw_dir, taken_at, f"yahoo_{resource}", raw)
            except Exception as e:  # noqa: BLE001 -- the SHA-256 below still identifies the response
                _banner(f"could not keep a copy of the Yahoo {resource} response under {raw_dir} ({type(e).__name__}: {e})")
        n_rows = len(next(iter(tables.values()))) if tables else 0
        try:
            sid = store(resource, res, ok, error, tables, raw_file, n_rows)
        except Exception as e:  # noqa: BLE001 -- e.g. a duplicate key from a shape we misread: rolled back, recorded as failed
            ok, error = False, f"could not be stored ({type(e).__name__}: {e})"
            try:
                sid = store(resource, res, False, error, {}, raw_file, 0)
            except Exception:  # noqa: BLE001 -- the DB itself is unusable; the banner below is all that's left
                sid = None
        summary[resource] = {"ok": ok, "error": error, "n_rows": n_rows if ok else 0, "snapshot_id": sid}
        if not ok:
            _banner(f"Yahoo {resource} snapshot FAILED ({error}). Recorded as failed; the other resources are unaffected.")
    return summary


# ------------------------------------------------- losing bids, typed by hand

def read_bids_csv(text):
    """The CSV's text -> (rows, problems). rows: one dict per data line with
    line (1-based), award_date (a date), player, winning_team, winning_amount,
    losing_team, losing_amount -- the two teams still as typed. problems:
    "line N: why" for every line that couldn't be read; such a line is in
    neither list's rows. A missing or wrong header is a problem on its own
    and gives no rows at all.

    Two things a spreadsheet does to the file are allowed for: a UTF-8
    byte-order mark in front of the first line (Excel's "CSV UTF-8"), and a
    BLANK award_date on a row that repeats the row above's player, winning
    team and winning amount -- the second and later offers on one award --
    which takes that row's date. A blank date on any other row is a problem:
    a date is never guessed across awards."""
    rows, problems, header, above = [], [], None, None
    for n, cells in enumerate(csv.reader(io.StringIO(text.lstrip("\ufeff"))), start=1):
        cells = [c.strip() for c in cells]
        if not any(cells) or cells[0].startswith("#"):
            continue
        if header is None:
            header = [c.lower() for c in cells]
            if tuple(header[:len(BID_COLUMNS)]) != BID_COLUMNS:
                return [], [f"line {n}: the header must be {','.join(BID_COLUMNS)}"]
            continue
        r = dict(zip(BID_COLUMNS, cells + [""] * len(BID_COLUMNS)))
        # the row above, as (date, award): what a blank award_date may be copied from. Reset by any row without a usable date.
        was, above = above, None
        try:
            amounts = {k: int(r[k].lstrip("$")) for k in ("winning_amount", "losing_amount")}
        except ValueError:
            problems.append(f"line {n}: both amounts must be whole dollars")
            continue
        award = (ownership.norm_name(r["player"]), _name_key(r["winning_team"]), amounts["winning_amount"])
        day = r["award_date"]
        if not day:
            if was is None or was[1] != award:
                problems.append(f"line {n}: award_date is blank, which is only allowed on a row that repeats the row above's player, "
                                f"winning team and winning amount")
                continue
            when = was[0]
        else:
            try:
                when = datetime.strptime(day, "%m/%d/%Y").date() if "/" in day else date.fromisoformat(day)
            except ValueError:
                problems.append(f"line {n}: award_date must be YYYY-MM-DD (or M/D/YYYY)")
                continue
        above = (when, award)
        why = ("player is empty" if not r["player"] else "a team is empty" if not (r["winning_team"] and r["losing_team"]) else
               "an amount is negative" if min(amounts.values()) < 0 else
               f"the losing offer (${amounts['losing_amount']}) is above the winning one (${amounts['winning_amount']})"
               if amounts["losing_amount"] > amounts["winning_amount"] else None)
        if why:
            problems.append(f"line {n}: {why}")
            continue
        rows.append(dict(r, line=n, award_date=when, **amounts))
    if header is None:
        problems.append(f"no header line ({','.join(BID_COLUMNS)})")
    return rows, problems


CHECKED_THROUGH = re.compile(r"#\s*checked_through\s*:(.*)$", re.IGNORECASE)


def read_checked_through(text, today):
    """The file's "# checked_through: <date>" line -> (date or None, problems).
    A missing or blank line is None with no problem. A date that can't be
    read, more than one such line, or a date after `today` is None plus a
    "line N: why" problem: the report then keeps its unsure wording."""
    found, problems = [], []
    for n, line in enumerate(text.lstrip("\ufeff").splitlines(), start=1):
        cells = next(csv.reader([line]), [""])           # a spreadsheet save adds ",,,,," (or quotes): read the first cell only
        m = CHECKED_THROUGH.match((cells[0] if cells else "").strip())
        if m:
            found.append((n, m.group(1).strip()))
    found = [(n, v) for n, v in found if v]
    if not found:
        return None, []
    if len(found) > 1:
        return None, [f"line {found[1][0]}: a second checked_through line (line {found[0][0]} has one) -- neither is used"]
    n, value = found[0]
    try:
        day = datetime.strptime(value, "%m/%d/%Y").date() if "/" in value else date.fromisoformat(value)
    except ValueError:
        return None, [f"line {n}: checked_through must be YYYY-MM-DD (or M/D/YYYY) -- not used"]
    if day > today:
        return None, [f"line {n}: checked_through {day} is after today ({today}) -- not used"]
    return day, []


def _name_key(name):
    return " ".join(str(name or "").casefold().split())


def team_index(con):
    """{typed name: team_id} for every team name the snapshots have ever seen
    (a team can be renamed mid-season) and every manager nickname, compared
    case-insensitively. A name that has belonged to two teams is left out
    rather than guessed."""
    seen = defaultdict(set)
    for team_id, name, nick in con.execute("SELECT DISTINCT team_id, team_name, manager_nickname FROM lg_teams"):
        for label in (name, nick):
            if label:
                seen[_name_key(label)].add(team_id)
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def waiver_awards(con, tx_snapshot):
    """Every waiver add in a transactions snapshot: [dict(transaction_id, when,
    day (Pacific), team_id, amount, player, player_key, player_id)]."""
    out = []
    if tx_snapshot is None:
        return out
    for r in con.execute(
            "SELECT t.transaction_id, t.happened_at, t.faab_bid, p.name, p.player_id, p.destination_team_id FROM lg_transactions t "
            "JOIN lg_transaction_players p ON p.snapshot_id = t.snapshot_id AND p.transaction_id = t.transaction_id "
            "WHERE t.snapshot_id=? AND p.action='add' AND p.source_type='waivers' ORDER BY t.transaction_id", (tx_snapshot,)):
        when = datetime.fromisoformat(r["happened_at"])
        out.append({"transaction_id": r["transaction_id"], "when": when, "day": when.astimezone(LEAGUE_TZ).date(),
                    "team_id": r["destination_team_id"], "amount": r["faab_bid"] or 0, "player": r["name"],
                    "player_key": ownership.norm_name(r["name"]), "player_id": r["player_id"]})
    return out


def link_award(awards, player_key, award_date):
    """The API's waiver add for a hand-entered award: same player (name
    normalized as everywhere else), awarded on award_date or a day either side
    (a waiver run just after midnight is easy to write down as the day
    before). The same-day one wins; otherwise it must be the only candidate."""
    near = [a for a in awards if a["player_key"] == player_key and abs((a["day"] - award_date).days) <= 1]
    same_day = [a for a in near if a["day"] == award_date]
    return same_day[0] if len(same_day) == 1 else near[0] if len(near) == 1 else None


def api_check(award, winning_team_id, winning_amount):
    """How a hand-entered winner compares with Yahoo's own record of the award."""
    if award is None:
        return "no_api_transaction"
    team, amount = award["team_id"] != winning_team_id, award["amount"] != winning_amount
    return ("winner_team_and_amount_differ" if team and amount else "winner_team_differs" if team else
            "winner_amount_differs" if amount else "ok")


def current_bids(con):
    """The newest version of every hand-entered losing offer, withdrawn ones left out."""
    newest = {}
    for r in con.execute("SELECT * FROM lg_bids ORDER BY bid_id"):
        newest[(r["award_date"], r["player_key"], r["losing_team_id"])] = dict(r)
    return [r for r in newest.values() if r["change"] != "withdrawn"]


def import_bids(con, path, raw_dir, now=None):
    """Bring lg_bids in line with the CSV (see LOSING BIDS in the module
    docstring). -> dict(ok, error, unchanged_file, n_rows, n_new, n_corrected,
    n_withdrawn, n_unchanged, rejected [str], flagged [str], corrections [str],
    withdrawn [str], checked_through (a date or None)). The lists hold player names and amounts but no team or
    manager names. Never raises on a bad file."""
    out = {"ok": False, "error": None, "unchanged_file": False, "n_rows": 0, "n_new": 0, "n_corrected": 0, "n_withdrawn": 0,
           "n_unchanged": 0, "rejected": [], "flagged": [], "corrections": [], "withdrawn": [], "checked_through": None}
    imported_at = (now or utcnow()).isoformat()
    try:
        raw = Path(path).read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as e:
        out["error"] = f"the bids file could not be read ({type(e).__name__})"
        return out
    sha = hashlib.sha256(raw).hexdigest()
    last = con.execute("SELECT file_sha256 FROM lg_bid_imports WHERE ok=1 ORDER BY import_id DESC LIMIT 1").fetchone()
    if last and last[0] == sha:
        out.update(ok=True, unchanged_file=True)
        return out
    typed, out["rejected"] = read_bids_csv(text)
    out["checked_through"], checked_problems = read_checked_through(text, (now or utcnow()).astimezone(LEAGUE_TZ).date())
    teams = team_index(con)
    tx = latest_ok(con, "transactions")
    awards = waiver_awards(con, tx[0] if tx else None)
    stored = {(r["award_date"], r["player_key"], r["losing_team_id"]): r for r in current_bids(con)}
    if not teams and typed:
        out["error"] = "no league snapshot yet, so team names can't be recognised -- run `snapshot` first"
    elif not typed and not out["rejected"] and stored:
        out["error"] = f"the file has no data rows but {len(stored)} offer(s) are stored -- nothing was changed"
    if out["error"]:
        return _record_import(con, out, imported_at, path, sha, None, [])

    rows, winners = {}, defaultdict(set)
    for r in typed:
        ids = {side: teams.get(_name_key(r[side])) for side in ("winning_team", "losing_team")}
        unknown = [side for side, team_id in ids.items() if team_id is None]
        key = (r["award_date"].isoformat(), ownership.norm_name(r["player"]), ids["losing_team"])
        why = (f"{' and '.join(unknown).replace('_', ' ')} not recognised (use the team name as Yahoo shows it)" if unknown else
               "the losing team is the winning team" if ids["winning_team"] == ids["losing_team"] else
               f"the same offer is already on line {rows[key]['line']}" if key in rows else None)
        if why:
            out["rejected"].append(f"line {r['line']}: {why}")
            continue
        rows[key] = dict(r, winning_team_id=ids["winning_team"], losing_team_id=ids["losing_team"])
        winners[key[:2]].add((ids["winning_team"], r["winning_amount"]))
    for key in [k for k in rows if len(winners[k[:2]]) > 1]:    # one award, two different winners typed: neither can be trusted
        out["rejected"].append(f"line {rows.pop(key)['line']}: this award's winning team/amount differs between its rows")
    out["rejected"].sort(key=lambda m: int(m.split(":")[0].split()[-1]) if m.startswith("line ") else 0)

    new_rows = []
    for key, r in rows.items():
        award = link_award(awards, key[1], r["award_date"])
        check = api_check(award, r["winning_team_id"], r["winning_amount"])
        label = f"{r['player']} ({key[0]})"
        if check != "ok":
            out["flagged"].append(
                f"line {r['line']}: {label} -- " + ("no waiver add for this player within a day of that date in Yahoo's transactions"
                                                    if award is None else
                                                    f"{check.replace('_', ' ')}: typed ${r['winning_amount']}, Yahoo has ${award['amount']}"
                                                    + ("" if "team" not in check else " and a different winning team")))
        was = stored.get(key)
        same = was and (was["losing_amount"], was["winning_team_id"], was["winning_amount"]) == (
            r["losing_amount"], r["winning_team_id"], r["winning_amount"])
        if same:
            out["n_unchanged"] += 1
            continue
        if was:
            out["corrections"].append(f"line {r['line']}: {label} -- losing offer ${was['losing_amount']} -> ${r['losing_amount']}, "
                                      f"winning ${was['winning_amount']} -> ${r['winning_amount']}")
        new_rows.append({"change": "corrected" if was else "new", "line_no": r["line"], "award_date": key[0], "player_key": key[1],
                         "player": r["player"], "losing_team_id": r["losing_team_id"], "losing_amount": r["losing_amount"],
                         "winning_team_id": r["winning_team_id"], "winning_amount": r["winning_amount"],
                         "transaction_id": award["transaction_id"] if award else None,
                         "player_id": award["player_id"] if award else None, "api_check": check})
    if out["rejected"]:
        gone = []      # a line that can't be read may be an offer already stored: don't call anything withdrawn
    else:
        gone = [was for key, was in stored.items() if key not in rows]
    for was in gone:
        out["withdrawn"].append(f"{was['player']} ({was['award_date']}) -- a ${was['losing_amount']} losing offer is no longer in the file")
        new_rows.append({k: was[k] for k in ("award_date", "player_key", "player", "losing_team_id", "losing_amount", "winning_team_id",
                                             "winning_amount", "transaction_id", "player_id", "api_check")}
                        | {"change": "withdrawn", "line_no": None})
    out["rejected"] += checked_problems     # after the withdrawal check: a bad date line hides no offer
    out.update(ok=True, n_rows=len(rows), n_new=sum(1 for r in new_rows if r["change"] == "new"),
               n_corrected=len(out["corrections"]), n_withdrawn=len(gone))
    raw_file = None
    try:
        raw_file = ownership.write_raw(raw_dir, imported_at, "manual_bids", raw, suffix=".csv.gz")
    except Exception as e:  # noqa: BLE001 -- the SHA-256 still identifies the file that was imported
        _banner(f"could not keep a copy of the bids file under {raw_dir} ({type(e).__name__}: {e})")
    return _record_import(con, out, imported_at, path, sha, raw_file, new_rows)


def _record_import(con, out, imported_at, path, sha, raw_file, new_rows):
    with con:
        import_id = con.execute(
            "INSERT INTO lg_bid_imports (imported_at, file_name, file_sha256, ok, error, n_rows, n_new, n_corrected, n_withdrawn, "
            "n_unchanged, n_rejected, n_flagged, raw_file, checked_through) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (imported_at, Path(path).name, sha, 1 if out["ok"] else 0, out["error"], out["n_rows"], out["n_new"], out["n_corrected"],
             out["n_withdrawn"], out["n_unchanged"], len(out["rejected"]), len(out["flagged"]), raw_file,
             out["checked_through"].isoformat() if out["checked_through"] and out["ok"] else None)).lastrowid
        ledger.insert_rows(con, "lg_bids", [dict(r, import_id=import_id) for r in new_rows])
    return out


# -------------------------------------------------------------------- report

def latest_ok(con, resource):
    """(snapshot_id, taken_at) of the newest successful snapshot of a resource, or None."""
    r = con.execute("SELECT snapshot_id, taken_at FROM lg_snapshots WHERE resource=? AND ok=1 ORDER BY taken_at DESC, snapshot_id DESC "
                    "LIMIT 1", (resource,)).fetchone()
    return (r[0], r[1]) if r else None


def league_weeks(con, snapshot_id):
    """{week: (first day, last day)} from Yahoo's own week_start / week_end."""
    return {r[0]: (date.fromisoformat(r[1]), date.fromisoformat(r[2])) for r in con.execute(
        "SELECT DISTINCT week, week_start, week_end FROM lg_matchups WHERE snapshot_id=? AND week_start IS NOT NULL "
        "AND week_end IS NOT NULL", (snapshot_id,))}


def report_window(weeks, today, n_weeks=REPORT_WEEKS):
    """(first week, current week, window start, window end) -- the current
    league week and the n_weeks-1 before it, by Yahoo's week boundaries, as
    aware datetimes in the league's timezone. The current week is the one
    whose dates hold `today`; before the first week it is the first, after
    the last it is the last."""
    current = next((w for w, (a, b) in sorted(weeks.items()) if a <= today <= b), None)
    if current is None:
        current = min(weeks) if today < min(a for a, _ in weeks.values()) else max(weeks)
    first = max(min(weeks), current - n_weeks + 1)
    start = datetime.combine(weeks[first][0], datetime.min.time(), LEAGUE_TZ)
    end = datetime.combine(weeks[current][1] + timedelta(days=1), datetime.min.time(), LEAGUE_TZ)
    return first, current, start, end


def team_activity(con, tx_snapshot, start, end):
    """{team_id: [event]} for the transactions inside [start, end): one event
    per transaction per team, with what the team added and dropped."""
    events = defaultdict(dict)
    for r in con.execute(
            "SELECT t.transaction_id, t.type, t.happened_at, t.faab_bid, p.name, p.position, p.team, p.action, p.source_type, "
            "p.source_team_id, p.destination_team_id FROM lg_transactions t JOIN lg_transaction_players p "
            "ON p.snapshot_id = t.snapshot_id AND p.transaction_id = t.transaction_id WHERE t.snapshot_id=? "
            "ORDER BY t.happened_at, t.transaction_id, p.seq", (tx_snapshot,)):
        when = datetime.fromisoformat(r["happened_at"])
        if not start <= when < end:
            continue
        label = f"{r['name']} ({r['position']}, {r['team']})"
        sides = ([(r["destination_team_id"], "added")] if r["action"] == "add" else
                 [(r["source_team_id"], "dropped")] if r["action"] == "drop" else
                 [(r["destination_team_id"], "added"), (r["source_team_id"], "dropped")])
        for team_id, side in sides:
            if team_id is None:
                continue
            ev = events[team_id].setdefault(r["transaction_id"], {
                "transaction_id": r["transaction_id"], "when": when, "type": r["type"], "added": [], "dropped": [],
                "waiver": False, "faab_bid": None})
            ev[side].append(label)
            if r["action"] == "add" and r["source_type"] == "waivers" and side == "added":
                ev["waiver"], ev["faab_bid"] = True, r["faab_bid"] or 0
    return {team_id: sorted(evs.values(), key=lambda e: (e["when"], e["transaction_id"])) for team_id, evs in events.items()}


def unlisted_ids(con, tx_snapshot, start, end):
    """How many transaction ids inside the window Yahoo's list skips. Ids are
    handed out in order, so a missing id falls between its listed neighbours;
    it is counted when both are inside the window. On 2026-10-10, 8 of the 10
    such ids had been created in the same minute as a batch of winning waiver
    claims -- most likely failed claims, which the API otherwise hides."""
    listed = [(r[0], datetime.fromisoformat(r[1])) for r in con.execute(
        "SELECT transaction_id, happened_at FROM lg_transactions WHERE snapshot_id=? ORDER BY transaction_id", (tx_snapshot,))]
    return sum(b_id - a_id - 1 for (a_id, a_when), (b_id, b_when) in zip(listed, listed[1:])
               if start <= a_when < end and start <= b_when < end)


def losing_bids(con, tx_snapshot, start, end):
    """The current hand-entered losing offers, each linked afresh to the API's
    award (so a bid typed before its transaction was snapshotted still links),
    and those that fall inside [start, end). -> (all offers, offers in the
    window); every offer gains `award` (or None), `check` and `when`."""
    awards = waiver_awards(con, tx_snapshot)
    offers = []
    for b in current_bids(con):
        day = date.fromisoformat(b["award_date"])
        award = link_award(awards, b["player_key"], day)
        offers.append(dict(b, award=award, check=api_check(award, b["winning_team_id"], b["winning_amount"]),
                           when=award["when"] if award else datetime.combine(day, datetime.min.time(), LEAGUE_TZ) + timedelta(hours=12)))
    return offers, [o for o in offers if start <= o["when"] < end]


def checked_through(con):
    """The "# checked_through:" date of the newest successful bids import, or
    None (no import, or that import's file had no usable line)."""
    r = con.execute("SELECT checked_through FROM lg_bid_imports WHERE ok=1 ORDER BY import_id DESC LIMIT 1").fetchone()
    return date.fromisoformat(r[0]) if r and r[0] else None


def no_offer_wording(award_day, checked):
    """What an award with no losing offer entered is: "uncontested" on or
    before the checked-through date, "not typed in yet" after it, and the
    unsure "uncontested, or not typed in yet" when no date is set."""
    if checked is None:
        return "uncontested, or not typed in yet"
    return "uncontested" if award_day <= checked else "not typed in yet"


def bidder_activity(con, activity, in_window, checked=None, window_start=None):
    """(basis, {team_id: bids in the window}): every winning claim Yahoo's API
    lists plus every hand-entered losing offer. With no losing offers entered
    for the window it is winning claims only, and the basis says so -- unless
    `checked` (the checked-through date) reaches into the window starting on
    `window_start` (a date), in which case there were simply none."""
    bids = Counter({team_id: sum(1 for e in evs if e["waiver"]) for team_id, evs in activity.items()})
    if checked is None:
        caveat = "An award with no rows in the file counts as uncontested, whether or not it has been entered yet"
    else:
        caveat = (f"Losing offers are checked through {checked}: an award up to then with no rows was uncontested; "
                  f"a later one may not be typed in yet, so its losing offers are missing from these counts")
    if not in_window:
        if checked is not None and window_start is not None and checked >= window_start:
            return f"all bids -- winning claims from Yahoo's API; no losing offers in the window. {caveat}", bids
        return "winning waiver claims only -- no losing offers have been entered for this window", bids
    bids.update(o["losing_team_id"] for o in in_window)
    imported = con.execute("SELECT MAX(imported_at) FROM lg_bid_imports WHERE ok=1").fetchone()[0]
    return (f"all bids -- winning claims from Yahoo's API plus {len(in_window)} hand-entered losing offer(s) on "
            f"{len({(o['award_date'], o['player_key']) for o in in_window})} award(s) in the window (bids.csv, imported "
            f"{imported[:10]}; newest award entered {max(o['award_date'] for o in in_window)}). {caveat}", bids)


def bid_amounts(activity, in_window):
    """{team_id: [amount]}: every bid bidder_activity() counts -- the winning
    claims in `activity` plus the losing offers in `in_window` -- in dollars,
    $0 included. Callers leave out the $0 ones where only price competition
    matters (paid bids, typical paid bid)."""
    out = defaultdict(list)
    for team_id, evs in activity.items():
        out[team_id] += [e["faab_bid"] for e in evs if e["waiver"]]
    for o in in_window:
        out[o["losing_team_id"]].append(o["losing_amount"])
    return out


def award_key(offer):
    """What a losing offer was bid on: the API's transaction id when linked,
    else (award date, player) -- the same award either way."""
    return offer["award"]["transaction_id"] if offer["award"] else (offer["award_date"], offer["player_key"])


def top_offers(offers):
    """{award_key: the highest losing offer entered for that award}."""
    top = {}
    for o in offers:
        k = award_key(o)
        top[k] = max(top.get(k, o["losing_amount"]), o["losing_amount"])
    return top


def by_priority(winning_amount, next_offer):
    """An award won at $0 over a $0 next offer was decided by waiver priority,
    not by price. Unknown (False) when no losing offer was entered."""
    return winning_amount == 0 and next_offer == 0


def bid_margins(activity, offers, team_id):
    """A team's winning waiver claims in the window with what the next-highest
    offer was: [dict(transaction_id, when, added, won_at, next_offer,
    next_team_id, margin, n_offers, check, by_priority)]. next_* and margin are None when
    no losing offer has been entered for the award (uncontested, or not typed
    in yet). `activity` is team_activity()'s result, `offers` every linked
    hand-entered losing offer (losing_bids()[0])."""
    by_award = defaultdict(list)
    for o in offers:
        if o["award"]:
            by_award[o["award"]["transaction_id"]].append(o)
    out = []
    for e in activity.get(team_id, []):
        if not e["waiver"]:
            continue
        others = sorted(by_award.get(e["transaction_id"], []), key=lambda o: -o["losing_amount"])
        top = others[0] if others else None
        out.append({"transaction_id": e["transaction_id"], "when": e["when"], "added": e["added"], "won_at": e["faab_bid"],
                    "next_offer": top["losing_amount"] if top else None, "next_team_id": top["losing_team_id"] if top else None,
                    "margin": e["faab_bid"] - top["losing_amount"] if top else None, "n_offers": len(others),
                    "check": top["check"] if top else None,
                    "by_priority": by_priority(e["faab_bid"], top["losing_amount"] if top else None)})
    return out


def current_week(con, today=None):
    """The league week the report is for, or None before any scoreboard snapshot."""
    snap = latest_ok(con, "scoreboard")
    weeks = league_weeks(con, snap[0]) if snap else {}
    return report_window(weeks, today or datetime.now(LEAGUE_TZ).date())[1] if weeks else None


def build_report(con, today=None, n_weeks=REPORT_WEEKS, team_id=None, funded_min=FUNDED_MIN_FAAB):
    """The league activity report as markdown, or None when a needed snapshot
    is missing. Holds team names and manager nicknames: local only. team_id:
    whose winning bids get the margin section (default: default_team).
    funded_min: FAAB left (in $) for the funded-active-bidder flag."""
    snaps = {r: latest_ok(con, r) for r in ("teams", "transactions", "scoreboard")}
    if any(v is None for v in snaps.values()):
        return None
    weeks = league_weeks(con, snaps["scoreboard"][0])
    if not weeks:
        return None
    today = today or datetime.now(LEAGUE_TZ).date()
    first, current, start, end = report_window(weeks, today, n_weeks)
    teams = [dict(r) for r in con.execute("SELECT * FROM lg_teams WHERE snapshot_id=? ORDER BY team_id", (snaps["teams"][0],))]
    name = {t["team_id"]: t["team_name"] for t in teams}
    mine = team_id if team_id is not None else default_team(con)
    activity = team_activity(con, snaps["transactions"][0], start, end)
    offers, in_window = losing_bids(con, snaps["transactions"][0], start, end)
    checked = checked_through(con)
    basis, bids = bidder_activity(con, activity, in_window, checked, weeks[first][0])
    losing_known = bool(in_window) or (checked is not None and checked >= weeks[first][0])
    paid = {t: [a for a in amounts if a > 0] for t, amounts in bid_amounts(activity, in_window).items()}
    top = top_offers(offers)
    lost = defaultdict(list)
    for o in in_window:
        lost[o["losing_team_id"]].append(o)
    season_spent = {r[0]: r[1] for r in con.execute(
        "SELECT p.destination_team_id, SUM(COALESCE(t.faab_bid, 0)) FROM lg_transactions t JOIN lg_transaction_players p "
        "ON p.snapshot_id = t.snapshot_id AND p.transaction_id = t.transaction_id WHERE t.snapshot_id=? AND p.action='add' "
        "AND p.source_type='waivers' GROUP BY 1", (snaps["transactions"][0],))}
    hidden = unlisted_ids(con, snaps["transactions"][0], start, end)

    def stamp(s):
        return f"{datetime.fromisoformat(s):%Y-%m-%d %H:%M} UTC"

    def day(when):
        return f"{when.astimezone(LEAGUE_TZ):%a %m-%d}"

    def funded(t):
        if bids.get(t["team_id"], 0) < ACTIVE_BIDDER_MIN or not paid.get(t["team_id"]):
            return "no"
        if t["faab_balance"] is None:
            return "? (FAAB unknown)"
        return "**yes**" if t["faab_balance"] >= funded_min else "no"

    priority = " -- decided by waiver priority"

    lines = [
        f"# League activity: weeks {first}-{current}" if first != current else f"# League activity: week {current}",
        "",
        "LOCAL ONLY -- this file names teams and managers. It lives in the gitignored `league_data/` and is never committed.",
        "",
        f"- Window: league weeks {first}-{current} by Yahoo's week boundaries ({weeks[first][0]} to {weeks[current][1]}, Pacific days)",
        f"- Data: transactions as of {stamp(snaps['transactions'][1])}, FAAB balances as of {stamp(snaps['teams'][1])}",
        f"- **Active bidder** = at least {ACTIVE_BIDDER_MIN} bids in the window, $0 bids included (a $0 bid shows interest). "
        f"Basis: **{basis}.**",
        "- **Paid bids** and **typical paid bid** (their median) count only bids above $0, winning and losing, on the same basis: "
        "a $0 bid is interest, not price competition.",
        f"- **Funded active bidder** = an active bidder with at least one paid bid in the window and at least ${funded_min:g} FAAB left.",
        f"- Losing offers (bids.csv) checked through: "
        + (f"**{checked}** -- awards up to then with no rows were uncontested" if checked else
           "not set -- an award with no rows may be uncontested or not typed in yet (add a `# checked_through: YYYY-MM-DD` line)"),
        f"- Transaction ids in the window that Yahoo's list skips: {hidden} (probably failed waiver claims; Yahoo's API doesn't say whose)",
        "",
        "| Team | Manager | FAAB left | Spent (season) | Claims won | Losing bids | Paid bids | Typical paid bid | Spent in window "
        "| Free-agent adds | Drops | Trades | Active bidder | Funded active bidder |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for t in sorted(teams, key=lambda t: (-bids.get(t["team_id"], 0), t["team_id"])):
        evs = activity.get(t["team_id"], [])
        claims = [e for e in evs if e["waiver"]]
        lines.append(
            f"| {t['team_name']}{' (me)' if t['team_id'] == mine else ''} | {t['manager_nickname'] or ''} | "
            f"{'?' if t['faab_balance'] is None else '$' + str(t['faab_balance'])} | ${season_spent.get(t['team_id'], 0)} | {len(claims)} | "
            f"{len(lost[t['team_id']]) if losing_known else 'n/a'} | {len(paid.get(t['team_id'], []))} | "
            f"{'$%g' % median(paid[t['team_id']]) if paid.get(t['team_id']) else '-'} | "
            f"${sum(e['faab_bid'] for e in claims)} | {sum(1 for e in evs if e['added'] and not e['waiver'] and e['type'] != 'trade')} | "
            f"{sum(len(e['dropped']) for e in evs if e['type'] != 'trade')} | {sum(1 for e in evs if e['type'] == 'trade')} | "
            f"{'**yes**' if bids.get(t['team_id'], 0) >= ACTIVE_BIDDER_MIN else 'no'} | {funded(t)} |")

    # the chosen team's winning bids: how much more than the next offer it paid
    lines += ["", "## My winning bids: margin over the second-highest offer", ""]
    won = bid_margins(activity, offers, mine)
    if not won:
        lines.append("No winning waiver claims in the window.")
    for m in won:
        if m["by_priority"]:
            lines.append(f"- {day(m['when'])} -- {', '.join(m['added'])}: won at $0, next offer $0 ({name.get(m['next_team_id'], '?')})"
                         f"{priority}" + (f", {m['n_offers']} losing offers" if m["n_offers"] > 1 else "")
                         + ("" if m["check"] == "ok" else " -- CHECK: the typed winner doesn't match Yahoo's, see below"))
        elif m["next_offer"] is not None:
            lines.append(f"- {day(m['when'])} -- {', '.join(m['added'])}: won at ${m['won_at']}, next offer ${m['next_offer']} "
                         f"({name.get(m['next_team_id'], '?')}) -- **margin ${m['margin']}**"
                         + (f", {m['n_offers']} losing offers" if m["n_offers"] > 1 else "")
                         + ("" if m["check"] == "ok" else " -- CHECK: the typed winner doesn't match Yahoo's, see below"))
        else:
            lines.append(f"- {day(m['when'])} -- {', '.join(m['added'])}: won at ${m['won_at']}, no losing offer entered "
                         f"({no_offer_wording(m['when'].astimezone(LEAGUE_TZ).date(), checked)})")

    flagged = [o for o in offers if o["check"] != "ok"]
    if flagged:
        lines += ["", "## Check these entries in bids.csv", "",
                  "The typed winner of each award below doesn't match Yahoo's own record, so one of the two was typed wrong. "
                  "Fix the row and run `import-bids` again."]
        for o in sorted(flagged, key=lambda o: (o["award_date"], o["player_key"], o["losing_team_id"])):
            a = o["award"]
            lines.append(f"- {o['award_date']} {o['player']}: typed {name.get(o['winning_team_id'], '?')} for ${o['winning_amount']}; "
                         + ("Yahoo's transactions have no waiver add for this player within a day of that date" if a is None else
                            f"Yahoo has {name.get(a['team_id'], '?')} for ${a['amount']}"))

    for t in teams:
        evs = [(e["when"], 0, e) for e in activity.get(t["team_id"], [])] + [(o["when"], 1, o) for o in lost[t["team_id"]]]
        lines += ["", f"## {t['team_name']}{' (me)' if t['team_id'] == mine else ''}", ""]
        if not evs:
            lines.append("No transactions in the window.")
            continue
        for when, is_bid, e in sorted(evs, key=lambda x: (x[0], x[1])):
            if is_bid:
                lines.append(f"- {day(when)} -- losing bid, ${e['losing_amount']}: {e['player']} (went to "
                             f"{name.get(e['winning_team_id'], '?')} for ${e['winning_amount']})"
                             + (priority if by_priority(e["award"]["amount"] if e["award"] else e["winning_amount"],
                                                        top[award_key(e)]) else ""))   # Yahoo's amount over a typo
                continue
            how = (f"waiver claim, ${e['faab_bid']}" if e["waiver"] else "trade" if e["type"] == "trade" else
                   "free agent" if e["added"] else "drop")
            moved = " / ".join(([("got " if e["type"] == "trade" else "+") + ", ".join(e["added"])] if e["added"] else [])
                               + ([("gave " if e["type"] == "trade" else "-") + ", ".join(e["dropped"])] if e["dropped"] else []))
            lines.append(f"- {day(when)} -- {how}: {moved}"
                         + (priority if e["waiver"] and by_priority(e["faab_bid"], top.get(e["transaction_id"])) else ""))
    return "\n".join(lines) + "\n"


# ------------------------------------------------- scoring: Yahoo vs the tracker

def scoring_check(con, tracker_rules, unsupported=("pick_six",)):
    """Compare the league's own scoring rules (newest settings snapshot) with
    the tracker's. -> dict(ok, lines, unsupported): `lines` one per category
    that differs or that the tracker has no rule for; `unsupported` the
    categories the league scores that the tracker knowingly cannot."""
    snap = latest_ok(con, "settings")
    if snap is None:
        return {"ok": False, "lines": ["no settings snapshot yet -- run `snapshot` first"], "unsupported": []}
    lines, known = [], []
    yahoo = {}
    for name, value in con.execute("SELECT name, value FROM lg_scoring_rules WHERE snapshot_id=?", (snap[0],)):
        key = YAHOO_RULES.get(name)
        if key is None:
            if value:
                lines.append(f"Yahoo scores '{name}' at {value:g}; the tracker has no such category")
            continue
        yahoo[key] = value
        if key in unsupported:
            known.append(f"{name} ({value:g}): not scored by the tracker -- nflverse's weekly stats have no column for it")
        elif tracker_rules.get(key) is None:
            if value:
                lines.append(f"{key}: Yahoo {value:g}, tracker has no rule")
        elif abs(float(tracker_rules[key]) - value) > 1e-9:
            lines.append(f"{key}: Yahoo {value:g}, tracker {tracker_rules[key]:g}")
    lines += [f"{key}: tracker {value:g}, but Yahoo doesn't score it" for key, value in tracker_rules.items()
              if key not in yahoo and value]
    return {"ok": not lines, "lines": lines, "unsupported": known}


def points_check(con, store, season, weeks=None):
    """Yahoo's fantasy points for every rostered player-week on record against
    the same player's points from nflverse stats under the tracker's rules
    (`store`: a tracker scoring.StatsStore). -> dict(n, same, differ): differ
    is [(week, name, position, yahoo points, tracker points)]."""
    n = same = 0
    differ = []
    for week in sorted(weeks or lineup_weeks_stored(con)):
        rows = store.week_rows(season, week)
        for team_rows in week_lineups(con, week).values():
            for r in team_rows:
                ours = rows[r["player_id"]]["points"] if r["player_id"] in rows else 0.0
                n += 1
                if abs(r["points"] - ours) < 0.005:
                    same += 1
                else:
                    differ.append((week, r["name"], r["position"], r["points"], round(ours, 2)))
    return {"n": n, "same": same, "differ": differ}


# ----------------------------------------------------------------------- CLI

def cmd_snapshot(args):
    con = connect(args.db)
    try:
        key = args.league_key or league_availability.configured_league_key()
        summary = take_snapshot(con, key, args.raw_dir, args.roster or REPO_ROOT / "nflverse_raw" / f"roster_weekly_{args.season}.csv")
    finally:
        con.close()
    failed = [r for r in RESOURCES if not summary[r]["ok"]]
    print("[LEAGUE DATA] " + "; ".join(f"{r} {'ok' if summary[r]['ok'] else 'FAILED'}: {summary[r]['n_rows']} rows" for r in RESOURCES))
    return 1 if failed else 0


def cmd_report(args):
    con = connect(args.db)
    try:
        md, week = build_report(con, funded_min=args.funded_min), current_week(con)
    finally:
        con.close()
    if md is None:
        print("[LEAGUE DATA] nothing to report yet -- run `snapshot` first", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"league_activity_week{week:02d}.md"     # one file per league week, rewritten as the week goes on
    path.write_text(md, encoding="utf-8")
    print(f"[SAVED] {path}")   # the path only: the report itself names managers
    return 0


def cmd_check_scoring(args):
    sys.path.insert(0, str(REPO_ROOT / "tracker"))
    import scoring                       # the tracker's; imported here so the data layer itself doesn't depend on it
    cfg = scoring.load_config(args.config)
    con = connect(args.db)
    try:
        rules = scoring_check(con, cfg["scoring"], scoring.UNSUPPORTED_RULES)
        pts = points_check(con, scoring.StatsStore(args.stats_dir, cfg["scoring"]), args.season)
    finally:
        con.close()
    for line in rules["lines"]:
        print(f"[SCORING] RULE DIFFERS: {line}")
    for line in rules["unsupported"]:
        print(f"[SCORING] known gap: {line}")
    for week, name, pos, yahoo, ours in pts["differ"]:
        print(f"[SCORING] points differ: week {week} {name} ({pos}): Yahoo {yahoo:.2f}, tracker {ours:.2f}")
    print(f"[SCORING] rules {'match Yahoo' if rules['ok'] else 'DO NOT match Yahoo'}; {pts['same']} of {pts['n']} rostered "
          f"player-weeks equal Yahoo's points exactly, {len(pts['differ'])} differ")
    return 0 if rules["ok"] else 1


def cmd_import_bids(args):
    path = Path(args.file)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(BIDS_TEMPLATE, encoding="utf-8")
        print(f"[BIDS] created {path} with the header and an example line -- type the losing offers in and run this again")
        return 0
    con = connect(args.db)
    try:
        out = import_bids(con, path, args.raw_dir)
    finally:
        con.close()
    if out["unchanged_file"]:
        print("[BIDS] the file hasn't changed since the last import -- nothing to do")
        return 0
    for title, items in (("REJECTED (not stored)", out["rejected"]), ("FLAGGED (stored; doesn't match Yahoo's record of the award)", out["flagged"]),
                         ("corrected (newest version is used; the earlier one stays on record)", out["corrections"]),
                         ("withdrawn", out["withdrawn"])):
        for item in items:
            print(f"[BIDS] {title}: {item}")
    if not out["ok"]:
        print(f"[BIDS] FAILED: {out['error']}")
        return 1
    print(f"[BIDS] {out['n_rows']} losing offer(s) in the file: {out['n_new']} new, {out['n_corrected']} corrected, "
          f"{out['n_unchanged']} unchanged, {out['n_withdrawn']} withdrawn; {len(out['rejected'])} line(s) rejected, "
          f"{len(out['flagged'])} flagged; checked through "
          f"{out['checked_through'] or 'not set (no # checked_through: line)'}")
    return 1 if out["rejected"] or out["flagged"] else 0


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot", help="fetch teams, transactions, rosters and the scoreboard and append them")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="where the raw Yahoo responses are kept, gzip-compressed")
    p.add_argument("--league-key", default=None, help="default: YAHOO_LEAGUE_KEY from the environment or league/.env")
    p.add_argument("--season", type=int, default=utcnow().year if utcnow().month >= 3 else utcnow().year - 1)
    p.add_argument("--roster", default=None, help="weekly roster CSV for the Yahoo -> gsis id match (default: nflverse_raw's)")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("check-scoring", help="compare Yahoo's scoring rules and player points with the tracker's (read-only)")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--config", default=None, help="tracker hit config to check (default tracker/hit_config.json)")
    p.add_argument("--stats-dir", default=str(REPO_ROOT / "nflverse_raw"))
    p.add_argument("--season", type=int, default=utcnow().year if utcnow().month >= 3 else utcnow().year - 1)
    p.set_defaults(fn=cmd_check_scoring)
    p = sub.add_parser("import-bids", help="bring the stored losing FAAB offers in line with the hand-typed CSV")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--file", default=str(DEFAULT_BIDS_CSV), help="created with a header and an example line if it doesn't exist")
    p.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR), help="where a gzip copy of each imported file is kept")
    p.set_defaults(fn=cmd_import_bids)
    p = sub.add_parser("report", help="write the league activity report (local only)")
    p.add_argument("--db", default=str(DEFAULT_DB))
    p.add_argument("--out-dir", default=str(DEFAULT_REPORT_DIR))
    p.add_argument("--funded-min", type=float, default=FUNDED_MIN_FAAB,
                   help=f"FAAB left (in $) for the funded-active-bidder flag (default {FUNDED_MIN_FAAB})")
    p.set_defaults(fn=cmd_report)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
