#!/usr/bin/env python3
"""Shared evaluation metrics -- pooled AND per-position precision@k, plus
base rates and lift -- used identically by evaluation/verify_week.py and
the feature-group hypothesis tests (test_*.py), so a reported number means
the same thing everywhere it's printed or logged.

Every function takes a pandas DataFrame with at least these columns:
  season, week, pos, proba, spike_flag
'proba' is the model's ranking score for that row; 'spike_flag' is 0/1,
already resolved by the caller (a DNP or no-label row should be passed in
as spike_flag=0 -- counted as a non-hit, never dropped from the
denominator, matching verify_week.py's existing policy).

DENOMINATORS -- read this before comparing a pooled number to a
per-position one:

1. Per-position base rate is computed WITHIN that position's own rows --
   base_rate(df, group_col="pos")["QB"] is the spike rate among QB rows
   only, out of whatever population df represents. This is a DIFFERENT,
   smaller denominator than the pooled base rate (spike rate across every
   position's rows mixed together). A per-position rate should only be
   compared against that SAME position's own precision (for a
   within-position lift), never directly against the pooled base rate or
   against another position's rate as if they shared one scale.

2. Separately, WHICH rows populate df in the first place can differ by
   caller, and that also affects comparability. The feature-group
   hypothesis tests pass the same population to both precision_df and
   base_rate_df (every eligible, labeled player-week in the test set) --
   there, "candidate pool" and "all eligible players" are the same thing.
   verify_week.py does NOT: its precision_df is the model's own scored
   pool (stable, rosterable candidates only -- already excludes players
   suppressed for an offseason team change or a new competitor, and
   non-QB/RB/WR/TE positions), while its base_rate_df is every eligible,
   labeled player that week regardless of position or suppression status
   -- deliberately population-wide, so the base rate answers "how rare is
   a spike, period" rather than "how rare is a spike among players the
   model was willing to score." Don't assume every caller's precision_df
   and base_rate_df are the same population just because this module lets
   you pass either.
"""
POSITIONS = ("QB", "RB", "WR", "TE")
DEFAULT_PER_POSITION_K = 10


def precision_at_k(df, k, group_col=None):
    """Top-k by 'proba' within each (season, week[, group_col]) group,
    hits and n SUMMED across all such groups (not averaged) -- robust
    when per-week pool size varies, which matters most for a
    smaller-population per-position breakdown.

    group_col=None -> a single (hits, n, precision) tuple.
    group_col="pos" -> {group_value: (hits, n, precision)}.
    """
    def _one(d):
        hits, n = 0, 0
        for _, grp in d.groupby(["season", "week"]):
            top = grp.sort_values("proba", ascending=False).head(k)
            hits += int(top["spike_flag"].sum())
            n += len(top)
        return hits, n, (hits / n if n else None)

    if group_col is None:
        return _one(df)
    return {gval: _one(gdf) for gval, gdf in df.groupby(group_col)}


def mean_precision_at_k(df, k, group_col=None):
    """Same top-k selection as precision_at_k, but each (season, week[,
    group_col]) group's own precision is averaged UNWEIGHTED across
    groups, rather than summed -- the convention already established by
    the Group 1/2 hypothesis tests for the pooled (position-agnostic)
    metric across many weeks. Kept alongside precision_at_k rather than
    replacing it: which one to headline is a per-caller choice, not
    something this module should force.

    group_col=None -> a single float (or None if df is empty).
    group_col="pos" -> {group_value: float_or_None}.
    """
    def _one(d):
        precs = []
        for _, grp in d.groupby(["season", "week"]):
            top = grp.sort_values("proba", ascending=False).head(k)
            precs.append(top["spike_flag"].sum() / len(top))
        return (sum(precs) / len(precs)) if precs else None

    if group_col is None:
        return _one(df)
    return {gval: _one(gdf) for gval, gdf in df.groupby(group_col)}


