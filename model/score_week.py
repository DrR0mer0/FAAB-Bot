#!/usr/bin/env python3
"""Score every eligible player for a target (season, week) using the saved
O.D.D.S. model, and print the top N by predicted spike probability.

Works for a not-yet-played week: eligibility and features are computed via
features_lib.FeatureEngine using only data strictly before the target week,
the same function used to build the historical training table.

If a current weekly roster file is available (nflverse_raw/roster_weekly_
<season>.csv -- see load_current_roster; NOT the season-level roster_
<season>.csv, which is stale between refreshes and only used elsewhere by
data/crosscheck_roster_2026.py), candidates are cross-referenced against
it. A confirmed offseason team change (vs. the team inferred from the
player's last game) gets its schedule-dependent features (opponent
matchup, home/away, short week, presumed-starter check) recomputed for
the corrected team -- but the model score is suppressed entirely, since
usage features (touches, touch share) still reflect the old team and
there's no real data yet for the new one. A meaningful new same-position
competitor (judged from 2025 production or 2026 draft capital) suppresses
the incumbent's score the same way, on the theory that their trailing
touch share predates the competition. Those players are reported
separately as watch lists, not folded into the ranking.

Both suppressions are released automatically, and identically, once a player
has >= features_lib.MIN_PRIOR_GAMES games in the CURRENT season: at that
point their trailing window (used for touches/touch-share/etc.) is built
entirely from this season's real data, so no stale prior-season usage feeds
any feature and the offseason concern no longer applies -- regardless of
what the static roster/production checks would otherwise say.

Availability gate: a player whose most recent weekly roster status (from
the same --weekly-roster file as team-mapping above, via load_current_
roster's status_of) indicates a known long-term absence -- reserve/IR,
PUP, non-football injury, or suspension -- is suppressed unconditionally,
checked BEFORE anything else in the suppression loop (including the
recency-release above -- a player accumulating real current-season games
doesn't matter if they're currently on IR and can't play). Deliberately
does NOT gate on game-day designations (questionable/doubtful/out, from
nflverse's separate `injuries` release): those post through the week,
often after predictions are generated, and players already on long-term
reserve typically don't even appear on that report. This is a separate
DECISION from team-mapping (a player on reserve still belongs to his
team) even though both now read the same file -- status_of is never
filtered the way team_of/team_pos_roster are, so it sees every status,
not just the "confirmed" ones.

check_roster_staleness() prints a loud, hard-to-miss warning (not a hard
failure) if --weekly-roster is more than a few days old on disk, or its
newest rows don't reach close to the week being scored -- a quiet stale
roster file is exactly how a two-week-old snapshot went unnoticed. Run
data/fetch_weekly_update.py before scoring each week to keep it current.

Shadow scoring: alongside the production model, the same candidate pool
(same eligibility, same suppression -- neither depends on which model does
the scoring) is ALSO scored with a second, fixed "shadow" model -- by
default odds_xgb_model_production_10feature.joblib, the pre-Group-1
production model -- so a head-to-head against the current production model
accumulates week over week (see evaluation/verify_week.py). Skipped with a
warning if the shadow model file isn't present locally (it's gitignored,
like every .joblib). Like the production file, the shadow prediction file
must be generated before the week is played and committed frozen -- never
regenerated afterward, same discipline throughout this pipeline.
"""
import argparse
import csv
import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np

from features_lib import MIN_PRIOR_GAMES, PERSISTED_FEATURE_COLS, FeatureEngine
from model_metadata import read_metadata

REPO_ROOT = Path(__file__).resolve().parent.parent

# Only these statuses represent a confirmed, current team assignment. CUT/RET/
# EXE players aren't actually on the team a roster file last associated them
# with, so they're excluded rather than asserting a bogus team.
CONFIRMED_ROSTER_STATUSES = {"ACT", "DEV", "RES"}
TEAM_CHANGE_REASON = "team changed this offseason — insufficient data on new team"
NEW_COMPETITOR_REASON = "new same-position addition(s) this offseason — trailing touch share predates the competition"

