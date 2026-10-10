#!/usr/bin/env python3
"""Who is available in the user's own Yahoo league -- a DISPLAY-ONLY view.

At `log` time the tracker asks Yahoo which QB/RB/WR/TE are free agents or on
waivers in the league, maps them to gsis ids, and adds a section to each
prediction markdown listing that model's highest-ranked pool players who can
actually be picked up. Nothing here feeds the pool, a score, a rank, a pick or
a result, and the prediction JSON is never touched: O.D.D.S. is still measured
against every scored player, not against one league's waiver wire.

SOURCE -- Yahoo's Fantasy Sports API, read-only (league/yahoo_auth.py holds
the OAuth side): league/<key>/players;status=A is everyone available, 25 per
page, and the `ownership` sub-resource says which kind -- `freeagents` (an
immediate add) or `waivers` (a claim, with the date Yahoo processes it). A
healthy answer is ~40 pages / ~970 players and takes about ten seconds.

IDS -- Yahoo's records carry no gsis id, so each player is matched, in order:
  yahoo_id     the yahoo_id column of nflverse's weekly roster file. Exact, but
               it only covers ~40% of rostered players and no recent rookies.
  roster_name  normalized name + position, UNIQUE among the season's weekly
               roster (current players only, so a retired namesake can't win).
  ref_name     the same against ref_players (ownership.build_name_index).
The method is stored with each row; an ambiguous or unknown name stays
unmatched (player_id NULL) rather than guessed, and an unmatched player simply
cannot be listed. Measured 2026-10-10: 904 of 973 available players matched,
no disagreement between the id and name routes on the 522 that had both, and
of 404 scored players one real miss (Yahoo "Matt Hibner", nflverse "Matthew").

STORAGE -- with the run, append-only: one tr_league_snapshots row (status,
counts, the SHA-256 and file name of the raw response) and one
tr_league_availability row per available player. The raw response -- every
page, in one JSON document -- is a gzip file under raw/league/ (gitignored).
A failed fetch is stored too, as an ok=0 snapshot.

LEAGUE KEY -- YAHOO_LEAGUE_KEY in league/.env (gitignored, next to the Yahoo
credentials it needs anyway), e.g. 470.l.123456: game key, a lower-case L,
league id. It is deliberately NOT in a committed file: the repo is public and
league data stays local (see .gitignore). The committed record of a run -- the
ledger export -- carries the snapshot's SHA-256 and counts, never the key.

FAILURE -- the same policy as the roster-% feed: nothing here raises, and a
fetch that fails, hangs or comes back in an unexpected shape is loud, stored
as failed, and leaves the run to log normally with a section that says
availability was unavailable.
"""
import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

import ledger
import ownership

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = REPO_ROOT / "raw" / "league"
LEAGUE_DIR = REPO_ROOT / "league"
LEAGUE_KEY_VAR = "YAHOO_LEAGUE_KEY"
LEAGUE_KEY_RE = re.compile(r"^\d+\.l\.\d+$")

YAHOO_URL = ("https://fantasysports.yahooapis.com/fantasy/v2/league/{league_key}/players;status=A;position={positions}"
             ";start={start};count={count}/ownership?format=json")
POSITIONS = ("QB", "RB", "WR", "TE")
STATUS = {"freeagents": "FA", "waivers": "W"}   # Yahoo's ownership_type -> what is stored
STATUS_LABEL = {"FA": "Free agent", "W": "Waivers"}
PAGE_SIZE = 25           # Yahoo's maximum, whatever `count` asks for
MAX_PAGES = 200          # 5,000 players: far past any real answer, so a paging bug can't loop forever
MIN_ROWS = 200           # a 12-team league leaves ~970 QB/RB/WR/TE unrostered
REQUEST_TIMEOUT_S = 30
FETCH_BUDGET_S = 180     # for the whole fetch, token refresh included -- a hang must not hold up a scheduled run


def _banner(msg):
    bar = "!" * 78
    print(f"\n{bar}\n[LEAGUE WARNING] {msg}\n{bar}\n", file=sys.stderr)