def base_rate(df, group_col=None):
    """Spike rate across df AS A WHOLE (not top-k) -- the reference point
    precision@k is compared against. Computed over whatever population df
    represents -- this function doesn't know or care whether that's every
    eligible player or a narrower candidate pool; see the module
    docstring's "DENOMINATORS" section for what that distinction means in
    practice and why a per-position rate isn't directly comparable to the
    pooled one.

    group_col=None -> {"n_eligible":, "n_spiked":, "rate":}.
    group_col="pos" -> {group_value: {...}}, each computed WITHIN that
    position's own rows of df, not across all positions combined.
    """
    def _one(d):
        n = len(d)
        n_spiked = int(d["spike_flag"].sum())
        return {"n_eligible": n, "n_spiked": n_spiked, "rate": (n_spiked / n if n else None)}

    if group_col is None:
        return _one(df)
    return {gval: _one(gdf) for gval, gdf in df.groupby(group_col)}


def lift(precision, rate):
    """precision as a multiple of the base rate, or None if either side
    is unavailable (no base rate, or no hits/n)."""
    return (precision / rate) if (precision is not None and rate) else None


def pooled_report(precision_df, base_rate_df, ks=(10, 25), use_mean=True):
    """Pooled (position-agnostic) precision@k for each k in ks, plus base
    rate and lift. precision_df and base_rate_df may be the same
    DataFrame (a hypothesis test's full population) or different ones
    (verify_week.py: precision from the model's own committed top-N list,
    base rate from every eligible labeled player that week, regardless of
    whether the model actually scored them)."""
    br = base_rate(base_rate_df)
    out = {}
    for k in ks:
        if use_mean:
            mean_p = mean_precision_at_k(precision_df, k)
            hits, n, summed_p = precision_at_k(precision_df, k)
            precision = mean_p
        else:
            hits, n, precision = precision_at_k(precision_df, k)
        out[k] = {
            "hits": hits, "n": n, "precision": precision,
            "base_rate": br["rate"], "n_eligible": br["n_eligible"], "n_spiked": br["n_spiked"],
            "lift": lift(precision, br["rate"]),
        }
    return out


def per_position_report(precision_df, base_rate_df, k=DEFAULT_PER_POSITION_K,
                         positions=POSITIONS, use_mean=False):
    """Precision@k computed WITHIN each position (top-k among that
    position's own candidates each week) plus that position's own base
    rate (also computed within that position -- see base_rate) and lift.
    Same precision_df/base_rate_df split as pooled_report, and the same
    reasoning for it. Defaults to the SUMMED hits/n variant (use_mean=
    False) rather than pooled_report's mean-of-weeks default, since a
    per-position pool can be much smaller in a given week (e.g. a
    bye-heavy week with few eligible TEs), where summed hits/n is more
    robust than giving every week equal weight regardless of its size.

    Each position's precision/base_rate/lift are only meaningful compared
    to EACH OTHER (that position's own numbers) -- not to the pooled
    report's numbers, and not to another position's. See the module
    docstring's "DENOMINATORS" section."""
    prec_fn = mean_precision_at_k if use_mean else precision_at_k
    prec_by_pos = prec_fn(precision_df, k, group_col="pos")
    br_by_pos = base_rate(base_rate_df, group_col="pos")

    out = {}
    for pos in positions:
        result = prec_by_pos.get(pos)
        if use_mean:
            hits, n, precision = None, None, result
        else:
            hits, n, precision = result if result is not None else (0, 0, None)
        b = br_by_pos.get(pos, {"n_eligible": 0, "n_spiked": 0, "rate": None})
        out[pos] = {
            "k": k, "hits": hits, "n": n, "precision": precision,
            "base_rate": b["rate"], "n_eligible": b["n_eligible"], "n_spiked": b["n_spiked"],
            "lift": lift(precision, b["rate"]),
        }
    return out