# Roster statuses that mean "known unavailable for an extended stretch,
# not just this game" -- nflverse's own dictionary_roster_status.csv
# documents RES ("reserve list"), PUP, and RSN (non-football injury
# reserve) as separate status values, but in practice this data source
# bundles IR/PUP/NFI all under the single coarse "RES" code (confirmed by
# cross-referencing status_description_abbr sub-codes -- R01 dominates and
# was confirmed, via Jordan Mason, to mean IR). SUS (suspended) is
# included per the task; PUP/RSN are included defensively in case a future
# pull ever reports them as distinct top-level statuses. Does NOT include
# CUT/RET/EXE -- those are a different category (no longer on the team at
# all, or a legal-process exemption) and out of scope for this gate.
UNAVAILABLE_STATUSES = {"RES", "PUP", "SUS", "RSN"}
UNAVAILABLE_REASON = "known long-term unavailable (reserve/IR, PUP, non-football injury, or suspension) per the most recent weekly roster"

# Fantasy-relevant offensive positions only; IDP/O-line/K/P aren't rosterable
# in this league.
ROSTERABLE_POSITIONS = {"QB", "RB", "WR", "TE"}

# "Meaningful threat" bar for a new same-position arrival: either they
# produced at a real starter/committee-back level in 2025, or they carry
# first-few-round draft capital in the 2026 class (a Day 1-2 rookie is often
# a bigger threat to an incumbent's role than a marginal veteran). A rookie
# or futures signing with neither trips nothing.
PROD_THRESHOLDS = {"RB": 50, "WR": 30, "TE": 30, "QB": 100}  # touches/targets/targets/attempts
DRAFT_CAPITAL_PICK_CUTOFF = 96  # ~3 rounds at 32 picks/round


def get_eligible_candidates(engine, season, week):
    """The season-gap reset in features_lib only catches *structural* holes in
    the loaded-season sequence (e.g. 2019, or 2025 when scoring into 2026);
    it says nothing about an individual player who simply stopped playing.
    2024->2025 has no such hole, so a long-retired veteran (Matt Ryan, Julio
    Jones, ...) whose last real game was years ago would otherwise still
    pass eligibility on stale data. Require a recent-enough appearance to be
    a plausible current-roster candidate."""
    recency_cutoff = season - 1
    candidates = []
    for pid, entries in engine.player_history.items():
        if entries[-1][0] < recency_cutoff:
            continue
        feat = engine.compute_features(season, week, pid)
        if feat is None:
            continue
        candidates.append((pid, feat))
    return candidates


def render_markdown_report(output):
    """Renders the same content already written to --json-out as a
    markdown report, matching the style of evaluation/verify_week.py's
    _verified.md reports so prediction and verification files read
    consistently. Takes exactly the dict shape written as JSON (or one
    read back from a previously-written file, e.g. model/render_prediction_
    report.py), so a report can be (re)rendered without recomputing or
    altering the JSON itself."""
    counts = output["counts"]
    model = output.get("model") or {}
    lines = [
        f"# Predictions: {output['season']} week {output['week']}",
        "",
        f"Model: `{model.get('filename')}` (commit `{model.get('git_commit')}`)",
        "",
        f"Generated: {output.get('generated_at')}",
        "",
        f"- Total eligible: {counts['total_eligible']}",
        f"- After position filter (QB/RB/WR/TE): {counts['after_position_filter']}",
        f"- Stable scored: {counts['stable_scored']}"
        + (f" ({counts['released_from_offseason_suppression']} released from offseason suppression this week)"
           if counts.get("released_from_offseason_suppression") is not None else ""),
    ]
    if counts.get("unavailable_suppressed") is not None:
        lines.append(f"- Unavailable (reserve/PUP/NFI/suspended) suppressed: {counts['unavailable_suppressed']}")
    lines += [
        f"- Team-changed suppressed: {counts['team_changed_suppressed']}",
        f"- New-competitor suppressed: {counts['new_competitor_suppressed']} "
        f"({counts['new_competitor_via_production_only']} via production, "
        f"{counts['new_competitor_via_draft_only']} via draft, {counts['new_competitor_via_both']} via both)",
        "",
        "| Rank | Name | Pos | Team | Score |",
        "|---:|---|---|---|---:|",
    ]
    for r in output["top"]:
        lines.append(f"| {r['rank']} | {r['name']} | {r['pos']} | {r['team']} | {r['score']:.4f} |")
    return "\n".join(lines) + "\n"


