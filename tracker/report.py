#!/usr/bin/env python3
"""Newsletter-ready Markdown scoreboards and the season-to-date summary.

Weekly scoreboard: O.D.D.S. (production + shadow) vs dart vs heuristic vs
last_week, for the headline top-10 and the top-25, once for ALL eligible
players and once, as its own full section, for the sleeper pool (players
under 50% owned at pick time). Season summary: cumulative hit rates, mean
dart percentile, and bootstrap 95% confidence intervals -- resampling WEEKS
(not picks; picks within a week share one environment), using one run per
week (the first snapshot: thu if it exists, else sun), like verify_week.py.
"""
import json
from datetime import datetime

import numpy as np

# A bootstrap over fewer weeks than this is a zero-width or meaningless interval; show the point estimate only.
MIN_WEEKS_FOR_CI = 3

MODEL_ORDER = ["odds_prod", "odds_shadow", "dart", "heuristic", "last_week"]
MODEL_LABELS = {
    "odds_prod": "O.D.D.S. (13-feature)",
    "odds_shadow": "O.D.D.S. shadow (10-feature)",
    "dart": "Dart (avg of N random draws)",
    "heuristic": "Snap-share heuristic",
    "last_week": "Last week's points",
}
SCOPE_TITLES = {
    "all": "All eligible players",
    "u50": "Sleeper pool -- under 50% owned at pick time",
}
KNOWN_CAVEATS = [
    "**Known leak (flagged, not yet fixed):** the production model's `starter_absent_proxy` feature is built from "
    "whether the presumed starter played in the very week being predicted for training rows, but is empty at live "
    "scoring time. It's a small feature (~1% of importance) but it breaks the 'strictly before the week' rule and "
    "creates train/serve skew. A retrain with it computed strictly as-of (or removed) is queued; the tracker will log "
    "the fixed model as a new model_version so old vs fixed can be compared.",
    "**Scoring mismatch (flagged, not yet fixed):** this tracker scores every hit under the league's real settings "
    "(5-pt passing TDs). O.D.D.S.'s own training labels were built with a 4-pt passing TD, which mainly affects how QB "
    "'spikes' were defined. To be corrected in the same retrain.",
]


def pick_set_name(scope, k):
    return f"{scope}_k{k}"


def pool_rule_notes(runs):
    """Footnotes for runs whose pool was not built under the current pool rule
    (Out/Doubtful players excluded per the official injury report)."""
    notes = []
    for run in runs:
        cfg = json.loads(run["run_config_json"] or "{}")
        label = f"week {run['week']} `{run['run_slot']}` run"
        source = (cfg.get("injury") or {}).get("source")
        if "pool_rule" not in cfg:
            notes.append(f"**Pool rule:** the {label} was logged before the Out/Doubtful pool rule existed. Players already "
                         f"ruled Out or Doubtful were still in its pool and could be picked -- by any model and by the "
                         f"dart draws -- so some of its picks could not score. Its numbers are left as logged.")
        elif source == "espn-fallback":
            notes.append(f"**Pool rule:** for the {label} the official injury report was unavailable, so Out/Doubtful "
                         f"players were excluded using ESPN's injury status instead.")
        elif source in ("unavailable", "skipped"):
            notes.append(f"**Pool rule:** for the {label} no injury designations were available, so the Out/Doubtful "
                         f"exclusion was NOT applied; players already ruled out may have been in its pool.")
    return notes


def sleeper_pool_lines(con, runs, only_if_unknown=False):
    """One line per run saying how big its under-50% pool was and how many of
    those players had UNKNOWN ownership (no row in the roster-% snapshot the
    run used). From `sleeper_pool_rule` v2 on they are kept in the pool and
    counted here; the one earlier run dropped them, which is said instead."""
    lines = []
    for run in runs:
        if run["ownership_snapshot_id"] is None:
            continue
        n_pool, n_in, n_out = con.execute(
            "SELECT COALESCE(SUM(in_u50), 0), COALESCE(SUM(CASE WHEN in_u50=1 AND percent_owned IS NULL THEN 1 ELSE 0 END), 0), "
            "COALESCE(SUM(CASE WHEN in_u50=0 AND percent_owned IS NULL THEN 1 ELSE 0 END), 0) FROM tr_pool WHERE run_id=?",
            (run["run_id"],)).fetchone()
        label = f"week {run['week']} `{run['run_slot']}` run"
        if "sleeper_pool_rule" in json.loads(run["run_config_json"] or "{}"):
            if only_if_unknown and not n_in:
                continue
            lines.append(f"**Under-50% pool, {label}:** {n_pool} players, {n_in} of them with **ownership unknown** (no "
                         f"roster-% row for them in the snapshot used). Unknown is not treated as under 50% or as 0%: those "
                         f"players are kept in the pool and counted here rather than dropped.")
        else:
            if only_if_unknown and not n_out:
                continue
            lines.append(f"**Under-50% pool, {label}:** {n_pool} players. {n_out} pool player(s) with unknown ownership were "
                         f"left OUT of it -- the rule for this run only; later runs keep such players in, flagged.")
    return lines