def configured_league_key(env_path=None):
    """YAHOO_LEAGUE_KEY from the environment, else from league/.env; None if
    it isn't set. Read by hand so the tracker doesn't need python-dotenv."""
    key = os.environ.get(LEAGUE_KEY_VAR)
    path = Path(env_path or LEAGUE_DIR / ".env")
    if not key and path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == LEAGUE_KEY_VAR:
                key = value.strip().strip("'\"")
    return (key or "").strip() or None


def open_session():
    """An authenticated session from league/yahoo_auth.py, which refreshes and
    re-saves the token when it has expired. Imported here rather than at the
    top so the tracker (and its tests) run without the Yahoo libraries."""
    if str(LEAGUE_DIR) not in sys.path:
        sys.path.insert(0, str(LEAGUE_DIR))
    import logging

    import yahoo_auth
    logging.getLogger("yahoo_oauth").setLevel(logging.WARNING)   # it logs every token check at DEBUG, to stderr
    return yahoo_auth.get_session()


def parse_page(payload):
    """One page of league/<key>/players;status=A/ownership -> (rows, n_records).
    Strict on purpose: any shape it doesn't recognise raises, and the caller
    turns that into a failed fetch. n_records is how many player records the
    page held, which is what paging goes by."""
    players = payload["fantasy_content"]["league"][1]["players"]
    if not players:          # Yahoo answers with an empty list past the last player
        return [], 0
    rows, n = [], int(players["count"])
    for i in range(n):
        parts = players[str(i)]["player"]
        info = {}
        for part in parts[0]:
            if isinstance(part, dict):
                info.update(part)
        own = next(p["ownership"] for p in parts[1:] if isinstance(p, dict) and "ownership" in p)
        if own["ownership_type"] == "team":   # rostered between two pages: no longer available
            continue
        rows.append({
            "external_id": str(info["player_id"]), "name": info["name"]["full"],
            "position": info.get("primary_position") or info["display_position"].split(",")[0],
            "team": (info.get("editorial_team_abbr") or "").upper() or None,
            "status": STATUS[own["ownership_type"]], "waiver_date": own.get("waiver_date") or None})
    return rows, n


def _fetch(league_key, session, open_session, deadline):
    out = {"ok": False, "http_status": None, "raw_bytes": None, "rows": [], "n_pages": 0, "error": None,
           "url": YAHOO_URL.format(league_key="<league>", positions=",".join(POSITIONS), start="N", count=PAGE_SIZE)}
    pages, rows, seen = [], [], set()
    try:
        session = session or open_session()
        for page in range(MAX_PAGES):
            if time.monotonic() > deadline:
                out["error"] = f"ran out of time with {len(pages)} page(s) read"
                break
            resp = session.get(YAHOO_URL.format(league_key=league_key, positions=",".join(POSITIONS),
                                                start=page * PAGE_SIZE, count=PAGE_SIZE), timeout=REQUEST_TIMEOUT_S)
            out["http_status"] = resp.status_code
            pages.append({"start": page * PAGE_SIZE, "http_status": resp.status_code, "body": resp.text})
            if resp.status_code != 200:
                out["error"] = f"HTTP {resp.status_code} on page {page + 1}"
                break
            got, n = parse_page(resp.json())
            for r in got:
                if r["external_id"] not in seen:   # the list can shift under the paging; keep a player once
                    seen.add(r["external_id"])
                    rows.append(r)
            if n < PAGE_SIZE:
                break
        else:
            out["error"] = f"no end of the list after {MAX_PAGES} pages"
        if out["error"] is None and len(rows) < MIN_ROWS:
            out["error"] = f"only {len(rows)} available players returned (expected >= {MIN_ROWS})"
    except (Exception, SystemExit) as e:  # noqa: BLE001 -- yahoo_auth exits on missing credentials; nothing here may raise
        out["error"] = f"{type(e).__name__}: {e}"
    out["n_pages"] = len(pages)
    if pages:
        out["raw_bytes"] = json.dumps({"league_key": league_key, "pages": pages}, sort_keys=True).encode("utf-8")
    if out["error"] is None:
        out.update(ok=True, rows=rows)
    else:
        out["error"] = out["error"].replace(league_key, "<league>")   # errors end up in committed files; the key doesn't
    return out


