#!/usr/bin/env python3
"""Baseline tracker CLI -- is O.D.D.S. actually better than chance?

  python tracker/tracker.py log --season 2026 --week 5 --slot thu
  python tracker/tracker.py score --season 2026 --week 5
  python tracker/tracker.py report --season 2026 [--week 5]
  python tracker/tracker.py snapshot-ownership          # daily roster-% collection
  python tracker/tracker.py fetch-snaps --seasons 2024-2026
  python tracker/tracker.py backtest --seasons 2022-2025   # phase 2 (not built yet)

`log` runs model/score_week.py as-is (no model logic touched), ingests its
frozen JSON into the append-only ledger, then draws the dart / heuristic /
last_week baselines from that SAME pool. It REFUSES to log a run once the
slot's kickoff cutoff has passed (see check_slot_window). See README.md
("Baseline tracker") for the hit definition and the full workflow.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

TRACKER_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRACKER_DIR.parent
sys.path.insert(0, str(TRACKER_DIR))
sys.path.insert(0, str(REPO_ROOT / "model"))
sys.path.insert(0, str(REPO_ROOT / "data"))

import baselines  # noqa: E402
import ledger  # noqa: E402
import ownership  # noqa: E402
import report  # noqa: E402
import scoring  # noqa: E402
import snapcounts  # noqa: E402
from score_week import KICKOFF_TZ, parse_kickoff_utc  # noqa: E402 -- the one place kickoff timezone logic lives
from team_crosswalk import norm_team  # noqa: E402

DEFAULT_DB = REPO_ROOT / "faab_history_core_v0_1.db"
DEFAULT_PRED_DIR = REPO_ROOT / "predictions"
DEFAULT_RAW_DIR = REPO_ROOT / "nflverse_raw"
DEFAULT_EXPORT_DIR = TRACKER_DIR / "ledger_export"
DEFAULT_REPORT_DIR = TRACKER_DIR / "reports"
BASELINE_VERSIONS = {"dart": "dart-v1", "heuristic": "heuristic-v1", "last_week": "last_week-v1"}
METRICS = ("top24_hits", "spike_hits", "total_points")


class Refused(Exception):
    """A deliberate refusal (exit code 2): nothing was written."""


def utcnow():
    return datetime.now(timezone.utc)


def parse_as_of(s):
    if not s:
        return utcnow()
    d = datetime.fromisoformat(s)
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def default_season(now=None):
    now = now or utcnow()
    return now.year if now.month >= 3 else now.year - 1


def parse_seasons(spec):
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def with_db(fn):
    """Open the ledger DB for a command and ALWAYS close it, including when the
    command raises Refused (an open handle would otherwise pin the DB file)."""
    def wrapper(args):
        con = ledger.connect(args.db)
        try:
            return fn(args, con)
        finally:
            con.close()
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ----------------------------------------------------------------- slot guard

def week_kickoffs(con, season, week):
    out = []
    for r in con.execute("SELECT kickoff_utc FROM nfl_games WHERE season=? AND week=? AND is_playoffs=0", (season, week)):
        dt = parse_kickoff_utc(r[0])
        if dt is not None:
            out.append(dt)
    return sorted(out)


def slot_cutoff(kickoffs, slot):
    """(cutoff_utc, reason). `thu` = before the first game that precedes the
    Sunday slate (Thursday; also Wednesday/Friday/Saturday games); a week with
    no such game has no thu slot. `sun` = before the first Sunday kickoff
    (Eastern calendar day), which includes a 9:30am international game. Both
    resolved from real kickoff instants, never from a day-of-week flag."""
    if not kickoffs:
        return None, "no scheduled games found for this week"
    sundays = [k for k in kickoffs if k.astimezone(KICKOFF_TZ).weekday() == 6]
    sunday_start = min(sundays) if sundays else None
    if slot == "thu":
        pre = [k for k in kickoffs if sunday_start is not None and k < sunday_start]
        if not pre:
            return None, "this week has no game before the Sunday slate (no Thursday game) -- use --slot sun"
        return min(pre), None
    if slot == "sun":
        return (sunday_start or min(kickoffs)), None
    return None, f"unknown slot {slot!r}"


def check_slot_window(kickoffs, slot, as_of):
    """(ok, cutoff, message). Refuses at or after the cutoff."""
    cutoff, reason = slot_cutoff(kickoffs, slot)
    if cutoff is None:
        return False, None, reason
    if as_of >= cutoff:
        return False, cutoff, (f"REFUSED: the {slot} slot closed at {cutoff.isoformat()} "
                               f"(as-of {as_of.isoformat()}). Runs are only logged BEFORE kickoff so the ledger can never "
                               f"contain a prediction made with the answer in hand.")
    return True, cutoff, "ok"


def choose_prediction_path(pred_dir, season, week, slot):
    """First run of a week keeps the bare name (what verify_week.py reads and
    the committed history uses); a later same-week run gets '_sunday'. Never
    overwrites."""
    pred_dir = Path(pred_dir)
    bare = pred_dir / f"{season}_week{week:02d}.json"
    later = pred_dir / f"{season}_week{week:02d}_sunday.json"
    if not bare.exists():
        return bare
    if slot == "sun" and not later.exists():
        return later
    raise Refused(f"prediction file(s) already exist for {season} week {week} ({bare.name}"
                  f"{', ' + later.name if later.exists() else ''}); refusing to overwrite a frozen snapshot")


def check_data_fresh(con, store, season, week):
    if week <= 1:
        return
    g = con.execute("SELECT COUNT(*), SUM(CASE WHEN home_score IS NOT NULL AND away_score IS NOT NULL THEN 1 ELSE 0 END) "
                    "FROM nfl_games WHERE season=? AND week=? AND is_playoffs=0", (season, week - 1)).fetchone()
    if not g[0] or g[0] != g[1]:
        raise Refused(f"week {week - 1} isn't fully final in nfl_games -- run data/fetch_weekly_update.py --season {season} "
                      f"and data/load_nflverse_into_history_SAFE_v3.py --seasons {season} first")
    if store.max_week(season) < week - 1:
        raise Refused(f"stats CSV only runs through week {store.max_week(season)}; need week {week - 1} "
                      f"(run data/fetch_weekly_update.py --season {season})")


# -------------------------------------------------------------------- picks

def make_predictions(run_id, pool, cfg, seed, versions):
    """pool: list of dicts (player_id, position, odds_prod_score, odds_shadow_score,
    in_u50, snap_delta, target_share, last_week_points). Returns
    (prediction rows, sets) where sets feeds the dart distribution. O.D.D.S.
    production defines each pick set's size and position mix; every baseline
    matches it. The sleeper ('u50') scope re-picks from the under-50% pool."""
    ks, scopes = cfg["run"]["ks"], cfg["run"]["pools"]
    rows, sets = [], {}

    def add(model, ps, picks, scores=True):
        for rank, p in enumerate(picks, start=1):
            rows.append({"run_id": run_id, "model_name": model, "model_version": versions.get(model), "pick_set": ps,
                         "rank": rank, "player_id": p["player_id"], "position": p["position"],
                         "score": p.get(f"{model}_score") if scores else None, "random_seed": None, "draw_index": None})

    for scope in scopes:
        subset = pool if scope == "all" else [r for r in pool if r["in_u50"]]
        if not subset:
            continue
        prod_ranked = sorted(subset, key=lambda r: (-r["odds_prod_score"], r["player_id"]))
        have_shadow = all(r.get("odds_shadow_score") is not None for r in subset)
        shadow_ranked = sorted(subset, key=lambda r: (-r["odds_shadow_score"], r["player_id"])) if have_shadow else None
        heur_ranked, lw_ranked = baselines.rank_heuristic(subset), baselines.rank_last_week(subset)
        for k in ks:
            ps = f"{scope}_k{k}"
            prod_picks = prod_ranked[:k]
            mix = baselines.position_mix(prod_picks)
            add("odds_prod", ps, prod_picks)
            if shadow_ranked:
                add("odds_shadow", ps, shadow_ranked[:k])
            add("heuristic", ps, baselines.select_matched(heur_ranked, mix)[0], scores=False)
            add("last_week", ps, baselines.select_matched(lw_ranked, mix)[0], scores=False)
            sets[ps] = (subset, mix)
    draw0 = next(baselines.dart_draws(sets, seed, 1)) if sets else {}
    pos_of = {r["player_id"]: r["position"] for r in pool}
    for ps, ids in draw0.items():
        for rank, pid in enumerate(ids, start=1):
            rows.append({"run_id": run_id, "model_name": "dart", "model_version": versions.get("dart"), "pick_set": ps,
                         "rank": rank, "player_id": pid, "position": pos_of[pid], "score": None,
                         "random_seed": seed, "draw_index": 0})
    return rows, sets


def rebuild_dart_sets(con, run_id):
    """Recreate exactly the (pool, mix) per pick set that `log` used, from the
    ledger alone -- pool snapshot + O.D.D.S. production's logged picks."""
    pool = [dict(r) for r in con.execute("SELECT * FROM tr_pool WHERE run_id=?", (run_id,))]
    sets = {}
    for ps in sorted({r[0] for r in con.execute("SELECT DISTINCT pick_set FROM tr_predictions WHERE run_id=?", (run_id,))}):
        scope = ps.split("_k")[0]
        subset = pool if scope == "all" else [r for r in pool if r["in_u50"]]
        picks = [dict(r) for r in con.execute(
            "SELECT position FROM tr_predictions WHERE run_id=? AND model_name='odds_prod' AND pick_set=?", (run_id, ps))]
        sets[ps] = (subset, baselines.position_mix(picks))
    return sets