STALE_NOTE_HOURS_DEFAULT = 12


def ownership_notes(con, runs):
    """Footnotes about the roster-% snapshot behind a run's under-50% pool: one
    that was not taken the same morning (older than the run's own
    ownership_stale_note_hours at log time)."""
    notes = []
    for run in runs:
        if run["ownership_snapshot_id"] is None:
            continue
        snap = con.execute("SELECT taken_at FROM tr_ownership_snapshots WHERE snapshot_id=?", (run["ownership_snapshot_id"],)).fetchone()
        if snap is None:
            continue
        cfg = json.loads(run["run_config_json"] or "{}")
        limit = (cfg.get("run") or {}).get("ownership_stale_note_hours", STALE_NOTE_HOURS_DEFAULT)
        age_h = (datetime.fromisoformat(run["run_timestamp"]) - datetime.fromisoformat(snap["taken_at"])).total_seconds() / 3600
        if age_h > limit:
            notes.append(f"**Roster-%:** the week {run['week']} `{run['run_slot']}` run used a roster-% snapshot that was "
                         f"{age_h:.0f} hours old when the run was logged (taken {snap['taken_at'][:16].replace('T', ' ')} UTC), "
                         f"not that morning's. Its under-50% pool reflects ownership as of then; players whose ownership "
                         f"crossed 50% in between are on the wrong side of the line.")
    return notes


def fmt_hits(h, n):
    if n is None or n == 0:
        return "-"
    h = 0.0 if h is None else h
    hs = f"{h:.0f}" if abs(h - round(h)) < 1e-9 else f"{h:.1f}"
    ns = f"{n:.0f}" if abs(n - round(n)) < 1e-9 else f"{n:.1f}"
    return f"{hs}/{ns} ({100 * h / n:.0f}%)"


def fmt_pct(p):
    return "-" if p is None else f"{p:.0f}%"


# ---- bootstrap ----

def bootstrap_ratio_ci(nums, dens, resamples, seed, level=0.95):
    """CI for sum(nums)/sum(dens) resampling weeks. -> (estimate, lo, hi)."""
    nums, dens = np.asarray(nums, float), np.asarray(dens, float)
    n = len(nums)
    if n == 0 or dens.sum() == 0:
        return None, None, None
    est = nums.sum() / dens.sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(resamples, n))
    d = dens[idx].sum(axis=1)
    r = np.where(d > 0, nums[idx].sum(axis=1) / np.where(d > 0, d, 1), np.nan)
    a = (1 - level) / 2
    return est, float(np.nanpercentile(r, 100 * a)), float(np.nanpercentile(r, 100 * (1 - a)))


def bootstrap_mean_ci(vals, resamples, seed, level=0.95):
    vals = np.asarray([v for v in vals if v is not None], float)
    if vals.size == 0:
        return None, None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, vals.size, size=(resamples, vals.size))
    m = vals[idx].mean(axis=1)
    a = (1 - level) / 2
    return float(vals.mean()), float(np.percentile(m, 100 * a)), float(np.percentile(m, 100 * (1 - a)))


def bootstrap_diff_ci(nums_a, dens_a, nums_b, dens_b, resamples, seed, level=0.95):
    """CI for rate_a - rate_b, resampling the SAME weeks for both (paired)."""
    na, da, nb, db = (np.asarray(x, float) for x in (nums_a, dens_a, nums_b, dens_b))
    n = len(na)
    if n == 0 or da.sum() == 0 or db.sum() == 0:
        return None, None, None
    est = na.sum() / da.sum() - nb.sum() / db.sum()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(resamples, n))
    d1, d2 = da[idx].sum(axis=1), db[idx].sum(axis=1)
    ok = (d1 > 0) & (d2 > 0)
    r = na[idx].sum(axis=1)[ok] / d1[ok] - nb[idx].sum(axis=1)[ok] / d2[ok]
    a = (1 - level) / 2
    return est, float(np.percentile(r, 100 * a)), float(np.percentile(r, 100 * (1 - a)))


