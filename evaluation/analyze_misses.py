#!/usr/bin/env python3
"""Post-hoc miss diagnostic over already-committed prediction artifacts.

No model training, no scoring, no leakage concerns -- this only reads what
was frozen before each week was played (predictions/<season>_week<NN>.json:
top 25, scored_pool, and the suppression watch lists) and compares it with
what actually happened. Actuals (fantasy points, spike_flag, carries/targets)
come from labels_player_week / player_week_stats in the local DB, exactly as
evaluation/verify_week.py reads them; the per-player spike threshold is
recomputed with verify_week.trailing_baseline (imported, not re-implemented).
Nothing is written -- not the verification log, not any file. Verified weeks
default to whatever predictions/verification_log.json already records.

SAMPLE-SIZE CAVEAT (printed in the output too): a couple of weeks means a few
dozen top-25 non-hits and a few dozen missed spikes split across four
positions. These are descriptive counts, not conclusions. Weeks 1-3 are also
the most heavily gated part of the season (offseason suppressions release
only once a player has 3 games in the current season), and weeks 1-2 were
scored by the earlier 10-feature model, not the current production model --
the model commit is printed per week from the prediction file itself.

1. MISS TAXONOMY (top-25 entries that did not spike). Mutually exclusive and
   exhaustive; checked in this order:
     DNP                     no player_week_stats row that week
     NO-OPPORTUNITY          played, but opportunities < OPPORTUNITY_FLOOR
     NEAR-MISS               cleared the floor, points >= 75% of the spike
                             threshold (but under it, since it isn't a hit)
     OPP-NO-CONVERSION       cleared the floor, points < 75% of the threshold
     UNLABELED               played but the pipeline produced no spike label
                             (not expected for top-25 candidates; shown so
                             the table is exhaustive)
   "Opportunities" = carries + targets + pass attempts. Receptions are not
   added on top of targets (they're a subset of them -- "touches + targets"
   would double count), and pass attempts are included because for a QB
   carries + targets alone would call a full-time starter opportunity-less.
   OPPORTUNITY_FLOOR = 5, chosen a priori from history rather than from the
   weeks being analyzed: among the 10,659 QB/RB/WR/TE spike rows in 2010-2025
   only ~10% had fewer than 5 opportunities, i.e. under that volume a spike is
   rare. The script re-derives and prints that share as a check. Near-miss
   requires clearing the floor so the four buckets don't overlap; the count of
   NO-OPPORTUNITY rows that still scored >= 75% of threshold is printed as a
   footnote so nothing is hidden by the precedence order.

2. MISSED SPIKES. Every player with spike_flag = 1 who was not in the top 25,
   located in the committed artifacts: RANKED-LOW (in scored_pool; pooled rank
   and rank within position shown), SUPPRESSED (which watch list, and the
   reason recorded there), or NOT-A-CANDIDATE (in none of them -- filtered
   upstream of scoring; the likely reason is derived from the DB using
   score_week.py's own recency and position rules). Position for this section
   is the player's actual game-week position, matching verify_week's base
   rate. Gating failure = suppressed + not-a-candidate; ranking failure =
   ranked-low. A funnel table also shows the spike rate inside each group
   (scored / each watch list), since a suppressed share of spikes only means
   something next to the suppressed share of the pool.

3. THRESHOLD CONTEXT. For near-misses (and, for reference, the other played
   non-hit buckets): median shortfall = threshold - actual points, and median
   actual as a share of threshold.
"""
import argparse
import json
import sqlite3
import statistics
from pathlib import Path

from verify_week import trailing_baseline

REPO_ROOT = Path(__file__).resolve().parent.parent
PREDICTIONS_DIR = REPO_ROOT / "predictions"

POSITIONS = ("QB", "RB", "WR", "TE")
OTHER = "OTHER"
ROSTERABLE_POSITIONS = set(POSITIONS)  # mirrors model/score_week.py
OPPORTUNITY_FLOOR = 5
NEAR_MISS_FRACTION = 0.75
TAXONOMY = ("DNP", "NO-OPPORTUNITY", "OPP-NO-CONVERSION", "NEAR-MISS", "UNLABELED")
LOCATIONS = ("RANKED-LOW", "SUPPRESSED: team-changed", "SUPPRESSED: new-competitor",
             "SUPPRESSED: unavailable", "NOT-A-CANDIDATE")

SMALL_SAMPLE_CAVEAT = (
    "SMALL SAMPLE -- descriptive, not conclusive. Few weeks, few players per cell; "
    "weeks 1-3 are also the most heavily gated part of the season."
)