# ---------------------------------------------------------------------- log

def resolve_ownership(con, cfg, season, as_of, allow_live):
    """-> (status, snapshot_id, {gsis: percent_owned}). Never raises."""
    max_age = cfg["run"]["ownership_max_age_hours"]
    found = ownership.latest_ok_espn(con, as_of, max_age)
    if found is None and allow_live:
        ownership.take_snapshot(con, season)
        found = ownership.latest_ok_espn(con, utcnow(), max_age)
    if found is None:
        ownership._banner("no usable ESPN roster-% snapshot -- logging WITHOUT ownership; the sleeper (under-50%) "
                          "scoreboard will be empty for this run")
        return "unavailable", None, {}
    return "ok", found[0], ownership.ownership_by_player(con, found[0])


@with_db
def cmd_log(args, con):
    cfg = scoring.load_config(args.config)
    season, week, slot = args.season, args.week, args.slot
    as_of = parse_as_of(args.as_of)

    kicks = week_kickoffs(con, season, week)
    ok, cutoff, msg = check_slot_window(kicks, slot, as_of)
    if not ok:
        raise Refused(msg)
    if con.execute("SELECT 1 FROM tr_runs WHERE season=? AND week=? AND run_slot=?", (season, week, slot)).fetchone():
        raise Refused(f"a {slot} run for {season} week {week} is already in the ledger (append-only: no re-logging)")
    store = scoring.StatsStore(args.raw_dir, cfg["scoring"])
    check_data_fresh(con, store, season, week)
    pred_path = choose_prediction_path(args.predictions_dir, season, week, slot)

    cmd = [sys.executable, str(REPO_ROOT / "model" / "score_week.py"), "--season", str(season), "--week", str(week),
           "--json-out", str(pred_path), "--as-of", as_of.isoformat(), "--db", str(args.db),
           "--predictions-dir", str(args.predictions_dir)]
    print(f"[INFO] running O.D.D.S. as-is: {' '.join(cmd[1:])}")
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    written = [pred_path, pred_path.with_suffix(".md"),
               pred_path.with_name(pred_path.stem + "_shadow.json"), pred_path.with_name(pred_path.stem + "_shadow.md")]
    if proc.returncode != 0:
        for p in written:
            p.unlink(missing_ok=True)
        raise RuntimeError(f"score_week.py failed (exit {proc.returncode}):\n{proc.stderr[-1500:]}")
    if "[STALE ROSTER WARNING]" in proc.stdout and not args.allow_stale_roster:
        for p in written:
            p.unlink(missing_ok=True)
        raise Refused("score_week.py reported a STALE roster file; the ledger is append-only so a bad run can't be "
                      "undone -- run data/fetch_weekly_update.py first (or pass --allow-stale-roster)")

    prod = json.loads(pred_path.read_text(encoding="utf-8"))
    shadow_path = pred_path.with_name(pred_path.stem + "_shadow.json")
    shadow = json.loads(shadow_path.read_text(encoding="utf-8")) if shadow_path.exists() else None
    pool_src = prod["scored_pool"]
    if not pool_src:
        raise Refused("O.D.D.S. scored an empty pool -- nothing to log")
    shadow_scores = {r["player_id"]: r["score"] for r in shadow["scored_pool"]} if shadow else {}
    if shadow and set(shadow_scores) != {r["player_id"] for r in pool_src}:
        raise RuntimeError("production and shadow pools differ -- they must be identical by construction")

    allow_live = args.as_of is None and not args.skip_ownership
    if args.skip_ownership:
        status, snap_id, own = "skipped", None, {}
    else:
        status, snap_id, own = resolve_ownership(con, cfg, season, as_of, allow_live)
    thr = cfg["run"]["sleeper_owned_pct_max"]
    ids = [r["player_id"] for r in pool_src]
    lw = baselines.last_week_and_target_share(store, ids, season, week)
    snaps = baselines.snap_features(con, ids, season, week)
    pool = []
    for r in pool_src:
        pid = r["player_id"]
        pct = own.get(pid)
        pool.append({
            "run_id": None, "player_id": pid, "name": r["name"], "position": r["pos"], "team": r["team"],
            "percent_owned": pct, "in_u50": 1 if (pct is not None and pct < thr) else 0,
            "odds_prod_score": r["score"], "odds_shadow_score": shadow_scores.get(pid),
            **lw[pid], **snaps[pid]})
    n_matched = sum(1 for r in pool if r["percent_owned"] is not None)
    if status == "ok" and n_matched < 0.8 * len(pool):
        ownership._banner(f"ownership matched only {n_matched}/{len(pool)} pool players -- the sleeper pool will be thin")
    latest_snap = baselines.latest_snap_week(con, season, week)
    if latest_snap is None or latest_snap < (season, week - 1):
        print(f"[WARN] snap counts only run through {latest_snap}; the heuristic baseline is using older snap data "
              f"(run `fetch-snaps` after the release updates)")

    run_id = f"{season}w{week:02d}-{slot}-{as_of:%Y%m%dT%H%M%SZ}"
    seed = args.seed if args.seed is not None else baselines.derive_seed(season, week, slot)
    versions = {"odds_prod": (prod.get("model") or {}).get("git_commit"),
                "odds_shadow": ((shadow or {}).get("model") or {}).get("git_commit"), **BASELINE_VERSIONS}
    for r in pool:
        r["run_id"] = run_id
    preds, _sets = make_predictions(run_id, pool, cfg, seed, versions)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(REPO_ROOT)).stdout.strip() or None
    run_row = {
        "run_id": run_id, "run_timestamp": as_of.isoformat(), "season": season, "week": week, "run_slot": slot,
        "repo_git_commit": commit, "prediction_file": pred_path.name,
        "shadow_prediction_file": shadow_path.name if shadow else None, "kickoff_cutoff_utc": cutoff.isoformat(),
        "pool_size": len(pool), "n_draws": args.n_draws or cfg["run"]["n_draws"], "dart_seed": seed,
        "snap_week_used": f"{latest_snap[0]}w{latest_snap[1]:02d}" if latest_snap else None,
        "ownership_snapshot_id": snap_id, "ownership_status": status, "ownership_matched": n_matched,
        "run_config_json": json.dumps({"run": cfg["run"], "hit_config_hash": scoring.config_hash(cfg)}, sort_keys=True)}
    with con:
        ledger.insert_rows(con, "tr_runs", [run_row])
        ledger.insert_rows(con, "tr_pool", pool)
        ledger.insert_rows(con, "tr_predictions", preds)
    export = ledger.export_jsonl(Path(args.export_dir) / f"{season}_week{week:02d}_{slot}_log.jsonl",
                                 {"tr_runs": [run_row], "tr_pool": pool, "tr_predictions": preds})
    print(f"\n[LOGGED] {run_id}: pool {len(pool)} players ({sum(r['in_u50'] for r in pool)} under {thr}% owned; "
          f"ownership {status}), {len(preds)} pick rows, dart seed {seed}, {run_row['n_draws']} draws at score time")
    print(f"[SAVED] {pred_path} (+ shadow, markdown)  |  ledger export: {export}")
    for ps in sorted({p["pick_set"] for p in preds}):
        if not ps.endswith("_k10"):
            continue
        print(f"\n  {ps}:")
        for m in ("odds_prod", "odds_shadow", "heuristic", "last_week", "dart"):
            names = {r["player_id"]: r["name"] for r in pool}
            picks = [names[p["player_id"]] for p in preds if p["model_name"] == m and p["pick_set"] == ps]
            if picks:
                print(f"    {m:12} {', '.join(picks)}")
    return 0


