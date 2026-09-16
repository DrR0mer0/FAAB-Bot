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
    precision@k is compared against. Always population-wide: callers
    that want this measured against the model's own scored/candidate
    pool rather than every eligible player should filter df themselves
    before calling.

    group_col=None -> {"n_eligible":, "n_spiked":, "rate":}.
    group_col="pos" -> {group_value: {...}}.
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
    rate and lift. Same precision_df/base_rate_df split as pooled_report,
    and the same reasoning for it. Defaults to the SUMMED hits/n variant
    (use_mean=False) rather than pooled_report's mean-of-weeks default,
    since a per-position pool can be much smaller in a given week (e.g. a
    bye-heavy week with few eligible TEs), where summed hits/n is more
    robust than giving every week equal weight regardless of its size."""
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