def load_current_roster(path, season, week):
    """Reads nflverse's weekly_rosters release (roster_weekly_<season>.csv)
    -- one row per player PER WEEK, unlike the season-level
    roster_<season>.csv (still fetched for data/crosscheck_roster_2026.py,
    but no longer read here) whose own `week` column means "the most
    recent week this player's entry was touched," which is ambiguous for
    someone quietly sitting on reserve. Filters to each player's status as
    of the most recent available week <= the target week -- unambiguous,
    no interpretation needed.

    Returns:
      team_of: gsis_id -> team, for CONFIRMED_ROSTER_STATUSES players only
               (team-change detection).
      team_pos_roster: (team, position) -> [gsis_id, ...], same filter
               (new-same-position-competitor detection).
      draft_pick_of: gsis_id -> overall pick number, rookies of this draft
               class only (rookie_year == season).
      status_of: gsis_id -> status, for EVERY player with a known row --
               deliberately NOT filtered to CONFIRMED_ROSTER_STATUSES,
               since the availability gate needs to see RES/PUP/SUS/RSN
               regardless of whether that status is "confirmed."
    """
    latest = {}  # gsis_id -> (week, row)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("season") != str(season):
                continue
            try:
                w = int(r.get("week") or 0)
            except ValueError:
                continue
            if w <= 0 or w > week:
                continue
            pid = r.get("gsis_id")
            if not pid:
                continue
            prev = latest.get(pid)
            if prev is None or w > prev[0]:
                latest[pid] = (w, r)

    team_of, team_pos_roster, draft_pick_of, status_of = {}, {}, {}, {}
    for pid, (_w, r) in latest.items():
        status = r.get("status")
        status_of[pid] = status
        if status not in CONFIRMED_ROSTER_STATUSES:
            continue
        team_of[pid] = r["team"]
        team_pos_roster.setdefault((r["team"], r["position"]), []).append(pid)
        if r.get("rookie_year") == str(season) and r.get("draft_number"):
            draft_pick_of[pid] = int(r["draft_number"])
    return team_of, team_pos_roster, draft_pick_of, status_of


STALE_ROSTER_DAYS = 3


def check_roster_staleness(path, season, week):
    """Loud, hard-to-miss warning if roster_weekly_<season>.csv looks
    stale -- either the file on disk hasn't been refreshed recently, or it
    simply has no rows anywhere near the week being scored. A quiet stale
    file is exactly how the team-changed/new-competitor checks ran on a
    two-week-old snapshot unnoticed (see CLAUDE.md) -- this makes it
    impossible to miss on the console, without refusing to run."""
    if not os.path.exists(path):
        return

    age_days = (time.time() - os.path.getmtime(path)) / 86400
    max_week_in_file = 0
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("season") != str(season):
                continue
            try:
                w = int(r.get("week") or 0)
            except ValueError:
                continue
            max_week_in_file = max(max_week_in_file, w)

    problems = []
    if age_days > STALE_ROSTER_DAYS:
        problems.append(f"file on disk is {age_days:.1f} days old (> {STALE_ROSTER_DAYS}-day threshold)")
    if max_week_in_file < week - 1:
        problems.append(f"newest rows in the file are for week {max_week_in_file}, but scoring week {week} "
                         f"(expected rows at least through week {week - 1})")

    if problems:
        banner = "!" * 78
        print(f"\n{banner}")
        print(f"[STALE ROSTER WARNING] {path}")
        for p in problems:
            print(f"  - {p}")
        print("  Team-changed / new-competitor / availability suppressions below may be WRONG.")
        print(f"  Fix: refresh with data/fetch_weekly_update.py --season {season} before trusting this output.")
        print(f"{banner}\n")