# ---- data access ----

def latest_scoring_id(con, season, week):
    r = con.execute("SELECT MAX(scoring_id) FROM tr_scorings WHERE season=? AND week=?", (season, week)).fetchone()
    return r[0]


def scored_weeks(con, season):
    return [r[0] for r in con.execute("SELECT DISTINCT week FROM tr_scorings WHERE season=? ORDER BY week", (season,))]


def run_results(con, scoring_id, run_id):
    res = {}
    for r in con.execute("SELECT model_name, pick_set, n_picks, n_played, top24_hits, spike_hits, total_points, n_draws "
                         "FROM tr_run_results WHERE scoring_id=? AND run_id=?", (scoring_id, run_id)):
        res[(r["model_name"], r["pick_set"])] = dict(r)
    pct = {}
    for r in con.execute("SELECT model_name, pick_set, metric, percentile, dart_mean FROM tr_percentiles "
                         "WHERE scoring_id=? AND run_id=?", (scoring_id, run_id)):
        pct[(r["model_name"], r["pick_set"], r["metric"])] = r["percentile"]
    return res, pct


def _scoreboard_table(res, pct, scope, k):
    ps = pick_set_name(scope, k)
    lines = ["| Model | Top-24 finish | Spike week | Points | Dart pctile (top-24) | Dart pctile (spike) |",
             "|---|---:|---:|---:|---:|---:|"]
    any_row = False
    for m in MODEL_ORDER:
        r = res.get((m, ps))
        if not r:
            continue
        any_row = True
        label = MODEL_LABELS[m].replace("N", str(r["n_draws"])) if m == "dart" and r.get("n_draws") else MODEL_LABELS[m]
        show_pct = m not in ("dart",)
        lines.append(f"| {label} | {fmt_hits(r['top24_hits'], r['n_picks'])} | {fmt_hits(r['spike_hits'], r['n_picks'])} "
                     f"| {r['total_points']:.1f} | "
                     f"{fmt_pct(pct.get((m, ps, 'top24_hits'))) if show_pct else '-'} | "
                     f"{fmt_pct(pct.get((m, ps, 'spike_hits'))) if show_pct else '-'} |")
    return lines if any_row else None


def _hit_defs_block(cfg_json):
    h = cfg_json["hit"]
    return [
        "**How a pick is scored** (league settings: 0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, "
        "-2 INT, -2 fumbles lost, +2 per 2-pt conversion):",
        f"- **Top-24 finish** -- the player finishes top-{h['top_n_per_position']} at his position that week "
        "(\"does it help me win\").",
        f"- **Spike week** -- the player scores at least {h['spike']['multiplier']}x his trailing "
        f"{h['spike']['baseline_games']}-game average and at least {h['spike']['min_points']:.0f} points "
        "(\"does O.D.D.S. beat chance at its own job\").",
        "- **Dart percentile** -- where the model's result falls among N random draws from the same pool with the "
        "same position mix (\"beat 85% of dart throws\"). 50% is chance; ties count half.",
    ]


