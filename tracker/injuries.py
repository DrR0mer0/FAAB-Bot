#!/usr/bin/env python3
"""Pre-game injury designations for the tracker's pool rule.

POOL RULE (from 2026 week 5; recorded with every run as `pool_rule`): a player
designated Out or Doubtful on the week's NFL injury report at log time is left
out of the pool -- for every model alike, the same way players whose game has
already kicked off are. He can't be a pick for O.D.D.S., the shadow model, a
baseline or a dart draw. (Of 2,363 skill-position player-weeks designated Out
in 2016-2024 none recorded a stat; of 442 Doubtful, 3 did. Questionable
players stay in: 60% of them play.)

SOURCE OF RECORD -- nflverse's `injuries` release: the official weekly report,
one row per (team, week, player) keyed by gsis_id, the same data the retrain
will use for history. nflverse refreshes it nightly at 06:00 UTC (11 PM
Pacific) and again some hours later; the nightly run is the one both morning
log jobs can count on. What it cannot contain: Sunday teams' game statuses
before Friday (so a `thu` run only knows about Thursday-game players), and
game-day inactives (announced 90 minutes before kickoff).

CROSS-CHECK AND FALLBACK -- ESPN's `injuryStatus`, parsed from the roster-%
snapshot the run already pins. Every run compares the two for its pool and
records the disagreements. ESPN is editorial, not the official report, so it
only DECIDES the pool when the nflverse file can't be fetched, hasn't been
updated within `injury_report_max_age_hours`, or has no rows for the week. If
neither source is available the run is still logged, loudly, with the rule not
applied and that fact recorded -- same policy as a missing roster-% feed.

Nothing here touches model/score_week.py's scores, ranks or JSON. The same
designations are also used to FLAG players in the prediction markdown.
"""
import csv
import hashlib
import io
import sys
from datetime import timezone
from email.utils import parsedate_to_datetime

import requests

POOL_RULE = "exclude-out-doubtful-v1"
NFLVERSE_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
ESPN_TO_REPORT = {"OUT": "Out", "DOUBTFUL": "Doubtful", "QUESTIONABLE": "Questionable"}


def _banner(msg):
    bar = "!" * 78
    print(f"\n{bar}\n[INJURY WARNING] {msg}\n{bar}\n", file=sys.stderr)


def fetch_nflverse(season, session=None, timeout=60):
    """-> dict(ok, raw_bytes, last_modified, error, url). last_modified is the
    file's own Last-Modified (an aware UTC datetime) -- when nflverse last
    published it, which is what "is the report current?" has to be judged
    by. Never raises."""
    url = NFLVERSE_URL.format(season=season)
    out = {"ok": False, "raw_bytes": None, "last_modified": None, "error": None, "url": url}
    try:
        resp = (session or requests).get(url, timeout=timeout)
        if resp.status_code != 200:
            out["error"] = f"HTTP {resp.status_code}"
            return out
        out["raw_bytes"] = resp.content
        lm = resp.headers.get("Last-Modified")
        if lm:
            out["last_modified"] = parsedate_to_datetime(lm).astimezone(timezone.utc)
        out["ok"] = True
    except Exception as e:  # noqa: BLE001 -- a broken feed must never take a prediction run down
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def parse_week(raw_bytes, season, week):
    """(designations, n_listed) for the regular-season report of (season, week):
    {gsis_id: report_status} for players WITH a game status, and how many
    players the report lists at all (practice-only rows included)."""
    status, n = {}, 0
    for r in csv.DictReader(io.StringIO(raw_bytes.decode("utf-8-sig"))):
        if r.get("season") != str(season) or r.get("week") != str(week) or r.get("game_type") not in (None, "", "REG"):
            continue
        n += 1
        st = (r.get("report_status") or "").strip()
        if st and r.get("gsis_id"):
            status[r["gsis_id"]] = st
    return status, n