def pos_bucket(pos):
    return pos if pos in POSITIONS else OTHER


def pct(n, d):
    return f"{100 * n / d:5.1f}%" if d else "   n/a"


def cell(n, d):
    return f"{n:3d} ({pct(n, d)})"


def historical_floor_check(con):
    row = con.execute(
        """SELECT SUM(CASE WHEN COALESCE(s.rush_att,0)+COALESCE(s.rec_tgt,0)+COALESCE(s.attempts,0) < ? THEN 1 ELSE 0 END),
                  COUNT(*)
           FROM labels_player_week l JOIN player_week_stats s
             ON s.season=l.season AND s.week=l.week AND s.player_id=l.player_id
           WHERE l.spike_flag = 1 AND l.season <= 2025 AND s.pos IN ('QB','RB','WR','TE')""",
        (OPPORTUNITY_FLOOR,),
    ).fetchone()
    below, total = row[0] or 0, row[1]
    return below, total


def verified_weeks(log_path, season):
    with open(log_path, encoding="utf-8") as f:
        log = json.load(f)
    return sorted(w["week"] for w in log["weeks"] if w["season"] == season), log


def load_week_data(con, season, week):
    stats = {
        r["player_id"]: r
        for r in con.execute(
            "SELECT player_id, team, pos, rush_att, rec_tgt, attempts FROM player_week_stats WHERE season=? AND week=?",
            (season, week),
        )
    }
    labels = {
        r["player_id"]: (r["fantasy_points_half"], r["spike_flag"])
        for r in con.execute(
            "SELECT player_id, fantasy_points_half, spike_flag FROM labels_player_week WHERE season=? AND week=?",
            (season, week),
        )
    }
    return stats, labels


def opportunities(stat_row):
    return (stat_row["rush_att"] or 0) + (stat_row["rec_tgt"] or 0) + (stat_row["attempts"] or 0)


def classify_top_entry(entry, stats, labels, threshold):
    """Returns (category, pts, opp). category 'HIT' for a spiker."""
    pid = entry["player_id"]
    stat = stats.get(pid)
    if stat is None:
        return "DNP", None, None
    pts, flag = labels.get(pid, (None, None))
    opp = opportunities(stat)
    if flag == 1:
        return "HIT", pts, opp
    if flag is None or threshold is None:
        return "UNLABELED", pts, opp
    if opp < OPPORTUNITY_FLOOR:
        return "NO-OPPORTUNITY", pts, opp
    if pts >= NEAR_MISS_FRACTION * threshold:
        return "NEAR-MISS", pts, opp
    return "OPP-NO-CONVERSION", pts, opp


def not_candidate_reason(con, pid, season, week, stat_pos):
    last = con.execute(
        """SELECT season, week, pos FROM player_week_stats
           WHERE player_id=? AND (season < ? OR (season = ? AND week < ?))
           ORDER BY season DESC, week DESC LIMIT 1""",
        (pid, season, season, week),
    ).fetchone()
    if last is not None and last["season"] < season - 1:
        return f"recency cutoff (last game {last['season']} wk {last['week']}, before {season - 1})"
    if last is not None and last["pos"] not in ROSTERABLE_POSITIONS:
        return f"inferred position from last game not rosterable ({last['pos']})"
    if stat_pos not in ROSTERABLE_POSITIONS:
        return f"position not rosterable ({stat_pos})"
    return "not explained by the committed artifacts / recency / position rules"


