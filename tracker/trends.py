#!/usr/bin/env python3
"""Breakout-trend observations: does O.D.D.S. find players on a breakout
TREND, not just single-week spikes? Measurement only -- never part of the
official spike/top-24 record, and no model changes.

PRE-REGISTERED on 2026-10-20 before any 2026 number was computed
(docs/tracker_handoff.md, decision 15); the definitions live in
hit_config.json's `trend` section, with their own hash (NOT part of the hash
that defines the official record). A change is a new version, and earlier
results stay on record.

WHAT IS STORED. For each week's official run (the first snapshot: thu if the
week has one, else sun) and each metric whose window has closed, one
append-only tr_trend_outcomes row per POOL player: eligible / hit / value.
Any pick set can then be judged from stored data alone -- a model's picks
from tr_predictions, or each of the 1000 dart draws rebuilt exactly as
`score` rebuilds them -- and the season aggregate can de-duplicate players
picked in consecutive weeks.

METRICS (all league scoring = the tracker's StatsStore; "team games" = the
weeks his team, per week, had a game; byes skipped)
  payoff          weeks W..W+3, W <= 14. B = mean of his last 3 games before W
                  (fewer: not eligible), P = mean points over the team games
                  (a team game he didn't play = 0). hit: P >= 1.5 B and
                  P >= 10; value: P - B.
  snap_growth     W <= 15. mean offense snap share over the team games of
                  W..W+2 minus W-3..W-1 (no snap row in a team game = 0), each
                  window >= 2 team games. hit: >= that position's cut-off (the
                  90th percentile over 2022-2025, from `trend-cutoffs`, fixed
                  before any 2026 number). value: the difference.
  target_growth   the same with nflverse's weekly target_share, RB/WR/TE.
  crowd           under 50% ESPN-owned at pick time; hit: any ESPN snapshot in
                  (log time, +14 days] over 50%.
  league_add      available in the user's league at log time (the run's
                  league-availability snapshot); hit: added by ANY team in
                  (log time, +14 days] (waivers or free agency). Aggregate
                  counts only -- never a team or manager name.
"""
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np

import baselines
import ledger

METRICS = ("payoff", "snap_growth", "target_growth", "crowd", "league_add")
# Payoff and role growth are evaluated for picks from week 4 on (decision 15: "picks in weeks 4-14"); role growth's
# before-window, W-3..W-1, only exists from week 4. A code constant, not a hit_config key, so the trend hash -- and with it
# the pre-registered definitions -- stays the same (added 2026-10-22, when no outcome was stored and no run predated week 4).
FIRST_PICK_WEEK = 4
# Outcomes kept in the local DB only, never in the committed JSONL export: a league-add row says which NFL players were
# available in the user's league and whether someone in it added them -- the league's own activity (user decision
# 2026-10-22). The season summary shows them as counts and rates only.
LOCAL_ONLY_METRICS = ("league_add",)
POSITIONS = ("QB", "RB", "WR", "TE")
TARGET_POSITIONS = ("RB", "WR", "TE")

DDL = """
CREATE TABLE IF NOT EXISTS tr_trend_outcomes (
  run_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  player_id TEXT NOT NULL,
  position TEXT,
  eligible INTEGER NOT NULL,
  hit INTEGER,
  value REAL,
  detail TEXT,
  trend_hash TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (run_id, metric, player_id, trend_hash)
);
"""


def init_schema(con):
    con.executescript(DDL)
    ledger.create_append_only_triggers(con, "tr_trend_outcomes")
    con.commit()