# -------------------------------------------------------------------- score

def _metric_tuple(ids, res):
    t = np.zeros(4)
    for pid in ids:
        r = res.get(pid)
        if r is not None and r["played"]:
            t += (r["top24"], r["spike"] or 0, r["points"], 1)
    return t  # top24, spike, points, played


def score_one_week(con, cfg, store, season, week, now, force=False, export_dir=DEFAULT_EXPORT_DIR):
    runs = [dict(r) for r in con.execute("SELECT * FROM tr_runs WHERE season=? AND week=? ORDER BY run_timestamp", (season, week))]
    if not runs:
        raise Refused(f"no runs logged for {season} week {week}")
    g = con.execute("SELECT COUNT(*), SUM(CASE WHEN home_score IS NOT NULL AND away_score IS NOT NULL THEN 1 ELSE 0 END) "
                    "FROM nfl_games WHERE season=? AND week=? AND is_playoffs=0", (season, week)).fetchone()
    if not g[0] or g[0] != g[1]:
        raise Refused(f"{season} week {week} isn't complete in nfl_games ({g[1] or 0}/{g[0]} games final) -- "
                      f"score after Monday night, after data/fetch_weekly_update.py + the loader")
    sched_teams = {norm_team(t) for r in con.execute(
        "SELECT home_team, away_team FROM nfl_games WHERE season=? AND week=? AND is_playoffs=0", (season, week)) for t in r}
    rows = store.week_rows(season, week)
    missing = sched_teams - {r["team"] for r in rows.values()}
    if missing:
        raise Refused(f"stats CSV has no rows for {sorted(missing)} in {season} week {week} -- refresh with data/fetch_weekly_update.py")
    h = scoring.config_hash(cfg)
    if con.execute("SELECT 1 FROM tr_scorings WHERE season=? AND week=? AND hit_config_hash=?", (season, week, h)).fetchone() and not force:
        print(f"[SKIP] {season} week {week} already scored under hit definition {h[:10]} (use --force to append a re-score)")
        return None

    hit = cfg["hit"]
    ranks = scoring.position_ranks(rows, hit["positions"], hit["top_n_per_position"])
    pool_ids = {r[0] for r in con.execute(
        "SELECT DISTINCT player_id FROM tr_pool WHERE run_id IN (%s)" % ",".join("?" * len(runs)), [r["run_id"] for r in runs])}
    res = {}
    for pid in set(ranks) | pool_ids:
        r = rows.get(pid)
        if r is None:  # DNP / no stat row: a miss with 0 points
            res[pid] = {"played": False, "position": None, "points": 0.0, "rank": None, "top24": 0, "spike": 0,
                        "baseline": None, "threshold": None}
            continue
        pos, rank, top = ranks.get(pid, (r["position"], None, False))  # played at a non-ranked position: counts, never top-N
        res[pid] = {"played": True, "position": pos, "points": r["points"], "rank": rank, "top24": 1 if top else 0,
                    "spike": None, "baseline": None, "threshold": None}
        if pid in pool_ids:
            prior = [g_[2] for g_ in store.history_before(pid, season, week, hit["spike"]["baseline_games"])]
            sp, base, thr_ = scoring.spike_result(prior, r["points"], hit["spike"])
            res[pid].update(spike=None if sp is None else int(sp), baseline=base, threshold=thr_)

    run_results, percentiles = [], []
    for run in runs:
        preds = [dict(r) for r in con.execute("SELECT * FROM tr_predictions WHERE run_id=?", (run["run_id"],))]
        by = {}
        for p in preds:
            by.setdefault((p["model_name"], p["pick_set"]), []).append(p["player_id"])
        sets = rebuild_dart_sets(con, run["run_id"])
        draws = list(baselines.dart_draws(sets, run["dart_seed"], run["n_draws"]))
        if draws and any(set(draws[0][ps]) != set(by.get(("dart", ps), [])) for ps in sets):
            print(f"[WARN] {run['run_id']}: regenerated dart draw 0 does NOT match the logged dart picks -- the numpy "
                  f"random stream or pool snapshot changed; dart percentiles for this run are suspect", file=sys.stderr)
        dist = {ps: np.array([_metric_tuple(d[ps], res) for d in draws]) for ps in sets}
        for ps, arr in dist.items():
            m = arr.mean(axis=0)
            run_results.append({"run_id": run["run_id"], "model_name": "dart", "pick_set": ps, "n_picks": float(len(draws[0][ps])),
                                "n_played": float(m[3]), "top24_hits": float(m[0]), "spike_hits": float(m[1]),
                                "total_points": float(m[2]), "n_draws": len(draws)})
        for (model, ps), ids in sorted(by.items()):
            if model == "dart":
                continue
            t = _metric_tuple(ids, res)
            run_results.append({"run_id": run["run_id"], "model_name": model, "pick_set": ps, "n_picks": float(len(ids)),
                                "n_played": float(t[3]), "top24_hits": float(t[0]), "spike_hits": float(t[1]),
                                "total_points": float(t[2]), "n_draws": None})
            for mi, metric in enumerate(METRICS):
                col = dist[ps][:, (0, 1, 2)[mi]]
                percentiles.append({"run_id": run["run_id"], "model_name": model, "pick_set": ps, "metric": metric,
                                    "value": float(t[mi]), "dart_mean": float(col.mean()),
                                    "dart_p05": float(np.percentile(col, 5)), "dart_p95": float(np.percentile(col, 95)),
                                    "percentile": baselines.percentile_midrank(float(t[mi]), col), "n_draws": len(col)})

    scoring_row = {"season": season, "week": week, "scored_at": now.isoformat(), "hit_config_hash": h,
                   "hit_config_json": scoring.hash_input(cfg), "n_runs": len(runs), "n_players_ranked": len(ranks),
                   "stats_source": str(store.path(season))}
    with con:
        sid = con.execute(
            "INSERT INTO tr_scorings (season, week, scored_at, hit_config_hash, hit_config_json, n_runs, n_players_ranked, stats_source) "
            "VALUES (:season,:week,:scored_at,:hit_config_hash,:hit_config_json,:n_runs,:n_players_ranked,:stats_source)",
            scoring_row).lastrowid
        player_rows = [{"scoring_id": sid, "player_id": pid, "position": r["position"] or "?", "points": r["points"],
                        "pos_rank": r["rank"], "top24": r["top24"], "spike": r["spike"], "trailing_baseline": r["baseline"],
                        "spike_threshold": r["threshold"]} for pid, r in res.items() if r["played"]]
        for r in run_results:
            r["scoring_id"] = sid
        for r in percentiles:
            r["scoring_id"] = sid
        ledger.insert_rows(con, "tr_player_results", player_rows)
        ledger.insert_rows(con, "tr_run_results", run_results)
        ledger.insert_rows(con, "tr_percentiles", percentiles)
    scoring_row["scoring_id"] = sid
    ledger.export_jsonl(Path(export_dir) / f"{season}_week{week:02d}_scoring_{sid}.jsonl",
                        {"tr_scorings": [scoring_row], "tr_run_results": run_results, "tr_percentiles": percentiles,
                         "tr_player_results": player_rows})
    return {"scoring_id": sid, "n_runs": len(runs), "run_results": run_results, "percentiles": percentiles}