def fetch(league_key, session=None, open_session=open_session, budget_s=FETCH_BUDGET_S):
    """-> dict(ok, http_status, raw_bytes, rows, n_pages, error, url). rows:
    one dict per available player (external_id = Yahoo's player id, name,
    position, team, status 'FA'|'W', waiver_date). Never raises, and never
    takes much longer than budget_s: the work runs in a thread that is left
    behind if Yahoo (or the token refresh, which has no timeout of its own)
    hangs."""
    box = {}
    worker = threading.Thread(target=lambda: box.update(out=_fetch(league_key, session, open_session, time.monotonic() + budget_s)),
                              daemon=True)
    worker.start()
    worker.join(budget_s + REQUEST_TIMEOUT_S)
    return box.get("out") or {"ok": False, "http_status": None, "raw_bytes": None, "rows": [], "n_pages": 0,
                              "error": f"no answer from Yahoo within {budget_s + REQUEST_TIMEOUT_S}s", "url": None}


def load_roster_index(roster_csv):
    """(yahoo_id -> gsis_id, (normalized name, position) -> gsis_id) from
    nflverse's weekly roster file; both empty if the file is missing. The name
    index holds UNIQUE matches only, like ownership.build_name_index."""
    yahoo_ids, names, dup = {}, {}, set()
    path = Path(roster_csv)
    if path.exists():
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                gsis = r.get("gsis_id")
                if not gsis:
                    continue
                if r.get("yahoo_id"):
                    yahoo_ids[str(r["yahoo_id"]).split(".")[0]] = gsis
                k = (ownership.norm_name(r.get("full_name")), r.get("position"))
                if k in names and names[k] != gsis:
                    dup.add(k)
                names[k] = gsis
    return yahoo_ids, {k: v for k, v in names.items() if k not in dup}


def map_players(rows, yahoo_ids, roster_names, ref_names):
    """Set player_id (gsis, or None) and match_method on every row, in place."""
    for r in rows:
        key = (ownership.norm_name(r["name"]), r["position"])
        r["player_id"], r["match_method"] = next(
            ((pid, method) for method, pid in (("yahoo_id", yahoo_ids.get(r["external_id"])),
                                               ("roster_name", roster_names.get(key)), ("ref_name", ref_names.get(key))) if pid),
            (None, None))
    return rows


def by_player(rows):
    """{gsis_id: row} for the matched rows. Should two Yahoo players land on
    one gsis id, the one matched by id wins over one matched by name."""
    out = {}
    for r in rows:
        pid = r.get("player_id")
        if pid and (pid not in out or (r["match_method"] == "yahoo_id" and out[pid]["match_method"] != "yahoo_id")):
            out[pid] = r
    return out


def resolve(league_key, con, roster_csv, fetch=fetch):
    """The league's availability for one run. Returns a dict:
      status       'ok' | 'unavailable'
      reason       why it is unavailable (None when ok)
      rows         one per available Yahoo player, mapped (see map_players)
      by_player    {gsis_id: row}
      n_available, n_waivers, n_matched, n_pages, http_status
      raw_bytes, sha256     every page as fetched, in one JSON document
    Never raises."""
    out = {"status": "unavailable", "reason": None, "rows": [], "by_player": {}, "n_available": 0, "n_waivers": 0,
           "n_matched": 0, "n_pages": 0, "http_status": None, "raw_bytes": None, "sha256": None}
    if not league_key:
        out["reason"] = f"no league is configured ({LEAGUE_KEY_VAR} in league/.env)"
    elif not LEAGUE_KEY_RE.match(league_key):
        out["reason"] = f"{LEAGUE_KEY_VAR} is not a Yahoo league key (expected <game>.l.<league id>, with a lower-case L)"
    else:
        got = fetch(league_key)
        out.update(n_pages=got.get("n_pages") or 0, http_status=got.get("http_status"), raw_bytes=got.get("raw_bytes"))
        if out["raw_bytes"]:
            out["sha256"] = hashlib.sha256(out["raw_bytes"]).hexdigest()
        if not got.get("ok"):
            out["reason"] = f"the Yahoo fetch failed: {got.get('error')}"
        else:
            try:
                yahoo_ids, roster_names = load_roster_index(roster_csv)
                rows = map_players(got["rows"], yahoo_ids, roster_names, ownership.build_name_index(con))
                out.update(status="ok", rows=rows, by_player=by_player(rows), n_available=len(rows),
                           n_waivers=sum(1 for r in rows if r["status"] == "W"),
                           n_matched=sum(1 for r in rows if r["player_id"]))
            except Exception as e:  # noqa: BLE001 -- a mapping problem is a missing view, not a failed run
                out["reason"] = f"the available players could not be matched to player ids ({type(e).__name__}: {e})"
    if out["status"] != "ok":
        _banner(f"league availability is unavailable -- {out['reason']}. The run is logged as usual; the prediction "
                f"markdown's league section will say so.")
    return out