def analyze_week(con, season, week, predictions_dir, loaded_seasons, names):
    pred_path = predictions_dir / f"{season}_week{week:02d}.json"
    with open(pred_path, encoding="utf-8") as f:
        pred = json.load(f)
    stats, labels = load_week_data(con, season, week)

    top = pred["top"]
    top_ids = {e["player_id"] for e in top}
    pool = sorted(pred.get("scored_pool", []), key=lambda e: e["score"], reverse=True)
    pool_rank = {e["player_id"]: i + 1 for i, e in enumerate(pool)}
    pool_by_pos = {}
    for e in pool:
        pool_by_pos.setdefault(e["pos"], []).append(e["player_id"])
    pos_rank = {pid: i + 1 for ids in pool_by_pos.values() for i, pid in enumerate(ids)}
    pos_pool_n = {pid: len(ids) for ids in pool_by_pos.values() for pid in ids}

    watch = {
        "SUPPRESSED: team-changed": {e["player_id"]: e for e in pred.get("team_changed_watch_list", [])},
        "SUPPRESSED: new-competitor": {e["player_id"]: e for e in pred.get("new_competitor_watch_list", [])},
        "SUPPRESSED: unavailable": {e["player_id"]: e for e in pred.get("unavailable_watch_list", [])},
    }
    overlaps = 0
    seen = set(pool_rank)
    for lst in watch.values():
        overlaps += len(seen & set(lst))
        seen |= set(lst)

    top_rows = []
    for e in top:
        pid = e["player_id"]
        _b, threshold = trailing_baseline(con, pid, season, week, loaded_seasons)
        cat, pts, opp = classify_top_entry(e, stats, labels, threshold)
        top_rows.append({
            "rank": e["rank"], "player_id": pid, "name": e["name"], "pos": pos_bucket(e["pos"]),
            "team": e["team"], "category": cat, "pts": pts, "opp": opp, "threshold": threshold,
            "gap": (threshold - pts) if (threshold is not None and pts is not None) else None,
            "ratio": (pts / threshold) if (threshold and pts is not None) else None,
        })

    spikers = [pid for pid, (_pts, flag) in labels.items() if flag == 1]
    missed = []
    for pid in spikers:
        if pid in top_ids:
            continue
        stat = stats.get(pid)
        pts, _flag = labels[pid]
        _b, threshold = trailing_baseline(con, pid, season, week, loaded_seasons)
        pos = pos_bucket(stat["pos"]) if stat is not None else OTHER
        rec = {"player_id": pid, "name": names.get(pid, pid), "pos": pos,
               "team": stat["team"] if stat is not None else "?", "pts": pts, "threshold": threshold,
               "loc": None, "detail": "", "rank": None, "pos_rank": None, "pool_n": len(pool), "pos_pool_n": None}
        if pid in pool_rank:
            rec.update(loc="RANKED-LOW", rank=pool_rank[pid], pos_rank=pos_rank[pid], pos_pool_n=pos_pool_n[pid])
        else:
            for loc, lst in watch.items():
                if pid in lst:
                    rec["loc"] = loc
                    ent = lst[pid]
                    if loc == "SUPPRESSED: new-competitor":
                        comps = ent.get("new_competitors", [])
                        prod = any(c.get("via_production") for c in comps)
                        draft = any(c.get("via_draft_capital") for c in comps)
                        trig = "both" if (prod and draft) else ("production" if prod else "draft capital")
                        rec["detail"] = f"trigger: {trig}; new: " + ", ".join(c["name"] for c in comps)
                    elif loc == "SUPPRESSED: team-changed":
                        rec["detail"] = f"{ent.get('old_team')} -> {ent.get('new_team')}"
                    else:
                        rec["detail"] = ent.get("reason", "")
                    break
            if rec["loc"] is None:
                rec["loc"] = "NOT-A-CANDIDATE"
                rec["detail"] = not_candidate_reason(con, pid, season, week, stat["pos"] if stat is not None else None)
        missed.append(rec)

    # Funnel: candidate groups (all are position-filtered candidates), and how
    # many of each group's members actually spiked (DNP counts as a non-spike,
    # matching verify_week's precision denominators).
    spike_set = set(spikers)
    groups = {"scored pool": set(pool_rank)}
    for loc, lst in watch.items():
        groups[loc.replace("SUPPRESSED: ", "suppressed ")] = set(lst)
    funnel = {g: (len(ids), len(ids & spike_set)) for g, ids in groups.items()}
    all_cands = set().union(*groups.values())
    funnel["spikers outside every list"] = (None, len(spike_set - all_cands))

    return {
        "season": season, "week": week, "model": pred.get("model", {}), "top_rows": top_rows,
        "missed": missed, "funnel": funnel, "n_spikes": len(spikers), "n_pool": len(pool),
        "overlaps": overlaps, "counts": pred.get("counts", {}),
    }


def print_taxonomy(rows, title):
    print(f"\n{title}")
    hits = sum(1 for r in rows if r["category"] == "HIT")
    nonhits = [r for r in rows if r["category"] != "HIT"]
    print(f"  top-25 slots: {len(rows)}   hits: {hits}   non-hits: {len(nonhits)}")
    if not nonhits:
        return
    hdr = f"  {'pos':5} {'non-hits':>16} | " + " ".join(f"{c:>17}" for c in TAXONOMY)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for pos in POSITIONS + (OTHER, "ALL"):
        sub = nonhits if pos == "ALL" else [r for r in nonhits if r["pos"] == pos]
        if not sub and pos == OTHER:
            continue
        counts = [sum(1 for r in sub if r["category"] == c) for c in TAXONOMY]
        print(f"  {pos:5} {cell(len(sub), len(nonhits)):>16} | " + " ".join(f"{cell(c, len(sub)):>17}" for c in counts))
    print("  (non-hits column: count and share of ALL non-hits; category columns: count and share of that row's non-hits)")
    sub_floor_close = [r for r in nonhits if r["category"] == "NO-OPPORTUNITY"
                       and r["ratio"] is not None and r["ratio"] >= NEAR_MISS_FRACTION]
    print(f"  footnote: {len(sub_floor_close)} NO-OPPORTUNITY row(s) still scored >= {int(NEAR_MISS_FRACTION * 100)}% of threshold "
          f"(under the floor, so not counted as near-misses)")