def compute_crowd(con, cfg, now, export_dir=DEFAULT_EXPORT_DIR):
    """Append crowd-hit rows for every run whose window has closed and isn't done.
    A crowd hit: the pick began under the threshold and any ESPN snapshot within
    the window shows it OVER the threshold. Picks already over it can't 'rise'
    and are recorded as not applicable."""
    window = timedelta(days=cfg["crowd_hit"]["window_days"])
    thr = cfg["crowd_hit"]["owned_pct_threshold"]
    new_rows = []
    for run in con.execute("SELECT run_id, run_timestamp FROM tr_runs").fetchall():
        start = datetime.fromisoformat(run["run_timestamp"])
        end = start + window
        if end > now or con.execute("SELECT 1 FROM tr_crowd_results WHERE run_id=? LIMIT 1", (run["run_id"],)).fetchone():
            continue
        snap_ids = [r[0] for r in con.execute(
            "SELECT snapshot_id FROM tr_ownership_snapshots WHERE source='espn' AND ok=1 AND taken_at > ? AND taken_at <= ?",
            (start.isoformat(), end.isoformat()))]
        mx = {}
        if snap_ids:
            q = ("SELECT player_id, MAX(percent_owned), COUNT(DISTINCT snapshot_id) FROM tr_ownership WHERE snapshot_id IN (%s) "
                 "AND player_id IS NOT NULL GROUP BY player_id" % ",".join("?" * len(snap_ids)))
            mx = {r[0]: (r[1], r[2]) for r in con.execute(q, snap_ids)}
        owned = {r[0]: r[1] for r in con.execute("SELECT player_id, percent_owned FROM tr_pool WHERE run_id=?", (run["run_id"],))}
        for p in con.execute("SELECT model_name, pick_set, player_id FROM tr_predictions WHERE run_id=?", (run["run_id"],)):
            o = owned.get(p["player_id"])
            row = {"run_id": run["run_id"], "model_name": p["model_name"], "pick_set": p["pick_set"],
                   "player_id": p["player_id"], "owned_at_pick": o, "max_owned_in_window": None,
                   "snapshots_in_window": 0, "crowd_hit": None, "note": None, "computed_at": now.isoformat()}
            if o is None:
                row["note"] = "no ownership recorded at pick time"
            elif o >= thr:
                row["note"] = f"already >= {thr}% owned at pick time (cannot rise)"
            else:
                m, n = mx.get(p["player_id"], (None, 0))
                row.update(max_owned_in_window=m, snapshots_in_window=n)
                if n == 0:
                    row["note"] = "no ESPN snapshots in the window"
                else:
                    row["crowd_hit"] = 1 if m > thr else 0
            new_rows.append(row)
    if new_rows:
        with con:
            ledger.insert_rows(con, "tr_crowd_results", new_rows)
        ledger.export_jsonl(Path(export_dir) / f"crowd_{now:%Y%m%dT%H%M%SZ}.jsonl", {"tr_crowd_results": new_rows})
    return len(new_rows)


