#!/usr/bin/env python3
"""Roster-% snapshots for the tracker's crowd_hit metric and the "under 50%
owned" sleeper scoreboard.

PRIMARY -- ESPN's public fantasy endpoint (unofficial/undocumented, no auth):
platform-wide `percentOwned` and `percentChange` for every QB/RB/WR/TE.
Neither Yahoo (API access pending) nor Sleeper (no ownership field in its
public API) can supply this today. ESPN's number is ESPN's, not Yahoo's --
it's a proxy for "the crowd found this player", not a measurement of the
user's own league.

SECONDARY -- Sleeper's trending-adds endpoint: league-wide ADD COUNTS over a
lookback window (not a percentage), mapped Sleeper id -> espn_id -> gsis_id.

STORAGE -- the DB keeps what the tracker queries: one append-only
tr_ownership_snapshots row per fetch (timestamp, status, and the SHA-256 of the
raw response) and the parsed rows in tr_ownership (player_id, percent_owned,
percent_change, ... keyed to that snapshot). The raw response itself is written
gzip-compressed to a file OUTSIDE the DB (raw/ownership/ under the repo root,
gitignored; ~3.6 MB per ESPN snapshot), named <UTC timestamp>_<source>_<first
12 hex of the SHA-256>.json.gz, so old ones can be moved to an archive without
touching the ledger. The snapshot row records the file's name; the SHA-256
still identifies the exact bytes wherever the file ends up.

Because the ESPN endpoint is unofficial, every fetch is shape-validated; on ANY
failure (network, HTTP error, JSON change, implausible values) the failure is
recorded as an ok=0 snapshot row, a loud banner is printed, and nothing raises
-- a broken roster-% feed must never take down a prediction run. The snapshot
command exits non-zero so a scheduler can notice.
"""
import csv
import gzip
import hashlib
import json
import os
import re
import sys
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import ledger

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = REPO_ROOT / "raw" / "ownership"

ESPN_URL = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
            "/segments/0/leaguedefaults/3?view=kona_player_info")