def espn_designations(espn_status):
    """{gsis_id: ESPN injuryStatus} -> the same statuses in the report's own vocabulary."""
    return {pid: ESPN_TO_REPORT[s] for pid, s in (espn_status or {}).items() if s in ESPN_TO_REPORT}


def resolve(season, week, as_of, max_age_hours, espn_status, fetch=fetch_nflverse):
    """Decide which source's designations apply to this run. Returns a dict:
      source       'nflverse' | 'espn-fallback' | 'unavailable'
      reason       why nflverse was not used (None when it was)
      status       {gsis_id: designation} from the source that applies
      nflverse     {gsis_id: designation} if the fetch worked, else None
      espn         {gsis_id: designation} from the pinned snapshot, else None
      raw_bytes, sha256, last_modified     the nflverse file as fetched
    espn_status: {gsis_id: ESPN injuryStatus} for the run's pinned roster-%
    snapshot, or None when the run has no such snapshot. Never raises."""
    got = fetch(season)
    out = {"source": "nflverse", "reason": None, "status": {}, "nflverse": None,
           "espn": espn_designations(espn_status) if espn_status else None,
           "raw_bytes": got.get("raw_bytes") if got.get("ok") else None, "sha256": None,
           "last_modified": got.get("last_modified")}
    if not got.get("ok"):
        out["reason"] = f"nflverse injuries fetch failed ({got.get('error')})"
    else:
        out["sha256"] = hashlib.sha256(got["raw_bytes"]).hexdigest()
        try:
            out["nflverse"], n_listed = parse_week(got["raw_bytes"], season, week)
        except Exception as e:  # noqa: BLE001 -- an upstream format change is a missing report, not a crash
            n_listed, out["reason"] = 0, f"nflverse injuries file could not be parsed ({type(e).__name__}: {e})"
        if out["reason"] is None:
            lm = got.get("last_modified")
            age_h = None if lm is None else (as_of - lm).total_seconds() / 3600
            if n_listed == 0:
                out["reason"] = f"the nflverse injuries file has no rows for {season} week {week}"
            elif age_h is None:
                out["reason"] = "the nflverse injuries file carries no Last-Modified, so its age is unknown"
            elif age_h > max_age_hours:
                out["reason"] = (f"the nflverse injuries file was last updated {lm.isoformat()} ({age_h:.0f}h ago, limit "
                                 f"{max_age_hours}h) -- the nightly update is missing")
    if out["reason"] is None:
        out["status"] = out["nflverse"]
    elif out["espn"] is not None:
        out["source"], out["status"] = "espn-fallback", out["espn"]
        _banner(f"{out['reason']}. Falling back to ESPN's injuryStatus from the pinned roster-% snapshot for the pool rule.")
    else:
        out["source"] = "unavailable"
        _banner(f"{out['reason']}, and no ESPN snapshot is available either. The Out/Doubtful pool rule is NOT applied "
                f"to this run; players already ruled out may be in the pool.")
    return out


def split_pool(pool_src, status, exclude):
    """(kept, excluded {player_id: designation}) -- pool rows whose player is
    designated one of `exclude` are left out."""
    kept, excluded = [], {}
    for r in pool_src:
        st = status.get(r["player_id"])
        if st in exclude:
            excluded[r["player_id"]] = st
        else:
            kept.append(r)
    return kept, excluded


def disagreements(pool_src, nflverse, espn, exclude):
    """Pool players the two sources would treat differently under the rule:
    [{player_id, name, nflverse, espn}], statuses None when a source doesn't
    list the player. [] unless both sources are present."""
    if nflverse is None or espn is None:
        return []
    out = []
    for r in pool_src:
        a, b = nflverse.get(r["player_id"]), espn.get(r["player_id"])
        if (a in exclude) != (b in exclude):
            out.append({"player_id": r["player_id"], "name": r.get("name"), "nflverse": a, "espn": b})
    return out