def load_and_score(model_path, label, stable):
    """Loads a model bundle, scores the shared `stable` candidate pool with
    it, and returns (model_commit, model_meta, feature_cols, proba, ranked)
    -- or None if model_path doesn't exist (used for an optional shadow
    model). `label` is only for console messages, e.g. "PRODUCTION" or
    "SHADOW"."""
    if not os.path.exists(model_path):
        return None

    bundle = joblib.load(model_path)
    model = bundle["model"]
    feature_cols = bundle["feature_cols"]
    # The saved model may use any subset of PERSISTED_FEATURE_COLS (e.g. the
    # production model trains on features_lib.PRODUCTION_FEATURE_COLS, a
    # strict subset that excludes the rejected share_delta_vs_prior_season --
    # see features_lib.py for why) -- just check it's a real subset, not an
    # exact match.
    unknown = set(feature_cols) - set(PERSISTED_FEATURE_COLS)
    assert not unknown, f"saved {label} model uses unknown feature columns: {unknown}"
    print(f"[INFO] {label} model ({Path(model_path).name}) uses {len(feature_cols)} feature(s): {feature_cols}")

    model_meta = read_metadata(model_path)
    model_commit = model_meta.get("git_commit") if model_meta else None
    print(f"[INFO] {label} model identity: {Path(model_path).name}"
          + (f" (git_commit={model_commit})" if model_commit else " (no metadata sidecar found -- commit hash unknown)"))

    X = np.array([[c[1][col] if c[1][col] is not None else np.nan for col in feature_cols] for c in stable])
    proba = model.predict_proba(X)[:, 1] if len(stable) else np.array([])
    return model_commit, model_meta, feature_cols, proba


def print_top_table(label, ranked, args, names):
    print(f"\n[{label}] Top {len(ranked)} by predicted spike probability, {args.season} week {args.week} "
          f"(stable candidates only, both flags clear):")
    header = f"{'Name':24} {'Pos':4} {'Team':5} {'Score':>7}"
    print(header)
    print("-" * len(header))
    for (pid, feat), score in ranked:
        display = names.get(pid, pid)
        print(f"{display:24.24} {feat['_pos']:4} {feat['_team']:5} {score:7.4f}")