def build_weekly_report(con, season, week):
    sid = latest_scoring_id(con, season, week)
    if sid is None:
        return None
    sc = con.execute("SELECT * FROM tr_scorings WHERE scoring_id=?", (sid,)).fetchone()
    cfg_json = json.loads(sc["hit_config_json"])
    runs = con.execute("SELECT * FROM tr_runs WHERE season=? AND week=? ORDER BY run_timestamp", (season, week)).fetchall()
    ks = sorted({int(r[0].split("_k")[1]) for r in con.execute(
        "SELECT DISTINCT pick_set FROM tr_run_results WHERE scoring_id=?", (sid,))})
    out = [f"# O.D.D.S. vs the baselines -- {season} Week {week}", "",
           f"_Scored {sc['scored_at'][:16].replace('T', ' ')} UTC; hit definition `{sc['hit_config_hash'][:10]}`._", ""]
    out += _hit_defs_block(cfg_json) + [""]
    for scope in ("all", "u50"):
        out += [f"## {SCOPE_TITLES[scope]}", ""]
        wrote = False
        for run in runs:
            res, pct = run_results(con, sc["scoring_id"], run["run_id"])
            for k in ks:
                tbl = _scoreboard_table(res, pct, scope, k)
                if tbl is None:
                    continue
                if not wrote and scope == "u50":
                    out += [f"_Pool restricted to players under {cfg_json['crowd_hit']['owned_pct_threshold']}% owned "
                            f"at the time of the run; every model re-picks from that smaller pool._", ""]
                    out += [f"- {line}" for line in sleeper_pool_lines(con, runs)] + [""]
                wrote = True
                out += [f"### Top {k} -- {run['run_slot']} run ({run['run_timestamp'][:16].replace('T', ' ')} UTC)", ""] + tbl + [""]
        if not wrote:
            out += ["_No results for this section (no roster-% was available when the run was logged)._", ""]
    out += ["## Caveats", ""] + [f"- {c}" for c in KNOWN_CAVEATS + pool_rule_notes(runs) + ownership_notes(con, runs)] + [""]
    return "\n".join(out)


def _week_run(con, season, week):
    r = con.execute("SELECT run_id FROM tr_runs WHERE season=? AND week=? ORDER BY CASE run_slot WHEN 'thu' THEN 0 ELSE 1 END LIMIT 1",
                    (season, week)).fetchone()
    return r[0] if r else None


def build_season_report(con, season, report_cfg):
    weeks = scored_weeks(con, season)
    if not weeks:
        return None
    B, seed, min_weeks = report_cfg["bootstrap_resamples"], report_cfg["bootstrap_seed"], report_cfg["min_weeks_to_call"]
    per_week = {}  # (week) -> (res, pct)
    hashes = set()
    used_runs = []
    for w in weeks:
        sid = latest_scoring_id(con, season, w)
        hashes.add(con.execute("SELECT hit_config_hash FROM tr_scorings WHERE scoring_id=?", (sid,)).fetchone()[0])
        rid = _week_run(con, season, w)
        per_week[w] = run_results(con, sid, rid)
        used_runs.append(con.execute("SELECT * FROM tr_runs WHERE run_id=?", (rid,)).fetchone())
    ks = sorted({int(ps.split("_k")[1]) for (res, _p) in per_week.values() for (_m, ps) in res})
    out = [f"# O.D.D.S. vs the baselines -- {season} season to date", "",
           f"_{len(weeks)} scored week(s): {', '.join(str(w) for w in weeks)}. One run per week (the first snapshot)._", ""]
    if len(hashes) > 1:
        out += [f"> **Warning:** these weeks were scored under {len(hashes)} different hit definitions "
                f"({', '.join(h[:8] for h in sorted(hashes))}); cumulative numbers mix definitions.", ""]

    notes = []
    for scope in ("all", "u50"):
        out += [f"## {SCOPE_TITLES[scope]}", ""]
        wrote = False
        for k in ks:
            ps = pick_set_name(scope, k)
            wk = [w for w in weeks if ("odds_prod", ps) in per_week[w][0]]
            if not wk:
                continue
            wrote = True
            out += [f"### Top {k} ({len(wk)} week(s))", "",
                    "| Model | Top-24 rate (95% CI) | Spike rate (95% CI) | Mean dart pctile, top-24 (95% CI) | Mean dart pctile, spike (95% CI) |",
                    "|---|---|---|---|---|"]
            series = {}
            for m in MODEL_ORDER:
                rows = [per_week[w][0].get((m, ps)) for w in wk]
                if not all(rows):
                    continue
                nums_t = [r["top24_hits"] or 0 for r in rows]
                nums_s = [r["spike_hits"] or 0 for r in rows]
                dens = [r["n_picks"] for r in rows]
                series[m] = (nums_t, nums_s, dens)
                et = bootstrap_ratio_ci(nums_t, dens, B, seed)
                es = bootstrap_ratio_ci(nums_s, dens, B, seed + 1)

                def ci(e):
                    if e[0] is None:
                        return "-"
                    if len(wk) < MIN_WEEKS_FOR_CI:
                        return f"{100 * e[0]:.1f}% (CI needs {MIN_WEEKS_FOR_CI}+ weeks)"
                    return f"{100 * e[0]:.1f}% ({100 * e[1]:.1f}-{100 * e[2]:.1f})"

                def pci(metric):
                    if m == "dart":
                        return "-"
                    vals = [per_week[w][1].get((m, ps, metric)) for w in wk]
                    e = bootstrap_mean_ci(vals, B, seed + 2)
                    if e[0] is None:
                        return "-"
                    if len(wk) < MIN_WEEKS_FOR_CI:
                        return f"{e[0]:.0f}% (CI needs {MIN_WEEKS_FOR_CI}+ weeks)"
                    return f"{e[0]:.0f}% ({e[1]:.0f}-{e[2]:.0f})"

                out.append(f"| {MODEL_LABELS[m].replace('N ', '')} | {ci(et)} | {ci(es)} | {pci('top24_hits')} | {pci('spike_hits')} |")
            out.append("")
            if "odds_prod" in series and "dart" in series:
                for idx, name in ((0, "top-24 finish"), (1, "spike week")):
                    e = bootstrap_diff_ci(series["odds_prod"][idx], series["odds_prod"][2], series["dart"][idx],
                                          series["dart"][2], B, seed + 3 + idx)
                    notes.append((scope, k, name, len(wk), e))
    out += ["## Plain-English read", ""]
    if not notes:
        out += ["Nothing to compare yet."]
    else:
        n_weeks = len(weeks)
        if n_weeks < min_weeks:
            out += [f"**Too early to call a winner.** Only {n_weeks} scored week(s) so far, and the confidence intervals "
                    f"above are wide because they're built from {n_weeks} data point(s) (one per week). "
                    f"Read every number as a first look, not a verdict; we'd want at least {min_weeks} weeks before "
                    "saying whether O.D.D.S. is better than chance."]
        for scope, k, name, n, (est, lo, hi) in notes:
            if est is None:
                continue
            verdict = ("ahead of chance" if lo > 0 else "behind chance" if hi < 0 else "not distinguishable from chance")
            out.append(f"- {SCOPE_TITLES[scope]}, top {k}, {name}: O.D.D.S. minus dart = {100 * est:+.1f} points "
                       f"(95% CI {100 * lo:+.1f} to {100 * hi:+.1f}) over {n} week(s) -- {verdict}"
                       + (" (but see the small-sample note)." if n_weeks < min_weeks else "."))
    crowd = build_crowd_section(con, season)
    out += [""] + crowd + ["", "## Caveats", ""] + [f"- {c}" for c in KNOWN_CAVEATS + pool_rule_notes(used_runs) + ownership_notes(con, used_runs)
                                                           + sleeper_pool_lines(con, used_runs, only_if_unknown=True)] + [""]
    return "\n".join(out)