@with_db
def cmd_score(args, con):
    cfg = scoring.load_config(args.config)
    now = utcnow()
    if not args.crowd_only:
        if args.week is None:
            raise Refused("--week is required (unless --crowd-only)")
        store = scoring.StatsStore(args.raw_dir, cfg["scoring"])
        out = score_one_week(con, cfg, store, args.season, args.week, now, force=args.force, export_dir=args.export_dir)
        if out:
            print(f"[SCORED] {args.season} week {args.week} -> scoring_id {out['scoring_id']} ({out['n_runs']} run(s), "
                  f"hit definition {scoring.config_hash(cfg)[:10]})")
            for r in out["run_results"]:
                if r["pick_set"] in ("all_k10", "u50_k10"):
                    print(f"  {r['run_id']:34} {r['pick_set']:8} {r['model_name']:12} top-24 {r['top24_hits']:.1f}/{r['n_picks']:.0f}  "
                          f"spike {r['spike_hits']:.1f}  pts {r['total_points']:.1f}")
    n = compute_crowd(con, cfg, now, export_dir=args.export_dir)
    print(f"[CROWD] appended {n} crowd-hit row(s) for runs whose {cfg['crowd_hit']['window_days']}-day window has closed")
    return 0


# ------------------------------------------------------------ report etc.

