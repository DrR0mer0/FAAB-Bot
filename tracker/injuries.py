#!/usr/bin/env python3
"""Pre-game injury designations for the tracker's pool rule.

POOL RULE (from 2026 week 5; part of the `pool_rule` recorded with every run --
see tracker.POOL_RULE): a player
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

ONE DEFINITION, TWO USERS (from 2026-10-18). model/score_week.py applies the
same rule to its own ranking: an Out/Doubtful player is not ranked and is
listed in its injury_watch_list. Both read the statuses and the age limit from
rule() (tracker/hit_config.json's `run` section) and decide with
excluded_ids(). In a tracker `log` run the designations are resolved ONCE here
and handed to score_week.py as a file (designations_record /
write_designations / read_designations), so the two can't see different
reports; the tracker's own split_pool stays as its guarantee. A score_week.py
run by hand resolves them itself with resolve_standalone() -- the same
nflverse source and ESPN fallback, the fallback taken from the newest roster-%
snapshot within ownership_max_age_hours (it never takes a live snapshot).
The same designations are also used to FLAG players in the prediction markdown.
"""
import csv
import hashlib
import io
import json
import sys
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

NFLVERSE_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{season}.csv"
HIT_CONFIG = Path(__file__).resolve().parent / "hit_config.json"
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


def rule(cfg=None):
    """(exclude, max_age_hours, espn_max_age_hours): the designations that keep
    a player out, how old the nflverse file may be, and how old the roster-%
    snapshot behind the ESPN fallback may be. From hit_config.json's `run`
    section -- the one place both the tracker and score_week.py read it."""
    run = (cfg if cfg is not None else json.loads(HIT_CONFIG.read_text(encoding="utf-8")))["run"]
    return tuple(run["pool_exclude_injury_statuses"]), run["injury_report_max_age_hours"], run["ownership_max_age_hours"]


def excluded_ids(status, exclude):
    """{player_id: designation} for every player designated one of `exclude` -- THE decision, for both users."""
    return {pid: st for pid, st in (status or {}).items() if st in exclude}


def split_pool(pool_src, status, exclude):
    """(kept, excluded {player_id: designation}) -- pool rows whose player is
    designated one of `exclude` are left out."""
    out = excluded_ids(status, exclude)
    kept = [r for r in pool_src if r["player_id"] not in out]
    return kept, {r["player_id"]: out[r["player_id"]] for r in pool_src if r["player_id"] in out}


def designations_record(inj, exclude, provided_by):
    """What score_week.py needs to apply the rule, and records with its output:
    the source, why it was or wasn't nflverse, the excluded players and the
    nflverse file's identity. JSON-ready."""
    lm = inj.get("last_modified")
    return {"source": inj["source"], "reason": inj.get("reason"), "exclude": list(exclude),
            "excluded": excluded_ids(inj.get("status"), exclude), "nflverse_sha256": inj.get("sha256"),
            "nflverse_last_modified": lm.isoformat() if hasattr(lm, "isoformat") else lm, "provided_by": provided_by}


def skipped_record(reason, exclude, provided_by):
    return {"source": "skipped", "reason": reason, "exclude": list(exclude), "excluded": {}, "nflverse_sha256": None,
            "nflverse_last_modified": None, "provided_by": provided_by}


def write_designations(path, record):
    Path(path).write_text(json.dumps(record, sort_keys=True), encoding="utf-8")


def read_designations(path):
    record = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = {"source", "exclude", "excluded"} - set(record)
    if missing:
        raise ValueError(f"injury designations file {path} lacks {sorted(missing)}")
    return record


def resolve_standalone(con, season, week, as_of, cfg=None, fetch=fetch_nflverse):
    """For a score_week.py run without the tracker: the same resolve(), with the
    ESPN fallback taken from the newest successful roster-% snapshot within the
    configured age (none is taken live). -> designations_record. Never raises."""
    exclude, max_age, espn_max_age = rule(cfg)
    espn = None
    try:
        import ownership  # same directory; only needed here
        found = ownership.latest_ok_espn(con, as_of, espn_max_age)
        espn = ownership.injury_status_by_player(con, found[0]) if found else None
    except Exception as e:  # noqa: BLE001 -- no snapshot tables, say: there's just no fallback
        print(f"[INJURY] no ESPN fallback available ({type(e).__name__}: {e})", file=sys.stderr)
    return designations_record(resolve(season, week, as_of, max_age, espn, fetch=fetch), exclude, "score_week")


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