# slot ids: 0=QB 2=RB 4=WR 6=TE
ESPN_FILTER = {"players": {"filterSlotIds": {"value": [0, 2, 4, 6]}, "limit": 3000,
                           "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
ESPN_POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}
SLEEPER_TRENDING_URL = "https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=200"
SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"

MIN_ESPN_ROWS = 300       # a healthy response is ~960 QB/RB/WR/TE
MIN_ESPN_HIGH_OWNED = 5   # sanity: at least this many players >= 90% owned


def _banner(msg):
    bar = "!" * 78
    print(f"\n{bar}\n[OWNERSHIP WARNING] {msg}\n{bar}\n", file=sys.stderr)


def now_utc():
    return datetime.now(timezone.utc)


def load_espn_crosswalk(players_csv=None):
    """espn_id -> gsis_id from nflverse players.csv."""
    p = Path(players_csv or REPO_ROOT / "nflverse_raw" / "players.csv")
    m = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("espn_id") and r.get("gsis_id"):
                    m[str(r["espn_id"]).split(".")[0]] = r["gsis_id"]
    return m


_SUFFIX = re.compile(r"\s(jr|sr|ii|iii|iv|v)$")


def norm_name(name):
    n = re.sub(r"[^a-z ]", "", (name or "").lower().replace(".", "").replace("'", ""))
    n = _SUFFIX.sub("", n.strip()).strip()
    return re.sub(r"\s+", " ", n)


def build_name_index(con):
    """(normalized name, position) -> gsis_id from ref_players, UNIQUE matches
    only -- ambiguity returns nothing rather than a guess. Used only as a
    fallback when the espn_id crosswalk misses (Sleeper often lacks ids on
    recent players)."""
    idx, dup = {}, set()
    try:
        rows = con.execute("SELECT player_id, full_name, pos FROM ref_players").fetchall()
    except Exception:  # noqa: BLE001 -- ref_players absent (tests): no fallback
        return {}
    for pid, full, pos in rows:
        k = (norm_name(full), pos)
        if k in idx and idx[k] != pid:
            dup.add(k)
        idx[k] = pid
    return {k: v for k, v in idx.items() if k not in dup}


def validate_espn(payload):
    """(ok, reason, rows). rows: parsed dicts. Strict on purpose -- an
    undocumented endpoint changing shape should be loud, not silently produce
    garbage ownership numbers."""
    try:
        players = payload["players"]
        if not isinstance(players, list):
            return False, "'players' is not a list", []
        rows = []
        for e in players:
            pl = e["player"]
            own = pl["ownership"]
            pct = float(own["percentOwned"])
            if not (0.0 <= pct <= 100.0):
                return False, f"percentOwned out of range: {pct} for {pl.get('fullName')}", []
            rows.append({
                "external_id": str(pl["id"]), "name": pl.get("fullName"),
                "position": ESPN_POSITIONS.get(pl.get("defaultPositionId")),
                "team": None, "percent_owned": pct,
                "percent_change": float(own["percentChange"]) if own.get("percentChange") is not None else None,
            })
    except (KeyError, TypeError, ValueError) as e:
        return False, f"unexpected response shape ({type(e).__name__}: {e})", []
    if len(rows) < MIN_ESPN_ROWS:
        return False, f"only {len(rows)} players returned (expected >= {MIN_ESPN_ROWS})", []
    if sum(1 for r in rows if r["percent_owned"] >= 90) < MIN_ESPN_HIGH_OWNED:
        return False, "implausible ownership distribution (almost nobody >= 90% owned)", []
    return True, "ok", rows


def fetch_espn(season, session=None, timeout=90):
    """-> dict(ok, http_status, raw_bytes, rows, error, url). Never raises."""
    url = ESPN_URL.format(season=season)
    out = {"ok": False, "http_status": None, "raw_bytes": None, "rows": [], "error": None, "url": url}
    try:
        resp = (session or requests).get(url, headers={"x-fantasy-filter": json.dumps(ESPN_FILTER)}, timeout=timeout)
        out["http_status"] = resp.status_code
        out["raw_bytes"] = resp.content
        if resp.status_code != 200:
            out["error"] = f"HTTP {resp.status_code}"
            return out
        ok, reason, rows = validate_espn(resp.json())
        out.update(ok=ok, rows=rows, error=None if ok else reason)
    except Exception as e:  # noqa: BLE001 -- the whole point is not to raise
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def fetch_sleeper_trending(session=None, timeout=60):
    """-> dict(ok, http_status, raw_bytes, rows, error, url, sleeper_players). Never raises.
    rows: {external_id (sleeper id), trending_count}."""
    out = {"ok": False, "http_status": None, "raw_bytes": None, "rows": [], "error": None,
           "url": SLEEPER_TRENDING_URL, "sleeper_players": {}}
    try:
        resp = (session or requests).get(SLEEPER_TRENDING_URL, timeout=timeout)
        out["http_status"] = resp.status_code
        out["raw_bytes"] = resp.content
        if resp.status_code != 200:
            out["error"] = f"HTTP {resp.status_code}"
            return out
        data = resp.json()
        if not isinstance(data, list) or (data and not {"player_id", "count"} <= set(data[0])):
            out["error"] = "unexpected trending response shape"
            return out
        out["rows"] = [{"external_id": str(d["player_id"]), "trending_count": int(d["count"])} for d in data]
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    try:  # id mapping only -- the 14MB players dump is NOT stored raw
        pr = (session or requests).get(SLEEPER_PLAYERS_URL, timeout=120)
        if pr.status_code == 200:
            out["sleeper_players"] = {
                pid: {"espn_id": str(v["espn_id"]) if v.get("espn_id") else None, "gsis_id": v.get("gsis_id"),
                      "name": v.get("full_name"), "position": v.get("position"), "team": v.get("team")}
                for pid, v in pr.json().items()}
    except Exception as e:  # noqa: BLE001
        out["error"] = f"trending ok, player-id map unavailable ({type(e).__name__}: {e})"
    return out


def raw_file_name(taken_at, source, sha256):
    ts = datetime.fromisoformat(taken_at).astimezone(timezone.utc)
    return f"{ts:%Y%m%dT%H%M%SZ}_{source}_{sha256[:12]}.json.gz"


def write_raw(raw_dir, taken_at, source, raw):
    """Write one raw response gzip-compressed under raw_dir and return the
    file's name. The file is read back and compared with `raw` before it gets
    its final name, and an existing file holding different bytes is never
    overwritten."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / raw_file_name(taken_at, source, hashlib.sha256(raw).hexdigest())
    if path.exists():
        if gzip.decompress(path.read_bytes()) != raw:
            raise FileExistsError(f"{path} already exists with different content")
        return path.name
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
    if gzip.decompress(tmp.read_bytes()) != raw:
        raise OSError(f"read-back of {tmp} does not match the response")
    os.replace(tmp, path)
    return path.name


def store_snapshot(con, source, taken_at, fetched, parsed_rows, raw_dir):
    """Append one snapshot: the raw response to a file under raw_dir, then its
    SHA-256 + file name and the parsed rows to the DB in a single transaction.
    A raw file that can't be written is loud but doesn't lose the snapshot."""
    raw = fetched.get("raw_bytes")
    raw_file = None
    if raw:
        try:
            raw_file = write_raw(raw_dir, taken_at, source, raw)
        except Exception as e:  # noqa: BLE001 -- disk trouble must not take the snapshot down with it
            _banner(f"could not write the raw {source} response under {raw_dir} ({type(e).__name__}: {e}). The parsed "
                    f"rows and the response's SHA-256 are still recorded; the raw bytes are NOT kept.")
    cur = con.execute(
        "INSERT INTO tr_ownership_snapshots (taken_at, source, url, ok, error, http_status, n_rows, raw_sha256, raw_file) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (taken_at, source, fetched.get("url"), 1 if fetched.get("ok") else 0, fetched.get("error"),
         fetched.get("http_status"), len(parsed_rows),
         hashlib.sha256(raw).hexdigest() if raw else None, raw_file))
    sid = cur.lastrowid
    con.executemany(
        "INSERT INTO tr_ownership (snapshot_id, source, external_id, player_id, name, position, team, "
        "percent_owned, percent_change, trending_count) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(sid, source, r["external_id"], r.get("player_id"), r.get("name"), r.get("position"), r.get("team"),
          r.get("percent_owned"), r.get("percent_change"), r.get("trending_count")) for r in parsed_rows])
    con.commit()
    return sid


def take_snapshot(con, season, raw_dir, now=None, crosswalk=None, espn_fetch=fetch_espn,
                  sleeper_fetch=fetch_sleeper_trending, include_sleeper=True):
    """Fetch + store ESPN (primary) and Sleeper trending (secondary); raw
    responses go to files under raw_dir (deliberately no default here, so a
    test can't write into the real raw/ownership/). Returns
    {'espn_ok', 'espn_snapshot_id', 'sleeper_ok', 'espn_matched', 'espn_rows', 'errors'}. Never raises."""
    taken_at = (now or now_utc()).isoformat()
    crosswalk = crosswalk if crosswalk is not None else load_espn_crosswalk()
    summary = {"espn_ok": False, "espn_snapshot_id": None, "sleeper_ok": None, "espn_matched": 0,
               "espn_rows": 0, "errors": []}

    espn = espn_fetch(season)
    rows = espn.get("rows", [])
    name_index = build_name_index(con)
    for r in rows:
        r["player_id"] = crosswalk.get(r["external_id"]) or name_index.get((norm_name(r.get("name")), r.get("position")))
    summary["espn_snapshot_id"] = store_snapshot(con, "espn", taken_at, espn, rows, raw_dir)
    summary["espn_ok"], summary["espn_rows"] = bool(espn["ok"]), len(rows)
    summary["espn_matched"] = sum(1 for r in rows if r.get("player_id"))
    if not espn["ok"]:
        msg = f"ESPN percentOwned fetch FAILED ({espn.get('error')}). Stored as a failed snapshot; roster-% is stale/unavailable."
        summary["errors"].append(msg)
        _banner(msg + " The endpoint is unofficial -- it may have changed shape.")

    if include_sleeper:
        sl = sleeper_fetch()
        srows = []
        for r in sl.get("rows", []):
            info = (sl.get("sleeper_players") or {}).get(r["external_id"], {})
            gsis = (info.get("gsis_id") or (crosswalk.get(info["espn_id"]) if info.get("espn_id") else None)
                    or name_index.get((norm_name(info.get("name")), info.get("position"))))
            srows.append({**r, "player_id": gsis, "name": info.get("name"), "position": info.get("position"),
                          "team": info.get("team")})
        store_snapshot(con, "sleeper_trending_add", taken_at, sl, srows, raw_dir)
        summary["sleeper_ok"] = bool(sl["ok"])
        if not sl["ok"]:
            msg = f"Sleeper trending fetch FAILED ({sl.get('error')})."
            summary["errors"].append(msg)
            _banner(msg)
    return summary


def migrate_raw_to_files(con, raw_dir):
    """One-time migration for a DB whose snapshots still carry the raw response
    as a zlib blob (tr_ownership_snapshots.raw_zlib): write every blob to a
    file under raw_dir, checked against the stored SHA-256 and read back, and
    only then record the file names and drop the column. That last step is the
    one sanctioned bypass of the table's append-only triggers -- they're dropped
    and recreated inside the same transaction, and nothing but raw_file /
    raw_zlib changes. Returns the list of files written; [] if already migrated."""
    table = "tr_ownership_snapshots"
    if "raw_zlib" not in ledger.table_columns(con, table):
        return []
    files = []
    for r in con.execute(f"SELECT snapshot_id, taken_at, source, raw_sha256, raw_zlib FROM {table} "
                         f"WHERE raw_zlib IS NOT NULL ORDER BY snapshot_id").fetchall():
        raw = zlib.decompress(r["raw_zlib"])
        if hashlib.sha256(raw).hexdigest() != r["raw_sha256"]:
            raise RuntimeError(f"snapshot {r['snapshot_id']}: stored blob does not match its raw_sha256 -- nothing changed")
        files.append((write_raw(raw_dir, r["taken_at"], r["source"], raw), r["snapshot_id"]))
    before = con.execute(f"SELECT COUNT(*), COUNT(raw_sha256) FROM {table}").fetchone()
    con.execute("BEGIN")
    try:
        con.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
        con.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
        con.executemany(f"UPDATE {table} SET raw_file=? WHERE snapshot_id=?", files)
        con.execute(f"ALTER TABLE {table} DROP COLUMN raw_zlib")
        ledger.create_append_only_triggers(con, table)
        after = con.execute(f"SELECT COUNT(*), COUNT(raw_sha256) FROM {table}").fetchone()
        if tuple(after) != tuple(before):
            raise RuntimeError(f"row counts changed during the migration ({tuple(before)} -> {tuple(after)})")
        con.commit()
    except Exception:
        con.rollback()
        raise
    return [name for name, _sid in files]


def latest_ok_espn(con, as_of, max_age_hours):
    """Newest successful ESPN snapshot taken at or before as_of and no older than
    max_age_hours; (snapshot_id, taken_at) or None."""
    cutoff = (as_of - timedelta(hours=max_age_hours)).isoformat()
    r = con.execute(
        "SELECT snapshot_id, taken_at FROM tr_ownership_snapshots WHERE source='espn' AND ok=1 "
        "AND taken_at <= ? AND taken_at >= ? ORDER BY taken_at DESC LIMIT 1", (as_of.isoformat(), cutoff)).fetchone()
    return (r[0], r[1]) if r else None


def ownership_by_player(con, snapshot_id):
    return {r[0]: r[1] for r in con.execute(
        "SELECT player_id, percent_owned FROM tr_ownership WHERE snapshot_id=? AND player_id IS NOT NULL", (snapshot_id,))}