@with_db
def cmd_report(args, con):
    cfg = scoring.load_config(args.config)
    if args.week is not None:
        md = report.build_weekly_report(con, args.season, args.week)
        name = f"{args.season}_week{args.week:02d}_scoreboard.md"
    else:
        md = report.build_season_report(con, args.season, cfg["report"])
        name = f"{args.season}_season_summary.md"
    if md is None:
        raise Refused(f"nothing scored yet for {args.season}" + (f" week {args.week}" if args.week else "")
                      + " -- run `score` first")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / name).write_text(md, encoding="utf-8")
    print(md)
    print(f"\n[SAVED] {out_dir / name}", file=sys.stderr)
    return 0


@with_db
def cmd_snapshot(args, con):
    s = ownership.take_snapshot(con, args.season or default_season())
    print(f"[OWNERSHIP] ESPN {'ok' if s['espn_ok'] else 'FAILED'}: {s['espn_rows']} players, {s['espn_matched']} matched to gsis_id "
          f"(snapshot {s['espn_snapshot_id']}); Sleeper trending {'ok' if s['sleeper_ok'] else 'FAILED'}")
    return 0 if s["espn_ok"] else 1


@with_db
def cmd_fetch_snaps(args, con):
    snapcounts.fetch_and_load(con, parse_seasons(args.seasons), raw_dir=args.raw_dir, do_fetch=not args.no_fetch)
    return 0