def build_crowd_section(con, season):
    rows = con.execute(
        "SELECT c.model_name, c.pick_set, COUNT(*) AS n_total, "
        "SUM(CASE WHEN c.crowd_hit IS NOT NULL THEN 1 ELSE 0 END) AS n_eval, SUM(COALESCE(c.crowd_hit, 0)) AS hits "
        "FROM tr_crowd_results c JOIN tr_runs r ON r.run_id = c.run_id WHERE r.season=? GROUP BY c.model_name, c.pick_set",
        (season,)).fetchall()
    out = ["## Crowd hits (secondary metric)", ""]
    if not rows:
        out += ["_No picks have a closed 2-week window yet. A crowd hit = a pick that started at or under 50% owned and "
                "rose above 50% (ESPN percentOwned) within 14 days. Picks already over 50% can't qualify and are excluded._"]
        return out
    out += ["| Model | Pick set | Picks evaluated | Crowd hits | Rate |", "|---|---|---:|---:|---:|"]
    for r in sorted(rows, key=lambda r: (r["pick_set"], MODEL_ORDER.index(r["model_name"]) if r["model_name"] in MODEL_ORDER else 9)):
        rate = f"{100 * r['hits'] / r['n_eval']:.0f}%" if r["n_eval"] else "-"
        out.append(f"| {MODEL_LABELS.get(r['model_name'], r['model_name'])} | {r['pick_set']} | {r['n_eval']} | {r['hits']} | {rate} |")
    out += ["", "_Excluded from any backtest: roster-% history only exists from the day collection started._"]
    return out