def write_output(model_path, model_commit, model_meta, args, names, ranked, stable, proba,
                  all_eligible_count, n_candidates, n_released_by_recency,
                  team_changed_records, new_competitor_records, unavailable_records,
                  n_via_production_only, n_via_draft_only, n_via_both, json_out_path):
    def player_record(pid, feat, score=None):
        return {
            "player_id": pid, "name": names.get(pid, pid),
            "pos": feat["_pos"], "team": feat["_team"],
            "score": float(score) if score is not None else None,
        }

    ranked_records = []
    for rank, ((pid, feat), score) in enumerate(ranked, start=1):
        rec = player_record(pid, feat, score)
        rec["rank"] = rank
        ranked_records.append(rec)

    scored_pool_records = [
        player_record(pid, feat, s)
        for (pid, feat), s in sorted(zip(stable, proba), key=lambda x: x[1], reverse=True)
    ]

    output = {
        "season": args.season,
        "week": args.week,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {
            "path": str(Path(model_path).resolve()),
            "filename": Path(model_path).name,
            "git_commit": model_commit,
            "role": model_meta.get("role") if model_meta else None,
        },
        "counts": {
            "total_eligible": all_eligible_count,
            "after_position_filter": n_candidates,
            "stable_scored": len(stable),
            "released_from_offseason_suppression": n_released_by_recency,
            "unavailable_suppressed": len(unavailable_records),
            "team_changed_suppressed": len(team_changed_records),
            "new_competitor_suppressed": len(new_competitor_records),
            "new_competitor_via_production_only": n_via_production_only,
            "new_competitor_via_draft_only": n_via_draft_only,
            "new_competitor_via_both": n_via_both,
        },
        "top": ranked_records,
        "scored_pool": scored_pool_records,
        "unavailable_watch_list": unavailable_records,
        "team_changed_watch_list": team_changed_records,
        "new_competitor_watch_list": new_competitor_records,
    }

    json_out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"[SAVED] scoring output written to {json_out_path}")

    md_out_path = json_out_path.with_suffix(".md")
    md_out_path.write_text(render_markdown_report(output), encoding="utf-8")
    print(f"[SAVED] markdown report written to {md_out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO_ROOT / "faab_history_core_v0_1.db"))
    ap.add_argument("--model", default=str(REPO_ROOT / "odds_xgb_model_production.joblib"))
    ap.add_argument("--shadow-model", default=str(REPO_ROOT / "odds_xgb_model_production_10feature.joblib"),
                     help="also score the same candidate pool with this model, for a head-to-head "
                          "tracked in verify_week.py; skipped with a warning if the file isn't present locally")
    ap.add_argument("--no-shadow", action="store_true", help="skip shadow scoring even if --shadow-model exists")
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--weekly-roster", default=None,
                     help="defaults to nflverse_raw/roster_weekly_<season>.csv if present; single source for "
                          "team-mapping, new-competitor detection, AND the availability gate")
    ap.add_argument("--json-out", default=None, help="if set, write the full scoring output (top N, scored pool, both watch lists, model identity) as JSON to this path, and a matching shadow file (same basename + _shadow) if shadow scoring runs")
    args = ap.parse_args()

    weekly_roster_path = args.weekly_roster or str(REPO_ROOT / "nflverse_raw" / f"roster_weekly_{args.season}.csv")
    roster_team_of, team_pos_roster, draft_pick_of, weekly_status_of = {}, {}, {}, {}
    if os.path.exists(weekly_roster_path):
        check_roster_staleness(weekly_roster_path, args.season, args.week)
        roster_team_of, team_pos_roster, draft_pick_of, weekly_status_of = load_current_roster(
            weekly_roster_path, args.season, args.week
        )
        n_flagged_unavailable = sum(1 for s in weekly_status_of.values() if s in UNAVAILABLE_STATUSES)
        print(f"[INFO] cross-referencing against {weekly_roster_path}: {len(roster_team_of)} players with a "
              f"confirmed current team, {len(draft_pick_of)} rookies with known {args.season}-draft pick numbers, "
              f"{n_flagged_unavailable} flagged long-term unavailable")
    else:
        print(f"[INFO] no weekly roster file at {weekly_roster_path} -- skipping offseason team-change / "
              f"new-competitor cross-checks and the availability gate")

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    engine = FeatureEngine(con)

    names = {row["player_id"]: row["full_name"] for row in con.execute("SELECT player_id, full_name FROM ref_players")}

    already_played = bool(engine.played_this_week.get((args.season, args.week)))
    print(f"[INFO] {args.season} week {args.week}: "
          f"{'ALREADY HAS stat data (historical/backtest mode)' if already_played else 'no stat data yet (genuine future-week mode)'}")

    candidates = get_eligible_candidates(engine, args.season, args.week)
    all_eligible_count = len(candidates)
    candidates = [(pid, feat) for pid, feat in candidates if feat["_pos"] in ROSTERABLE_POSITIONS]
    print(f"[INFO] {all_eligible_count} eligible players for {args.season} week {args.week}; "
          f"{len(candidates)} after restricting to {sorted(ROSTERABLE_POSITIONS)}")

    if not candidates:
        print("[DONE] no eligible players -- nothing to score.")
        con.close()
        return

    # A player is "on team T's 2025 roster" if they have a real 2025 game for
    # T -- used to detect a same-position teammate who is genuinely new to
    # the team this offseason (free agent signing, trade, or rookie), not
    # just someone who happened to change teams themselves.
    prior_season = args.season - 1
    players_on_team_2025 = {}
    rb_touches_2025, wr_te_targets_2025 = {}, {}
    for pid, entries in engine.player_history.items():
        for (s, _w, tm, pos, touches) in entries:
            if s != prior_season:
                continue
            players_on_team_2025.setdefault(tm, set()).add(pid)
            if pos == "RB":
                rb_touches_2025[pid] = rb_touches_2025.get(pid, 0) + touches

    # rec_tgt (targets) isn't part of the "touches" tuple in player_history,
    # so pull it straight from player_week_stats for WR/TE.
    for pid, tgt in con.execute(
        "SELECT player_id, SUM(rec_tgt) FROM player_week_stats WHERE season=? AND pos IN ('WR','TE') GROUP BY player_id",
        (prior_season,),
    ):
        wr_te_targets_2025[pid] = tgt or 0

    # pass attempts aren't in player_week_stats at all (never needed by the
    # half12 scoring profile) -- read them from the raw fetched prior-season
    # CSV instead of extending the core schema for one threshold check.
    qb_attempts_2025 = {}
    raw_2025_path = str(REPO_ROOT / "nflverse_raw" / f"stats_player_week_{prior_season}.csv")
    if os.path.exists(raw_2025_path):
        with open(raw_2025_path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("attempts"):
                    pid2 = r["player_id"]
                    qb_attempts_2025[pid2] = qb_attempts_2025.get(pid2, 0) + int(r["attempts"])

    def meaningful_threat(pid2, pos2):
        """Returns (is_threat, via_production, via_draft)."""
        prod_bar = PROD_THRESHOLDS.get(pos2)
        if pos2 == "RB":
            prod_val = rb_touches_2025.get(pid2, 0)
        elif pos2 in ("WR", "TE"):
            prod_val = wr_te_targets_2025.get(pid2, 0)
        elif pos2 == "QB":
            prod_val = qb_attempts_2025.get(pid2, 0)
        else:
            prod_val = 0
        via_production = prod_bar is not None and prod_val >= prod_bar
        pick = draft_pick_of.get(pid2)
        via_draft = pick is not None and pick <= DRAFT_CAPITAL_PICK_CUTOFF
        return (via_production or via_draft), via_production, via_draft

    stable, team_changed_list, new_competitor_list, unavailable_list = [], [], [], []
    n_via_production_only = n_via_draft_only = n_via_both = 0
    n_released_by_recency = 0
    for pid, feat in candidates:
        # Availability gate first, unconditionally -- ahead of even the
        # recency-release below. A player on reserve who happens to have
        # accumulated 3+ real current-season games (hurt mid-season, say)
        # still can't play; games_played_this_season says nothing about
        # whether he's currently allowed on the field.
        roster_status = weekly_status_of.get(pid)
        if roster_status in UNAVAILABLE_STATUSES:
            unavailable_list.append((pid, feat, roster_status))
            continue

        # Release both offseason suppressions once this player's trailing
        # window is built entirely from current-season games -- see the
        # module docstring. games_played_this_season already counts real
        # games strictly before this week, so >= MIN_PRIOR_GAMES here means
        # the last MIN_PRIOR_GAMES window (what every trailing feature
        # actually uses) can no longer reach back into stale prior-season
        # data, independent of what the static roster/production checks
        # below would otherwise conclude.
        if feat["games_played_this_season"] >= MIN_PRIOR_GAMES:
            stable.append((pid, feat))
            n_released_by_recency += 1
            continue

        inferred_team = feat["_team"]
        roster_team = roster_team_of.get(pid)

        if roster_team is not None and roster_team != inferred_team:
            corrected = engine.compute_schedule_dependent_features(args.season, args.week, roster_team, feat["_pos"], pid)
            team_changed_list.append((pid, feat, inferred_team, roster_team, corrected))
            continue

        team = roster_team or inferred_team
        pos = feat["_pos"]
        new_arrivals = [
            p2 for p2 in team_pos_roster.get((team, pos), [])
            if p2 != pid and p2 not in players_on_team_2025.get(team, set())
        ]

        meaningful, any_prod, any_draft = [], False, False
        for p2 in new_arrivals:
            is_threat, via_prod, via_draft = meaningful_threat(p2, pos)
            if is_threat:
                meaningful.append(p2)
                any_prod = any_prod or via_prod
                any_draft = any_draft or via_draft

        if meaningful:
            new_competitor_list.append((pid, feat, team, meaningful))
            if any_prod and any_draft:
                n_via_both += 1
            elif any_prod:
                n_via_production_only += 1
            else:
                n_via_draft_only += 1
        else:
            stable.append((pid, feat))

    print(f"[INFO] {len(stable)} stable candidates scored ({n_released_by_recency} of those released from "
          f"offseason suppression this week, having reached {MIN_PRIOR_GAMES}+ current-season games); "
          f"{len(unavailable_list)} unavailable (reserve/PUP/NFI/suspended, score suppressed); "
          f"{len(team_changed_list)} team-changed (score suppressed); "
          f"{len(new_competitor_list)} with a meaningful new same-position competitor this offseason (score suppressed)")
    print(f"[INFO]   of those {len(new_competitor_list)}: {n_via_production_only} via prior-production threshold only, "
          f"{n_via_draft_only} via draft-capital only, {n_via_both} via both")

    if unavailable_list:
        print(f"\nWatch list -- {len(unavailable_list)} players known long-term unavailable, score suppressed ({UNAVAILABLE_REASON}):")
        header1 = f"{'Name':24} {'Pos':4} {'Team':5} {'Status'}"
        print(header1)
        print("-" * len(header1))
        for pid, feat, status in sorted(unavailable_list, key=lambda w: (w[1]["_team"], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            print(f"{display:24.24} {feat['_pos']:4} {feat['_team']:5} {status}")

    if team_changed_list:
        print(f"\nWatch list -- {len(team_changed_list)} players with a confirmed offseason team change, score suppressed ({TEAM_CHANGE_REASON}):")
        header2 = f"{'Name':24} {'Pos':4} {'Old':4} {'New':4} {'Opp':4} {'Home':5} {'ShortWk':7} {'StarterAbsent':13} {'Matchup':>8}"
        print(header2)
        print("-" * len(header2))
        for pid, feat, old_team, new_team, corrected in sorted(team_changed_list, key=lambda w: (w[3], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            opp = corrected["_opp"] or "?"
            home = corrected["is_home"]
            sw = corrected["is_short_week"]
            sap = corrected["starter_absent_proxy"]
            matchup = corrected["opponent_position_matchup"]
            matchup_s = f"{matchup:.2f}" if matchup is not None else "None"
            print(f"{display:24.24} {feat['_pos']:4} {old_team:4} {new_team:4} {opp:4} {str(home):5} {str(sw):7} {str(sap):13} {matchup_s:>8}")

    if new_competitor_list:
        print(f"\nWatch list -- {len(new_competitor_list)} players with a meaningful new same-position competitor this offseason, score suppressed ({NEW_COMPETITOR_REASON}):")
        header3 = f"{'Name':24} {'Pos':4} {'Team':5} {'New competitor(s) [why]'}"
        print(header3)
        print("-" * len(header3))
        for pid, feat, team, competitors in sorted(new_competitor_list, key=lambda w: (w[2], names.get(w[0], w[0]))):
            display = names.get(pid, pid)
            comp_strs = []
            for c in competitors:
                _, via_prod, via_draft = meaningful_threat(c, feat["_pos"])
                why = "prod+draft" if (via_prod and via_draft) else ("draft" if via_draft else "prod")
                comp_strs.append(f"{names.get(c, c)} [{why}]")
            print(f"{display:24.24} {feat['_pos']:4} {team:5} {', '.join(comp_strs)}")

    # Watch-list JSON records are model-independent (same suppression logic
    # regardless of which model scores the stable pool) -- built once, reused
    # for both the production and shadow output files.
    unavailable_records = []
    for pid, feat, status in unavailable_list:
        unavailable_records.append({
            "player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"], "team": feat["_team"],
            "status": status, "reason": UNAVAILABLE_REASON,
        })

    team_changed_records = []
    for pid, feat, old_team, new_team, corrected in team_changed_list:
        team_changed_records.append({
            "player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"],
            "old_team": old_team, "new_team": new_team,
            "reason": TEAM_CHANGE_REASON,
            "corrected_features": {
                "opponent": corrected["_opp"], "is_home": corrected["is_home"],
                "is_short_week": corrected["is_short_week"],
                "starter_absent_proxy": corrected["starter_absent_proxy"],
                "opponent_position_matchup": corrected["opponent_position_matchup"],
            },
        })

    new_competitor_records = []
    for pid, feat, team, competitors in new_competitor_list:
        comp_details = []
        for c in competitors:
            _, via_prod, via_draft = meaningful_threat(c, feat["_pos"])
            comp_details.append({
                "player_id": c, "name": names.get(c, c),
                "via_production": via_prod, "via_draft_capital": via_draft,
            })
        new_competitor_records.append({
            "player_id": pid, "name": names.get(pid, pid), "pos": feat["_pos"], "team": team,
            "reason": NEW_COMPETITOR_REASON,
            "new_competitors": comp_details,
        })

    def run_pass(model_path, label, json_out_path):
        result = load_and_score(model_path, label, stable)
        if result is None:
            print(f"[WARN] {label} model not found at {model_path} -- skipping {label.lower()} scoring")
            return
        model_commit, model_meta, feature_cols, proba = result
        ranked = sorted(zip(stable, proba), key=lambda x: x[1], reverse=True)[: args.top]
        print_top_table(label, ranked, args, names)
        if json_out_path is not None:
            write_output(model_path, model_commit, model_meta, args, names, ranked, stable, proba,
                         all_eligible_count, len(candidates), n_released_by_recency,
                         team_changed_records, new_competitor_records, unavailable_records,
                         n_via_production_only, n_via_draft_only, n_via_both, json_out_path)

    json_out_path = Path(args.json_out) if args.json_out else None
    run_pass(args.model, "PRODUCTION", json_out_path)

    if not args.no_shadow:
        shadow_json_out = json_out_path.with_name(json_out_path.stem + "_shadow" + json_out_path.suffix) if json_out_path else None
        run_pass(args.shadow_model, "SHADOW", shadow_json_out)

    con.close()


if __name__ == "__main__":
    main()