def store(con, run_id, taken_at, league_key, view, raw_file):
    """Append the run's snapshot: one tr_league_snapshots row and its players.
    No commit -- the caller writes it in the run's own transaction."""
    sid = con.execute(
        "INSERT INTO tr_league_snapshots (run_id, taken_at, source, league_key, ok, error, http_status, n_pages, n_rows, "
        "n_matched, raw_sha256, raw_file) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, taken_at, "yahoo", league_key, 1 if view["status"] == "ok" else 0, view["reason"], view["http_status"],
         view["n_pages"], view["n_available"], view["n_matched"], view["sha256"], raw_file)).lastrowid
    ledger.insert_rows(con, "tr_league_availability", [
        {"snapshot_id": sid, "external_id": r["external_id"], "player_id": r["player_id"], "match_method": r["match_method"],
         "name": r["name"], "position": r["position"], "team": r["team"], "status": r["status"],
         "waiver_date": r["waiver_date"]} for r in view["rows"]])
    return sid


def run_record(view, raw_file, top_n, n_pool, n_pool_available):
    """What tr_runs.run_config_json keeps about the view (and so what the
    committed ledger export shows): status, counts and the raw response's hash."""
    return {"source": "yahoo", "status": view["status"], "reason": view["reason"], "top_n": top_n,
            "n_available": view["n_available"], "n_waivers": view["n_waivers"], "n_matched": view["n_matched"],
            "n_pool": n_pool, "n_pool_available": n_pool_available, "raw_sha256": view["sha256"], "raw_file": raw_file}


SECTION_TITLE = "## Available in my league"


def render_section(view, ranked, top_n, taken_at):
    """The markdown appended to one model's prediction report. ranked: that
    model's pool players in rank order -- dicts with rank, player_id, name,
    pos, team, score, exactly as the model scored them."""
    if view["status"] != "ok":
        return (f"\n{SECTION_TITLE}\n\nLeague availability was unavailable when this was generated ({view['reason']}), so "
                f"nobody is listed here.\n")
    avail = view["by_player"]
    mine = [r for r in ranked if r["player_id"] in avail]
    lines = [
        "", SECTION_TITLE, "",
        f"The {top_n} highest-ranked players of this run's pool who were free agents or on waivers in my Yahoo league at "
        f"{taken_at:%Y-%m-%d %H:%M} UTC ({len(mine)} of the pool's {len(ranked)} were). Rank and score are this model's, "
        f"as in the table above; a waiver date is when Yahoo processes claims on that player. Display only -- scores, "
        f"ranks and the tracker's pool are unchanged. {view['n_matched']} of the {view['n_available']} players Yahoo lists "
        f"as available could be matched to a player id; one that couldn't be cannot appear here.",
        ""]
    if not mine:
        return "\n".join(lines + ["Nobody in this run's pool was available."]) + "\n"
    lines += ["| Rank | Name | Pos | Team | Score | Status |", "|---:|---|---|---|---:|---|"]
    for r in mine[:top_n]:
        a = avail[r["player_id"]]
        status = STATUS_LABEL[a["status"]] + (f" ({a['waiver_date']})" if a["status"] == "W" and a["waiver_date"] else "")
        lines.append(f"| {r['rank']} | {r['name']} | {r['pos']} | {r['team']} | {r['score']:.4f} | {status} |")
    return "\n".join(lines) + "\n"