def cmd_backtest(args):
    print("backtest is phase 2 and not built yet. It needs (1) a walk-forward retraining wrapper with a season cutoff and "
          "(2) an as-of feature mode that recomputes week-W features with week W's data removed -- both touch the model "
          "code, so the diffs get reviewed first.", file=sys.stderr)
    return 2


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--db", default=str(DEFAULT_DB))
        p.add_argument("--config", default=None, help="hit config JSON (default tracker/hit_config.json)")
        p.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
        p.add_argument("--export-dir", default=str(DEFAULT_EXPORT_DIR))

    p = sub.add_parser("log", help="run O.D.D.S. + baselines and write to the ledger (before kickoff)")
    common(p)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--week", type=int, required=True)
    p.add_argument("--slot", choices=("thu", "sun"), required=True)
    p.add_argument("--predictions-dir", default=str(DEFAULT_PRED_DIR))
    p.add_argument("--as-of", default=None, help="ISO8601 instant to treat as 'now' (testing only; default now)")
    p.add_argument("--n-draws", type=int, default=None)
    p.add_argument("--seed", type=int, default=None, help="dart seed (default: derived from season/week/slot)")
    p.add_argument("--skip-ownership", action="store_true")
    p.add_argument("--allow-stale-roster", action="store_true")
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("score", help="score every run of a completed week")
    common(p)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--week", type=int, default=None)
    p.add_argument("--force", action="store_true", help="append a re-score even under an unchanged hit definition")
    p.add_argument("--crowd-only", action="store_true")
    p.set_defaults(fn=cmd_score)

    p = sub.add_parser("report", help="scoreboard (--week) or season summary")
    common(p)
    p.add_argument("--season", type=int, required=True)
    p.add_argument("--week", type=int, default=None)
    p.add_argument("--out-dir", default=str(DEFAULT_REPORT_DIR))
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("snapshot-ownership", help="collect roster-%% (ESPN primary, Sleeper trending secondary)")
    common(p)
    p.add_argument("--season", type=int, default=None)
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("fetch-snaps", help="download nflverse snap counts and load them")
    common(p)
    p.add_argument("--seasons", default=None, help="e.g. 2024-2026 or 2025,2026 (default: last 3 seasons)")
    p.add_argument("--no-fetch", action="store_true", help="load files already on disk")
    p.set_defaults(fn=cmd_fetch_snaps)

    p = sub.add_parser("backtest", help="phase 2 (not built yet)")
    common(p)
    p.add_argument("--seasons", default=None)
    p.set_defaults(fn=cmd_backtest)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "fetch-snaps" and not args.seasons:
        s = default_season()
        args.seasons = f"{s - 2}-{s}"
    try:
        return args.fn(args)
    except Refused as e:
        print(f"[REFUSED] {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
