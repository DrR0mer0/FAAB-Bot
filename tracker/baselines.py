#!/usr/bin/env python3
"""The "dumb" baselines O.D.D.S. is measured against.

All three draw from EXACTLY the same pool O.D.D.S. scored for that run (its
stable scored pool) and pick the same number of players with the same
position mix as O.D.D.S.'s production picks in that pick set:

  dart       Monte Carlo: uniformly random players, per position, N draws.
             Only a seed + the pool snapshot are logged; the whole N-draw
             distribution is regenerated at scoring time. Sampling uses only
             Generator.random() (argsort of uniform keys) rather than
             choice()/permutation(), so the stream depends on the PCG64 bit
             generator, not on which sampling algorithm a given numpy
             version picks.
  heuristic  Rank by snap-share increase: mean offense snap % over the last
             2 games played minus the mean over the 2 before that. Ties
             broken by mean target share over the same last 2 games, then
             player_id (so every run is deterministic). Players without 4
             prior games on record have no defined delta and rank last.
  last_week  Rank by half-PPR points (league scoring) in the player's most
             recent game from a prior week. No prior game ranks last.

Only games strictly before the target week are ever used, so none of this
can leak the week being predicted.
"""
import hashlib
from collections import Counter

import numpy as np

HEURISTIC_RECENT_GAMES = 2
HEURISTIC_PRIOR_GAMES = 2


def derive_seed(season, week, slot):
    digest = hashlib.sha256(f"fm-tracker-dart|{season}|{week}|{slot}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def position_mix(picks):
    """picks: iterable of dicts with 'position' -> {position: count}, sorted."""
    return dict(sorted(Counter(p["position"] for p in picks).items()))


def select_matched(ranked_pool, mix):
    """ranked_pool: list of dicts (best first) with 'player_id','position'.
    Takes the best mix[pos] players at each position, then returns them in
    their pooled rank order. Returns (picks, shortfall) where shortfall is
    {pos: missing} for positions with fewer players available than asked."""
    taken, picks = Counter(), []
    for r in ranked_pool:
        pos = r["position"]
        if taken[pos] < mix.get(pos, 0):
            taken[pos] += 1
            picks.append(r)
    shortfall = {pos: n - taken[pos] for pos, n in mix.items() if taken[pos] < n}
    return picks, shortfall


def rank_heuristic(pool):
    """pool rows need snap_delta (None ok), target_share (None ok), player_id."""
    return sorted(pool, key=lambda r: (
        r.get("snap_delta") is None,
        -(r.get("snap_delta") or 0.0),
        -(r.get("target_share") or 0.0),
        r["player_id"],
    ))


def rank_last_week(pool):
    """pool rows need last_week_points (None ok), player_id."""
    return sorted(pool, key=lambda r: (
        r.get("last_week_points") is None,
        -(r.get("last_week_points") or 0.0),
        r["player_id"],
    ))


def dart_draws(sets, seed, n_draws):
    """sets: {pick_set: (pool, mix)} with pool a list of dicts having
    player_id/position, mix {position: n}. Yields n_draws dicts
    {pick_set: [player_id, ...]}. Deterministic in (sets, seed, n_draws):
    pools are sorted by player_id and pick sets/positions visited in sorted
    order, so the same inputs always consume the random stream identically."""
    rng = np.random.default_rng(seed)
    prepared = {}
    for ps in sorted(sets):
        pool, mix = sets[ps]
        by_pos = {}
        for r in sorted(pool, key=lambda r: r["player_id"]):
            by_pos.setdefault(r["position"], []).append(r["player_id"])
        prepared[ps] = (by_pos, dict(sorted(mix.items())))
    for _ in range(n_draws):
        draw = {}
        for ps in sorted(prepared):
            by_pos, mix = prepared[ps]
            picks = []
            for pos, n in mix.items():
                ids = by_pos.get(pos, [])
                k = min(n, len(ids))
                if k <= 0:
                    continue
                keys = rng.random(len(ids))
                order = np.argsort(keys, kind="stable")[:k]
                picks.extend(ids[i] for i in order)
            draw[ps] = picks
        yield draw


def percentile_midrank(value, samples):
    """Where `value` falls in `samples`, 0-100: strictly-below plus half of
    ties ("beat 85% of dart throws")."""
    samples = np.asarray(samples, dtype=float)
    if samples.size == 0:
        return None
    below = float((samples < value - 1e-9).sum())
    tied = float((np.abs(samples - value) <= 1e-9).sum())
    return 100.0 * (below + 0.5 * tied) / samples.size


# ---- features for the data-driven baselines (DB / stats dependent) ----

def snap_features(con, player_ids, season, week):
    """{player_id: {snap_recent, snap_prior, snap_delta, snap_games}} from
    tr_snap_counts, using the player's last 4 games strictly before
    (season, week) -- 2 most recent vs the 2 before."""
    need = HEURISTIC_RECENT_GAMES + HEURISTIC_PRIOR_GAMES
    out = {}
    for pid in player_ids:
        rows = con.execute(
            "SELECT offense_pct FROM tr_snap_counts WHERE player_id=? "
            "AND (season < ? OR (season = ? AND week < ?)) AND offense_pct IS NOT NULL "
            "ORDER BY season DESC, week DESC LIMIT ?",
            (pid, season, season, week, need),
        ).fetchall()
        pcts = [r[0] for r in rows]  # most recent first
        rec = {"snap_recent": None, "snap_prior": None, "snap_delta": None, "snap_games": len(pcts)}
        if len(pcts) >= need:
            recent = sum(pcts[:HEURISTIC_RECENT_GAMES]) / HEURISTIC_RECENT_GAMES
            prior = sum(pcts[HEURISTIC_RECENT_GAMES:need]) / HEURISTIC_PRIOR_GAMES
            rec.update(snap_recent=recent, snap_prior=prior, snap_delta=recent - prior)
        out[pid] = rec
    return out


def latest_snap_week(con, season, week):
    """(season, week) of the newest snap-count game strictly before the
    target week, or None -- recorded per run so a lagging snap feed is
    visible in the ledger."""
    r = con.execute(
        "SELECT season, week FROM tr_snap_counts WHERE (season < ? OR (season = ? AND week < ?)) "
        "ORDER BY season DESC, week DESC LIMIT 1", (season, season, week)).fetchone()
    return (r[0], r[1]) if r else None


def last_week_and_target_share(store, player_ids, season, week):
    """{player_id: {last_week_points, last_week_label, target_share}}: points
    in the most recent game from a PRIOR week (league scoring), and mean
    target share over the player's last HEURISTIC_RECENT_GAMES games."""
    out = {}
    for pid in player_ids:
        hist = store.history_before(pid, season, week, HEURISTIC_RECENT_GAMES)
        if hist:
            s, w, pts, _ts = hist[-1]
            shares = [g[3] for g in hist if g[3] is not None]
            out[pid] = {"last_week_points": pts, "last_week_label": f"{s}w{w:02d}",
                        "target_share": (sum(shares) / len(shares)) if shares else None}
        else:
            out[pid] = {"last_week_points": None, "last_week_label": None, "target_share": None}
    return out