def print_threshold_context(rows, title):
    print(f"\n{title}")
    played = [r for r in rows if r["category"] in ("NEAR-MISS", "OPP-NO-CONVERSION", "NO-OPPORTUNITY") and r["gap"] is not None]
    print(f"  {'group':22} {'n':>3} {'median shortfall (pts)':>24} {'median actual/threshold':>25}")
    for label, cats in (("NEAR-MISS", ("NEAR-MISS",)), ("OPP-NO-CONVERSION", ("OPP-NO-CONVERSION",)),
                        ("NO-OPPORTUNITY", ("NO-OPPORTUNITY",)), ("all played non-hits", None)):
        sub = played if cats is None else [r for r in played if r["category"] in cats]
        if not sub:
            print(f"  {label:22} {0:3d} {'n/a':>24} {'n/a':>25}")
            continue
        print(f"  {label:22} {len(sub):3d} {statistics.median(r['gap'] for r in sub):24.2f} "
              f"{statistics.median(r['ratio'] for r in sub):25.2f}")
    nm = [r for r in played if r["category"] == "NEAR-MISS"]
    if nm:
        print("  near-misses by position: " + ", ".join(
            f"{pos} n={len(s)} median shortfall {statistics.median(r['gap'] for r in s):.2f}"
            for pos in POSITIONS for s in [[r for r in nm if r["pos"] == pos]] if s))


def print_missed(missed, funnel, title, list_players=True):
    print(f"\n{title}")
    n = len(missed)
    print(f"  spikers outside the top 25: {n}")
    if not n:
        return
    hdr = f"  {'pos':5} {'missed':>6} | " + " ".join(f"{loc:>27}" for loc in LOCATIONS)
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for pos in POSITIONS + (OTHER, "ALL"):
        sub = missed if pos == "ALL" else [m for m in missed if m["pos"] == pos]
        if not sub and pos == OTHER:
            continue
        print(f"  {pos:5} {len(sub):6d} | " + " ".join(
            f"{cell(sum(1 for m in sub if m['loc'] == loc), len(sub)):>27}" for loc in LOCATIONS))
    supp = sum(1 for m in missed if m["loc"].startswith("SUPPRESSED"))
    nocand = sum(1 for m in missed if m["loc"] == "NOT-A-CANDIDATE")
    low = sum(1 for m in missed if m["loc"] == "RANKED-LOW")
    print(f"  suppressed rather than ranked low: {supp} of {n} ({pct(supp, n).strip()}); ranked low: {low}; "
          f"never a candidate: {nocand}; gating failures (suppressed + never a candidate): {supp + nocand} ({pct(supp + nocand, n).strip()})")
    ranks = [m["rank"] for m in missed if m["loc"] == "RANKED-LOW"]
    if ranks:
        print(f"  ranked-low pooled ranks: median {statistics.median(ranks):.0f}, min {min(ranks)}, max {max(ranks)} "
              f"(pool sizes vary by week)")
    trig = {}
    for m in missed:
        if m["loc"] == "SUPPRESSED: new-competitor":
            key = m["detail"].split(";")[0]
            trig[key] = trig.get(key, 0) + 1
    if trig:
        print("  new-competitor suppressions by trigger: " + ", ".join(f"{k} {v}" for k, v in sorted(trig.items())))

    print(f"\n  Funnel context -- spike rate inside each candidate group (a suppressed share of spikes only means")
    print(f"  something next to the suppressed share of the pool):")
    print(f"  {'group':30} {'size':>6} {'spikers':>8} {'spike rate':>11}")
    for g, (size, sp) in funnel.items():
        if size is None:
            print(f"  {g:30} {'':>6} {sp:8d}")
        else:
            print(f"  {g:30} {size:6d} {sp:8d} {pct(sp, size):>11}")

    if list_players:
        print(f"\n  Per-player detail (missed spikers):")
        order = {loc: i for i, loc in enumerate(LOCATIONS)}
        for m in sorted(missed, key=lambda m: (order[m["loc"]], m["rank"] or 0, m["name"])):
            thr = f"{m['threshold']:.1f}" if m["threshold"] is not None else "n/a"
            where = m["loc"]
            if m["loc"] == "RANKED-LOW":
                where += f" (pool {m['rank']}/{m['pool_n']}, {m['pos']} {m['pos_rank']}/{m['pos_pool_n']})"
            print(f"  {m['name']:24.24} {m['pos']:5} {m['team']:4} pts {m['pts']:5.1f} (thr {thr:>5})  {where}"
                  + (f"  -- {m['detail']}" if m["detail"] else ""))