def trend_hash(cfg):
    return hashlib.sha256(json.dumps(cfg["trend"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ schedule, teams, weekly values

def team_games(con, season):
    """{(team, week)} with a regular-season game, current team codes."""
    from team_crosswalk import norm_team
    return {(norm_team(t), r[0]) for r in con.execute(
        "SELECT week, home_team, away_team FROM nfl_games WHERE season=? AND is_playoffs=0", (season,)) for t in r[1:]}


def final_weeks(con, season):
    """Weeks whose every game has a score."""
    out = set()
    for w, n, f in con.execute("SELECT week, COUNT(*), SUM(home_score IS NOT NULL AND away_score IS NOT NULL) FROM nfl_games "
                               "WHERE season=? AND is_playoffs=0 GROUP BY week", (season,)):
        if n and n == f:
            out.add(w)
    return out


class Season:
    """One season's per-week team, snap share and stats rows for every player, plus who had a game when."""

    def __init__(self, con, store, season, weekly_roster_rows=None):
        from team_crosswalk import norm_team
        self.season, self.games = season, team_games(con, season)
        self.team = defaultdict(dict)                     # player -> {week: team}
        self.snap = defaultdict(dict)                     # player -> {week: offense share 0-1}
        self.snap_pos = {}
        for pid, w, team, pct, pos in con.execute(
                "SELECT player_id, week, team, offense_pct, position FROM tr_snap_counts WHERE season=? AND player_id IS NOT NULL", (season,)):
            self.snap[pid][w] = pct or 0.0
            self.team[pid].setdefault(w, norm_team(team))
            self.snap_pos[pid] = pos
        self.stats = store.season_data(season) or {}
        for w, rows in self.stats.items():
            for pid, r in rows.items():
                if r.get("team"):
                    self.team[pid].setdefault(w, norm_team(r["team"]))
        for pid, w, team in weekly_roster_rows or ():
            self.team[pid][w] = norm_team(team)           # the week's roster wins when we have it

    def team_of(self, pid, week):
        weeks = self.team.get(pid)
        if not weeks:
            return None
        if week in weeks:
            return weeks[week]
        earlier = [w for w in weeks if w < week]
        return weeks[max(earlier)] if earlier else weeks[min(weeks)]

    def played(self, pid, week):
        t = self.team_of(pid, week)
        return t is not None and (t, week) in self.games

    def window(self, pid, weeks, value):
        """[value(week)] over the team games among `weeks` (value None -> 0)."""
        return [value(w) or 0.0 for w in weeks if self.played(pid, w)]

    def snap_share(self, pid, w):
        return self.snap.get(pid, {}).get(w)

    def target_share(self, pid, w):
        return (self.stats.get(w, {}).get(pid) or {}).get("target_share")

    def points(self, pid, w):
        return (self.stats.get(w, {}).get(pid) or {}).get("points")


def role_delta(season_data, pid, week, kind, cfg):
    """(delta, n_before, n_after) or None when a window has too few team games."""
    t = cfg["trend"]["role"]
    value = season_data.snap_share if kind == "snap" else season_data.target_share
    before = season_data.window(pid, range(week - t["before_weeks"], week), lambda w: value(pid, w))
    after = season_data.window(pid, range(week, week + t["after_weeks"]), lambda w: value(pid, w))
    if len(before) < t["min_games"] or len(after) < t["min_games"]:
        return None
    return sum(after) / len(after) - sum(before) / len(before), len(before), len(after)


def cutoffs_from_history(con, store, cfg, seasons=None):
    """{"snap": {pos: p}, "target": {pos: p}, "n": {...}}: the cfg percentile of role_delta over every player-week of the
    history seasons, by position, with exactly the pick rules (W from before_weeks+1 to last_pick_week)."""
    t = cfg["trend"]["role"]
    seasons = seasons or t["history_seasons"]
    vals = {"snap": defaultdict(list), "target": defaultdict(list)}
    for s in seasons:
        sd = Season(con, store, s)
        pos_of = dict(sd.snap_pos)
        for w, rows in sd.stats.items():
            for pid, r in rows.items():
                pos_of.setdefault(pid, r.get("position"))
        for pid, pos in pos_of.items():
            if pos not in POSITIONS:
                continue
            for week in range(t["before_weeks"] + 1, t["last_pick_week"] + 1):
                d = role_delta(sd, pid, week, "snap", cfg)
                if d is not None:
                    vals["snap"][pos].append(d[0])
                if pos in TARGET_POSITIONS:
                    d = role_delta(sd, pid, week, "target", cfg)
                    if d is not None:
                        vals["target"][pos].append(d[0])
    q = t["percentile"]
    return {kind: {pos: round(float(np.percentile(v, q)), 4) for pos, v in sorted(by.items())} for kind, by in vals.items()} | {
        "n": {kind: {pos: len(v) for pos, v in sorted(by.items())} for kind, by in vals.items()}}


# ------------------------------------------------------------------ per-run outcomes

def official_runs(con, season=None):
    """One run per (season, week): thu if it exists, else sun -- the season summary's record."""
    q = "SELECT * FROM tr_runs" + (" WHERE season=?" if season else "") + " ORDER BY season, week, CASE run_slot WHEN 'thu' THEN 0 ELSE 1 END"
    seen, out = set(), []
    for r in con.execute(q, (season,) if season else ()):
        if (r["season"], r["week"]) not in seen:
            seen.add((r["season"], r["week"]))
            out.append(r)
    return out


def resolvable(con, store, run, metric, cfg, now, finals, snap_weeks):
    """(ready, why not)."""
    t, w, s = cfg["trend"], run["week"], run["season"]
    start = datetime.fromisoformat(run["run_timestamp"])
    if metric in ("payoff", "snap_growth", "target_growth") and w < FIRST_PICK_WEEK:
        return False, f"picks before week {FIRST_PICK_WEEK} are not evaluated on {'payoff' if metric == 'payoff' else 'role growth'}"
    if metric == "payoff":
        last = w + t["payoff"]["weeks"] - 1
        if w > t["payoff"]["last_pick_week"]:
            return False, f"picks after week {t['payoff']['last_pick_week']} are not evaluated on payoff"
        return (all(x in finals for x in range(w, last + 1)) and store.max_week(s) >= last), f"needs week {last} final and loaded"
    if metric in ("snap_growth", "target_growth"):
        last = w + t["role"]["after_weeks"] - 1
        if w > t["role"]["last_pick_week"]:
            return False, f"picks after week {t['role']['last_pick_week']} are not evaluated on role growth"
        data_ok = (max(snap_weeks.get(s, [0])) >= last) if metric == "snap_growth" else (store.max_week(s) >= last)
        return (all(x in finals for x in range(w, last + 1)) and data_ok), f"needs week {last} final and loaded"
    if metric == "league_add" and not con.execute("SELECT 1 FROM tr_league_snapshots WHERE run_id=? AND ok=1",
                                                  (run["run_id"],)).fetchone():
        return False, "not evaluable: no league-availability snapshot with this run"
    days = t["crowd" if metric == "crowd" else "league_add"]["window_days"]
    end = start + timedelta(days=days)
    if end > now:
        return False, f"window closes {end:%m-%d %H:%M} UTC"
    if metric == "league_add":
        try:
            tx = con.execute("SELECT MAX(taken_at) FROM lg_snapshots WHERE resource='transactions' AND ok=1").fetchone()
        except Exception:  # noqa: BLE001 -- no league data layer in this DB
            return False, "no league data in this database"
        if not tx or not tx[0] or datetime.fromisoformat(tx[0]) < end:
            return False, "no transactions snapshot taken after the window closed"
    return True, None


def run_outcomes(con, store, run, metric, cfg, season_data):
    """[row] for every pool player of the run."""
    t, w = cfg["trend"], run["week"]
    pool = [dict(r) for r in con.execute("SELECT player_id, position, percent_owned FROM tr_pool WHERE run_id=?", (run["run_id"],))]
    out = []

    def row(p, eligible, hit=None, value=None, **detail):
        out.append({"player_id": p["player_id"], "position": p["position"], "eligible": int(eligible),
                    "hit": None if hit is None else int(hit), "value": None if value is None else round(float(value), 4),
                    "detail": json.dumps(detail, sort_keys=True) if detail else None})

    if metric == "payoff":
        pc = t["payoff"]
        for p in pool:
            prior = store.history_before(p["player_id"], run["season"], w, pc["baseline_games"])
            if len(prior) < pc["min_prior_games"]:
                row(p, False, note="fewer than 3 prior games")
                continue
            base = sum(g[2] for g in prior) / len(prior)
            pts = season_data.window(p["player_id"], range(w, w + pc["weeks"]), lambda wk: season_data.points(p["player_id"], wk))
            if not pts:
                row(p, False, note="no team game in the window")
                continue
            pay = sum(pts) / len(pts)
            row(p, True, pay >= pc["multiplier"] * base and pay >= pc["min_points"], pay - base,
                baseline=round(base, 2), payoff=round(pay, 2), games=len(pts))
    elif metric in ("snap_growth", "target_growth"):
        kind = "snap" if metric == "snap_growth" else "target"
        cut = t["role"][f"{kind}_cutoff"]
        for p in pool:
            if kind == "target" and p["position"] not in TARGET_POSITIONS:
                row(p, False, note="target share is for RB/WR/TE")
                continue
            d = role_delta(season_data, p["player_id"], w, kind, cfg)
            if d is None:
                row(p, False, note="fewer than 2 team games in a window")
                continue
            row(p, True, d[0] >= cut[p["position"]], d[0], before_games=d[1], after_games=d[2], cutoff=cut[p["position"]])
    elif metric == "crowd":
        c = t["crowd"]
        start = datetime.fromisoformat(run["run_timestamp"])
        ids = [r[0] for r in con.execute("SELECT snapshot_id FROM tr_ownership_snapshots WHERE source='espn' AND ok=1 AND taken_at > ? "
                                         "AND taken_at <= ?", (start.isoformat(), (start + timedelta(days=c["window_days"])).isoformat()))]
        mx = {}
        if ids:
            mx = {r[0]: r[1] for r in con.execute("SELECT player_id, MAX(percent_owned) FROM tr_ownership WHERE snapshot_id IN (%s) AND "
                                                  "player_id IS NOT NULL GROUP BY player_id" % ",".join("?" * len(ids)), ids)}
        for p in pool:
            o = p["percent_owned"]
            if o is None or o >= c["owned_pct_threshold"] or not ids:
                row(p, False, note="ownership unknown" if o is None else "already over the threshold" if ids else "no ESPN snapshot in window")
                continue
            m = mx.get(p["player_id"])
            row(p, True, m is not None and m > c["owned_pct_threshold"], m, owned_at_pick=o)
    elif metric == "league_add":
        snap = con.execute("SELECT snapshot_id FROM tr_league_snapshots WHERE run_id=? AND ok=1", (run["run_id"],)).fetchone()
        if snap is None:
            for p in pool:
                row(p, False, note="no league-availability snapshot with this run")
            return out
        available = {r[0] for r in con.execute("SELECT player_id FROM tr_league_availability WHERE snapshot_id=? AND player_id IS NOT NULL",
                                               (snap[0],))}
        start = datetime.fromisoformat(run["run_timestamp"])
        end = start + timedelta(days=t["league_add"]["window_days"])
        tx = con.execute("SELECT snapshot_id FROM lg_snapshots WHERE resource='transactions' AND ok=1 ORDER BY taken_at DESC LIMIT 1").fetchone()
        added = set()
        for pid, when in con.execute(
                "SELECT p.player_id, t.happened_at FROM lg_transactions t JOIN lg_transaction_players p ON p.snapshot_id=t.snapshot_id AND "
                "p.transaction_id=t.transaction_id WHERE t.snapshot_id=? AND p.action='add' AND p.source_type IN ('waivers','freeagents') "
                "AND p.player_id IS NOT NULL", (tx[0],)):
            if start < datetime.fromisoformat(when) <= end:
                added.add(pid)
        for p in pool:
            if p["player_id"] not in available:
                row(p, False, note="not available in the league at log time")
                continue
            row(p, True, p["player_id"] in added)
    return out


def compute_trends(con, cfg, store, now, weekly_roster_rows=None, export_path=None):
    """Append outcomes for every official run x metric whose window has closed and isn't stored under this trend hash.
    -> {(run_id, metric): n rows}. The JSONL export (committed) leaves out LOCAL_ONLY_METRICS; the DB keeps everything."""
    init_schema(con)
    th = trend_hash(cfg)
    role = cfg["trend"]["role"]
    if not role.get("snap_cutoff") or not role.get("target_cutoff"):
        raise ValueError("hit_config.json's trend.role cut-offs are not set -- run `tracker.py trend-cutoffs` first")
    done = {(r[0], r[1]) for r in con.execute("SELECT DISTINCT run_id, metric FROM tr_trend_outcomes WHERE trend_hash=?", (th,))}
    snap_weeks = defaultdict(list)
    for s, w in con.execute("SELECT DISTINCT season, week FROM tr_snap_counts"):
        snap_weeks[s].append(w)
    seasons, written, new_rows = {}, {}, []
    for run in official_runs(con):
        finals = final_weeks(con, run["season"])
        for metric in METRICS:
            if (run["run_id"], metric) in done:
                continue
            ok, _why = resolvable(con, store, run, metric, cfg, now, finals, snap_weeks)
            if not ok:
                continue
            if run["season"] not in seasons:
                rows = [r for r in (weekly_roster_rows or ()) if r[3] == run["season"]]
                seasons[run["season"]] = Season(con, store, run["season"], [(a, b, c) for a, b, c, _s in rows])
            rows = run_outcomes(con, store, run, metric, cfg, seasons[run["season"]])
            for r in rows:
                r.update(run_id=run["run_id"], metric=metric, trend_hash=th, computed_at=now.isoformat())
            new_rows += rows
            written[(run["run_id"], metric)] = len(rows)
    if new_rows:
        with con:
            ledger.insert_rows(con, "tr_trend_outcomes", new_rows)
        exported = [r for r in new_rows if r["metric"] not in LOCAL_ONLY_METRICS]
        if export_path is not None and exported:
            ledger.export_jsonl(export_path, {"tr_trend_outcomes": exported})
    return written


def pending(con, store, cfg, now):
    """[(week, slot, metric, why)] for the official runs' metrics not resolved yet."""
    th = trend_hash(cfg)
    try:
        done = {(r[0], r[1]) for r in con.execute("SELECT DISTINCT run_id, metric FROM tr_trend_outcomes WHERE trend_hash=?", (th,))}
    except Exception:  # noqa: BLE001 -- the table doesn't exist yet: nothing is done
        done = set()
    snap_weeks = defaultdict(list)
    for s, w in con.execute("SELECT DISTINCT season, week FROM tr_snap_counts"):
        snap_weeks[s].append(w)
    out = []
    for run in official_runs(con):
        finals = final_weeks(con, run["season"])
        for metric in METRICS:
            if (run["run_id"], metric) not in done:
                ok, why = resolvable(con, store, run, metric, cfg, now, finals, snap_weeks)
                out.append((run["week"], run["run_slot"], metric, why if not ok else "ready (computed by the next `score`)"))
    return out


# ------------------------------------------------------------------ aggregation (report)

def outcomes(con, run_id, metric, th):
    return {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT player_id, eligible, hit, value FROM tr_trend_outcomes WHERE run_id=? AND metric=? AND trend_hash=?", (run_id, metric, th))}


def _score(picks, oc):
    """(n_eval, hits, value_sum) over the picks that are eligible."""
    n = h = 0
    v = 0.0
    for pid in picks:
        e, hit, val = oc.get(pid, (0, None, None))
        if e:
            n += 1
            h += hit or 0
            v += val or 0.0
    return n, h, v


def season_table(con, season, metric, cfg, dedupe=True):
    """{(model, pick_set): dict(weeks, n, hits, value, dart_mean_rate, percentile, per_week)} over the official runs with this
    metric resolved. Consecutive-week repeats of a player by the same model and pick set count once (dart: per draw index)."""
    th = trend_hash(cfg)
    runs = [r for r in official_runs(con, season)
            if con.execute("SELECT 1 FROM tr_trend_outcomes WHERE run_id=? AND metric=? AND trend_hash=? LIMIT 1", (r["run_id"], metric, th)).fetchone()]
    agg = defaultdict(lambda: {"weeks": [], "n": 0, "hits": 0, "value": 0.0, "per_week": []})
    dart = defaultdict(lambda: None)                         # pick_set -> arrays over draws (n, hits, value)
    prev_model, prev_dart, prev_week = {}, {}, None
    for run in runs:
        oc = outcomes(con, run["run_id"], metric, th)
        consecutive = prev_week is not None and run["week"] == prev_week + 1
        picks = defaultdict(list)
        for r in con.execute("SELECT model_name, pick_set, player_id FROM tr_predictions WHERE run_id=? AND model_name != 'dart' ORDER BY rank",
                             (run["run_id"],)):
            picks[(r["model_name"], r["pick_set"])].append(r["player_id"])
        cur_model = {}
        for key, ids in picks.items():
            counted = [p for p in ids if not (dedupe and consecutive and p in prev_model.get(key, set()))]
            n, h, v = _score(counted, oc)
            a = agg[key]
            a["weeks"].append(run["week"])
            a["n"] += n
            a["hits"] += h
            a["value"] += v
            a["per_week"].append((run["week"], n, h, v))
            cur_model[key] = set(ids)
        draws = list(baselines.dart_draws(_dart_sets(con, run["run_id"]), run["dart_seed"], run["n_draws"]))
        cur_dart = {}
        for ps in {k[1] for k in picks}:
            arr = np.zeros((len(draws), 3))
            for i, d in enumerate(draws):
                ids = d.get(ps, [])
                prev = prev_dart.get((ps, i), set()) if (dedupe and consecutive) else set()
                arr[i] = _score([p for p in ids if p not in prev], oc)
                cur_dart[(ps, i)] = set(ids)
            dart[ps] = arr if dart[ps] is None else dart[ps] + arr
        prev_model, prev_dart, prev_week = cur_model, cur_dart, run["week"]
    out = {}
    for (model, ps), a in agg.items():
        d = dart.get(ps)
        rate = a["hits"] / a["n"] if a["n"] else None
        dist = (d[:, 1] / np.maximum(d[:, 0], 1)) if d is not None else None
        out[(model, ps)] = dict(a, rate=rate, mean_value=(a["value"] / a["n"] if a["n"] else None),
                                dart_mean_rate=float(dist.mean()) if dist is not None else None,
                                dart_mean_value=float((d[:, 2] / np.maximum(d[:, 0], 1)).mean()) if d is not None else None,
                                percentile=baselines.percentile_midrank(rate, dist) if (rate is not None and dist is not None) else None)
    return out, [r["week"] for r in runs]


def _dart_sets(con, run_id):
    return baselines.rebuild_dart_sets(con, run_id)   # the one rebuild of each run's dart pools and position mixes


def weekly_roster_rows(raw_dir, seasons):
    """[(gsis_id, week, team, season)] from nflverse_raw/roster_weekly_<season>.csv, where present."""
    import csv
    from pathlib import Path
    out = []
    for s in seasons:
        path = Path(raw_dir) / f"roster_weekly_{s}.csv"
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("season") == str(s) and r.get("gsis_id") and r.get("team") and (r.get("week") or "").isdigit():
                    out.append((r["gsis_id"], int(r["week"]), r["team"], s))
    return out


def moving_block_ci(per_week, block, resamples=2000, seed=11, level=0.95):
    """A moving-block bootstrap interval for hits / n over weeks: blocks of `block` consecutive weeks resampled with
    replacement. per_week: [(week, n, hits, value)] in week order. None when there are fewer weeks than one block."""
    n_w = len(per_week)
    if n_w < block:
        return None
    rng = np.random.default_rng(seed)
    starts = n_w - block + 1
    k = int(np.ceil(n_w / block))
    ns, hs = np.array([x[1] for x in per_week], float), np.array([x[2] for x in per_week], float)
    rates = []
    for _ in range(resamples):
        idx = np.concatenate([np.arange(s, s + block) for s in rng.integers(0, starts, k)])[:n_w]
        tot = ns[idx].sum()
        if tot:
            rates.append(hs[idx].sum() / tot)
    lo, hi = np.percentile(rates, [100 * (1 - level) / 2, 100 * (1 + level) / 2])
    return float(lo), float(hi)