def check_against_log(result, log):
    entry = next((w for w in log["weeks"] if w["season"] == result["season"] and w["week"] == result["week"]), None)
    if entry is None:
        return "no log entry to check against"
    hits = sum(1 for r in result["top_rows"] if r["category"] == "HIT")
    dnp = sum(1 for r in result["top_rows"] if r["category"] == "DNP")
    ok = hits == entry["precision_at_25"]["hits"] and dnp == entry["n_dnp"]
    return (f"hits {hits} / DNP {dnp} vs verification_log {entry['precision_at_25']['hits']} / {entry['n_dnp']} -> "
            f"{'MATCH' if ok else 'MISMATCH'}")


def merge_funnels(results):
    merged = {}
    for r in results:
        for g, (size, sp) in r["funnel"].items():
            a, b = merged.get(g, (0 if size is not None else None, 0))
            merged[g] = ((a + size) if size is not None else None, b + sp)
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--weeks", nargs="+", type=int, default=None,
                    help="default: every week already recorded in verification_log.json for --season")
    ap.add_argument("--predictions-dir", default=str(PREDICTIONS_DIR))
    ap.add_argument("--log", default=str(PREDICTIONS_DIR / "verification_log.json"))
    ap.add_argument("--summary-only", action="store_true", help="skip the per-player missed-spiker listing")
    args = ap.parse_args()

    weeks, log = verified_weeks(args.log, args.season)
    if args.weeks:
        weeks = sorted(args.weeks)
    if not weeks:
        raise SystemExit(f"[ERROR] no verified weeks for season {args.season} in {args.log}")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    loaded_seasons = [r[0] for r in con.execute("SELECT DISTINCT season FROM labels_player_week ORDER BY season")]
    names = {r["player_id"]: r["full_name"] for r in con.execute("SELECT player_id, full_name FROM ref_players")}

    print(f"=== analyze_misses: season {args.season}, weeks {weeks} ===")
    print(f"[CAVEAT] {SMALL_SAMPLE_CAVEAT}")
    below, total = historical_floor_check(con)
    print(f"[DEFINITIONS] opportunities = carries + targets + pass attempts; OPPORTUNITY_FLOOR = {OPPORTUNITY_FLOOR} "
          f"(historical check: {below}/{total} = {100 * below / total:.1f}% of 2010-2025 QB/RB/WR/TE spike rows had fewer); "
          f"near-miss = cleared the floor and scored >= {int(NEAR_MISS_FRACTION * 100)}% of the spike threshold.")

    results = []
    for week in weeks:
        res = analyze_week(con, args.season, week, Path(args.predictions_dir), loaded_seasons, names)
        results.append(res)
        m = res["model"]
        print(f"\n{'=' * 100}\nWEEK {week}  (model {m.get('filename')} @ commit {str(m.get('git_commit'))[:7]}; "
              f"{res['n_spikes']} spikers league-wide, scored pool {res['n_pool']})")
        print(f"[CHECK] {check_against_log(res, log)}")
        if res["overlaps"]:
            print(f"[WARN] {res['overlaps']} player(s) appear in more than one committed list; first match used")
        print_taxonomy(res["top_rows"], f"1. Miss taxonomy, week {week}")
        print_missed(res["missed"], res["funnel"], f"2. Missed spikes, week {week}", list_players=not args.summary_only)
        print_threshold_context(res["top_rows"], f"3. Threshold context, week {week}")

    if len(results) > 1:
        print(f"\n{'=' * 100}\nCUMULATIVE over weeks {weeks}")
        rows = [r for res in results for r in res["top_rows"]]
        missed = [m for res in results for m in res["missed"]]
        print_taxonomy(rows, "1. Miss taxonomy, cumulative")
        print_missed(missed, merge_funnels(results), "2. Missed spikes, cumulative", list_players=False)
        print_threshold_context(rows, "3. Threshold context, cumulative")

    print(f"\n[CAVEAT] {SMALL_SAMPLE_CAVEAT}")
    con.close()


if __name__ == "__main__":
    main()
