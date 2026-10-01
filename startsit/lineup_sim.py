#!/usr/bin/env python3
"""FAAB start/sit simulator.

Turns per-player weekly point distributions (floor / median / ceiling) into
the lineup with the best chance of beating this week's opponent, and explains
the close calls.

The only input is a "week file" (JSON). Whatever produces it -- the Yahoo
fetcher, the projection models, or a hand-written file -- just has to follow
the contract in README.md. See example_week.json.

    python startsit/lineup_sim.py startsit/example_week.json
    python startsit/lineup_sim.py league_data/2026_week05.json --seed 7 --json league_data/out.json --md league_data/out.md

From Python (with startsit/ on the path):

    from lineup_sim import run_week
    result = run_week(week_dict, n_sims=20000, seed=7)
    print(result["report_md"])

Only dependency: numpy.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np

SCHEMA_VERSION = 1
DEFAULT_SIMS = 20_000
MIN_POINTS = -2.0  # lowest a skill player realistically scores (fumbles, picks)

# Rough half-PPR spreads (p10, p90 as multiples of the median). Used ONLY when a
# player arrives with a median but no quantiles -- e.g. plugging in Yahoo's point
# projections before FAAB's own quantile models exist. Replace with fitted values.
DEFAULT_SPREADS = {
    "QB": (0.55, 1.45),
    "RB": (0.35, 1.80),
    "WR": (0.30, 1.90),
    "TE": (0.25, 2.00),
    "K": (0.45, 1.60),
    "DEF": (0.20, 1.90),
}

# Statuses that mean the player will not play this week.
OUT_STATUSES = {"O", "OUT", "IR", "IR-R", "SUSP", "PUP", "PUP-R", "NFI", "NFI-R", "BYE", "EXEMPT", "CEL"}

# Default chance to play for game-time designations, used only when the week
# file gives a status but no play_prob. Rough priors; override per player.
STATUS_PLAY_PROB = {"Q": 0.80, "QUESTIONABLE": 0.80, "GTD": 0.70, "D": 0.30, "DOUBTFUL": 0.30}

# Roster slots that never score.
BENCH_SLOTS = {"BN", "BENCH", "IR", "IR+", "NA"}

# Yahoo-style flex shorthand: "W/R/T" -> WR, RB, TE; "Q/W/R/T" -> superflex.
FLEX_LETTERS = {"Q": "QB", "W": "WR", "R": "RB", "T": "TE"}

DEFAULT_SLOTS_SPEC = ["QB", "WR", "WR", "RB", "RB", "TE", "W/R/T"]


def _pairs(table: dict) -> dict:
    """Make position-pair lookups order-free."""
    return {tuple(sorted(k)): v for k, v in table.items()}


# Correlation of weekly fantasy output between two players on the SAME NFL team.
SAME_TEAM_CORR = _pairs({
    ("QB", "WR"): 0.35, ("QB", "TE"): 0.25, ("QB", "RB"): 0.10, ("QB", "K"): 0.15,
    ("WR", "WR"): -0.05, ("WR", "TE"): -0.05, ("RB", "RB"): -0.25,
    ("RB", "K"): 0.10, ("WR", "K"): 0.05, ("TE", "K"): 0.05,
    ("DEF", "RB"): 0.10, ("DEF", "K"): 0.05,
})
# Correlation between players on OPPOSING teams in the same NFL game
# (shootouts lift both offenses; a defense moves against the other offense).
OPP_TEAM_CORR = _pairs({
    ("QB", "QB"): 0.20, ("QB", "WR"): 0.12, ("QB", "TE"): 0.08,
    ("WR", "WR"): 0.08, ("WR", "TE"): 0.05, ("RB", "RB"): -0.05,
    ("DEF", "QB"): -0.35, ("DEF", "WR"): -0.15, ("DEF", "TE"): -0.10,
    ("DEF", "RB"): -0.10, ("DEF", "K"): -0.15,
})


class WeekFileError(ValueError):
    """The week file breaks the input contract. .problems lists every issue found."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Week file problems:\n  - " + "\n  - ".join(problems))


@dataclass(frozen=True)
class Slot:
    name: str
    eligible: tuple[str, ...]


@dataclass
class Player:
    id: str
    name: str
    pos: str
    team: Optional[str]
    opp: Optional[str]
    p10: float
    p50: float
    p90: float
    play_prob: float
    status: str
    locked: bool
    started: bool
    locked_slot: Optional[str]
    actual: Optional[float]
    note: str
    assumed_spread: bool
    side: str  # "me" or "opp"


# --------------------------------------------------------------------------- #
# Parsing / validation
# --------------------------------------------------------------------------- #

def _norm_pos(pos) -> str:
    p = str(pos or "").strip().upper()
    return {"DST": "DEF", "D/ST": "DEF", "D": "DEF", "PK": "K"}.get(p, p)


def _norm_team(team) -> Optional[str]:
    t = str(team or "").strip().upper()
    return t or None


def _parse_slots(spec, problems: list[str]) -> list[Slot]:
    slots: list[Slot] = []
    for i, s in enumerate(spec):
        where = f"league.slots[{i}]"
        count = 1
        if isinstance(s, str):
            name = s.strip().upper()
            eligible = None
        elif isinstance(s, dict):
            name = str(s.get("name") or s.get("position") or "").strip().upper()
            eligible = s.get("eligible")
            count = s.get("count", 1)
            if not isinstance(count, int) or count < 0:
                problems.append(f"{where}: count must be a non-negative integer")
                continue
        else:
            problems.append(f"{where}: expected a string like 'W/R/T' or an object")
            continue
        if name in BENCH_SLOTS:
            continue
        if eligible is None:
            if not name:
                problems.append(f"{where}: needs a name or an eligible list")
                continue
            single = _norm_pos(name)  # "D/ST" is a position, not a flex
            if "/" in single:
                eligible = [FLEX_LETTERS.get(p, p) for p in single.split("/")]
            else:
                eligible = [single]
        eligible = tuple(dict.fromkeys(_norm_pos(e) for e in eligible if str(e).strip()))
        if not eligible:
            problems.append(f"{where}: eligible positions list is empty")
            continue
        slots.extend(Slot(name or "/".join(eligible), eligible) for _ in range(count))
    return slots


def _num(d: dict, key: str, where: str, problems: list[str]) -> Optional[float]:
    v = d.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        problems.append(f"{where}: {key} must be a number, got {v!r}")
        return None
    if math.isnan(f) or math.isinf(f):
        problems.append(f"{where}: {key} must be finite")
        return None
    return f


def _parse_player(d, side: str, where: str, problems: list[str]) -> Optional[Player]:
    if not isinstance(d, dict):
        problems.append(f"{where}: expected an object")
        return None
    n_before = len(problems)
    name = str(d.get("name") or "").strip()
    if not name:
        problems.append(f"{where}: missing name")
    pos = _norm_pos(d.get("pos") or d.get("position"))
    if not pos:
        problems.append(f"{where} ({name or '?'}): missing pos")
    label = f"{where} ({name or '?'})"

    status = str(d.get("status") or "").strip().upper()
    play_prob = _num(d, "play_prob", label, problems)
    if play_prob is None:
        play_prob = STATUS_PLAY_PROB.get(status, 1.0)
    elif not 0.0 <= play_prob <= 1.0:
        problems.append(f"{label}: play_prob must be between 0 and 1")
    if status in OUT_STATUSES:
        play_prob = 0.0

    actual = _num(d, "actual", label, problems)
    locked = bool(d.get("locked", actual is not None))
    started = bool(d.get("started", False))
    locked_slot = d.get("locked_slot")
    locked_slot = str(locked_slot).strip().upper() if locked_slot else None

    p50 = _num(d, "p50", label, problems)
    if p50 is None:
        p50 = _num(d, "proj", label, problems)
    p10 = _num(d, "p10", label, problems)
    p90 = _num(d, "p90", label, problems)
    assumed = False
    if p50 is None:
        if actual is not None or play_prob == 0.0:
            p50 = 0.0
            p10 = 0.0 if p10 is None else p10
            p90 = 0.0 if p90 is None else p90
        else:
            problems.append(f"{label}: needs p50 (or proj) unless it has an actual score or is out")
            p50 = 0.0
    if p10 is None or p90 is None:
        lo, hi = DEFAULT_SPREADS.get(pos, (0.35, 1.80))
        if p10 is None:
            p10 = p50 * lo
        if p90 is None:
            p90 = p50 * hi
        assumed = actual is None and play_prob > 0
    if not (p10 <= p50 <= p90):
        problems.append(f"{label}: needs p10 <= p50 <= p90, got {p10}, {p50}, {p90}")

    if len(problems) > n_before:
        return None
    return Player(
        id=str(d.get("id") or f"{side}:{name}"),
        name=name, pos=pos,
        team=_norm_team(d.get("team")), opp=_norm_team(d.get("opp")),
        p10=float(p10), p50=float(p50), p90=float(p90),
        play_prob=float(play_prob), status=status,
        locked=locked, started=started, locked_slot=locked_slot,
        actual=actual, note=str(d.get("note") or "").strip(),
        assumed_spread=assumed, side=side,
    )


def parse_week(week: dict):
    """Validate a week dict. Returns (meta, slots, my_players, opp_players, warnings).

    Raises WeekFileError listing every problem at once, so a broken fetcher
    gets one complete error report instead of a crash on the first bad field.
    """
    problems: list[str] = []
    warnings: list[str] = []
    if not isinstance(week, dict):
        raise WeekFileError(["top level must be a JSON object"])

    version = week.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        problems.append(f"schema_version {version!r} not supported (expected {SCHEMA_VERSION})")

    league = week.get("league") or {}
    slot_spec = league.get("slots")
    if not slot_spec:
        warnings.append("league.slots missing - assumed QB, 2 WR, 2 RB, TE, W/R/T flex")
        slot_spec = DEFAULT_SLOTS_SPEC
    slots = _parse_slots(slot_spec, problems)
    if not slots and not problems:
        problems.append("league.slots has no starting slots")

    my = week.get("my_team") or {}
    my_raw = my.get("players")
    if not isinstance(my_raw, list) or not my_raw:
        problems.append("my_team.players must be a non-empty list")
        my_raw = []
    my_players = [p for i, d in enumerate(my_raw)
                  if (p := _parse_player(d, "me", f"my_team.players[{i}]", problems))]

    opp = week.get("opponent") or {}
    opp_raw = opp.get("starters") or []
    if not isinstance(opp_raw, list):
        problems.append("opponent.starters must be a list")
        opp_raw = []
    opp_players = [p for i, d in enumerate(opp_raw)
                   if (p := _parse_player(d, "opp", f"opponent.starters[{i}]", problems))]
    if not opp_raw:
        warnings.append("no opponent starters - optimizing expected points instead of win probability")

    ids = [p.id for p in my_players + opp_players]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        problems.append(f"duplicate player ids: {', '.join(dupes)}")

    slot_names = {s.name for s in slots}
    slot_positions = {pos for s in slots for pos in s.eligible}
    for p in my_players:
        if p.locked_slot and p.locked_slot not in slot_names:
            problems.append(f"{p.name}: locked_slot {p.locked_slot!r} is not a starting slot")
        if p.locked and p.started and p.pos not in slot_positions:
            problems.append(f"{p.name}: started at {p.pos}, but no slot accepts {p.pos}")
        if p.locked and p.started and p.actual is None:
            warnings.append(f"{p.name} is locked into the lineup but has no actual score yet - simulating")

    if problems:
        raise WeekFileError(problems)

    assumed = [p.name for p in my_players + opp_players if p.assumed_spread]
    if assumed:
        warnings.append("floor/ceiling assumed from position defaults for: " + ", ".join(assumed))

    meta = {
        "week": week.get("week"),
        "season": week.get("season"),
        "stage": str(week.get("stage") or "").strip().lower() or None,
        "my_team_name": my.get("name") or "My team",
        "opponent_name": opp.get("name") or "Opponent",
    }
    return meta, slots, my_players, opp_players, warnings


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #

def _norm_cdf(z: np.ndarray) -> np.ndarray:
    """Standard normal CDF (Abramowitz & Stegun 7.1.26 erf, |error| < 1.5e-7)."""
    x = np.abs(z) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * x)
    poly = ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t
    erf = 1.0 - poly * np.exp(-x * x)
    return 0.5 * (1.0 + np.sign(z) * erf)


def quantile_knots(p: Player) -> tuple[np.ndarray, np.ndarray]:
    """Piecewise-linear quantile function through (p10, p50, p90).

    The tails extend past p10/p90: a short left tail floored near zero and a
    longer right tail, because fantasy points are right-skewed (boom weeks).
    """
    lo = max(p.p10 - 0.5 * (p.p50 - p.p10), MIN_POINTS)
    lo = min(lo, p.p10)
    hi = p.p90 + 0.8 * (p.p90 - p.p50)
    return np.array([0.0, 0.1, 0.5, 0.9, 1.0]), np.array([lo, p.p10, p.p50, p.p90, hi])


def pair_corr(a: Player, b: Player) -> float:
    if not a.team or not b.team:
        return 0.0
    key = tuple(sorted((a.pos, b.pos)))
    if a.team == b.team:
        return SAME_TEAM_CORR.get(key, 0.0)
    if a.opp and b.opp and a.opp == b.team and b.opp == a.team:
        return OPP_TEAM_CORR.get(key, 0.0)
    return 0.0


def correlation_matrix(players: list[Player]) -> np.ndarray:
    k = len(players)
    c = np.eye(k)
    for i in range(k):
        for j in range(i + 1, k):
            c[i, j] = c[j, i] = pair_corr(players[i], players[j])
    # Pairwise defaults can combine into an impossible matrix; nudge to the
    # nearest valid one (clip eigenvalues, restore unit diagonal).
    w, v = np.linalg.eigh(c)
    if w.min() < 1e-8:
        c = v @ np.diag(np.clip(w, 1e-6, None)) @ v.T
        d = np.sqrt(np.diag(c))
        c = c / np.outer(d, d)
    return c


def simulate(players: list[Player], n_sims: int, rng: np.random.Generator) -> np.ndarray:
    """Return an (n_sims, n_players) matrix of simulated fantasy points.

    Gaussian copula: correlated normals -> uniforms -> each player's own
    quantile function. Availability is an independent coin flip per sim;
    players with an actual score are fixed at it.
    """
    k = len(players)
    if k == 0:
        return np.zeros((n_sims, 0))
    chol = np.linalg.cholesky(correlation_matrix(players))
    u = _norm_cdf(rng.standard_normal((n_sims, k)) @ chol.T)
    pts = np.empty((n_sims, k))
    for j, p in enumerate(players):
        xs, ys = quantile_knots(p)
        pts[:, j] = np.interp(u[:, j], xs, ys)
    plays = rng.random((n_sims, k)) < np.array([p.play_prob for p in players])
    pts *= plays
    for j, p in enumerate(players):
        if p.actual is not None:
            pts[:, j] = p.actual
    return pts


# --------------------------------------------------------------------------- #
# Lineups
# --------------------------------------------------------------------------- #

def assign_slots(lineup: list[Player], slots: list[Slot]) -> Optional[dict[int, Player]]:
    """Fit players into slots, or return None if impossible.

    Players with fewer options go first, and each tries narrow slots before
    flex, so the result also reads naturally (the RB3 lands in the flex).
    """
    order = sorted(lineup, key=lambda p: (p.locked_slot is None,
                                          sum(p.pos in s.eligible for s in slots), -p.p50))
    slot_order = sorted(range(len(slots)), key=lambda i: len(slots[i].eligible))
    taken: dict[int, Player] = {}

    def place(n: int) -> bool:
        if n == len(order):
            return True
        p = order[n]
        for i in slot_order:
            if i in taken or p.pos not in slots[i].eligible:
                continue
            if p.locked_slot and slots[i].name != p.locked_slot:
                continue
            taken[i] = p
            if place(n + 1):
                return True
            del taken[i]
        return False

    return dict(taken) if place(0) else None


def _prune(pool: list[Player], slots: list[Slot], k: int, cap: int) -> list[Player]:
    """Big rosters only: per position, keep the best few by median and by ceiling."""
    if math.comb(len(pool), k) <= cap:
        return pool
    keep: list[Player] = []
    for pos in sorted({p.pos for p in pool}):
        group = [p for p in pool if p.pos == pos]
        n_keep = sum(pos in s.eligible for s in slots) + 2
        best = {p.id for p in sorted(group, key=lambda p: -p.p50)[:n_keep]}
        best |= {p.id for p in sorted(group, key=lambda p: -p.p90)[:n_keep]}
        keep.extend(p for p in group if p.id in best)
    return keep


def enumerate_lineups(my_players: list[Player], slots: list[Slot], cap: int = 200_000):
    """Every distinct legal set of starters. Returns (lineups, empty_slots).

    Respects lineup locks: players who already kicked off in your lineup must
    stay; players who kicked off on your bench can't come in. Out players are
    never started. If there aren't enough healthy players, slots stay empty.
    """
    forced = [p for p in my_players if p.locked and p.started]
    slot_positions = {pos for s in slots for pos in s.eligible}
    pool = [p for p in my_players
            if not p.locked and p.play_prob > 0 and p.pos in slot_positions]
    if assign_slots(forced, slots) is None:
        raise WeekFileError(["players locked into the lineup don't fit the starting slots"])
    for k in range(len(slots) - len(forced), -1, -1):
        cand = _prune(pool, slots, k, cap)
        found = [forced + list(c) for c in itertools.combinations(cand, k)
                 if assign_slots(forced + list(c), slots) is not None]
        if found:
            return found, len(slots) - len(forced) - k
    return [forced], len(slots) - len(forced)


# --------------------------------------------------------------------------- #
# Decision + explanation
# --------------------------------------------------------------------------- #

def _win_vec(totals: np.ndarray, opp_total: Optional[np.ndarray]) -> Optional[np.ndarray]:
    if opp_total is None:
        return None
    return (totals > opp_total) + 0.5 * (totals == opp_total)


def _driver(start: Player, bench: Player, p_win: Optional[float]) -> str:
    reasons = []
    if bench.play_prob < 0.95 and start.play_prob > bench.play_prob:
        reasons.append(f"availability ({bench.name} ~{bench.play_prob:.0%} to play)")
    if start.p50 >= bench.p50 and start.p90 >= bench.p90:
        reasons.append("higher median and ceiling")
    elif start.p50 < bench.p50 and start.p90 > bench.p90:
        reasons.append("ceiling play - as the underdog you need upside" if p_win is not None and p_win < 0.5
                       else "ceiling play")
    elif start.p50 >= bench.p50 and start.p90 < bench.p90:
        reasons.append("steadier floor - as the favorite, variance works against you"
                       if p_win is not None and p_win > 0.5 else "higher median")
    elif not reasons:
        reasons.append("fits better with the rest of your lineup (correlation)")
    if start.note:
        reasons.append(start.note)
    return "; ".join(reasons)


def _label(delta: float, se: float) -> str:
    # Under one win-percentage point is a coin flip in practice, even if "significant".
    if delta < max(2 * se, 0.01):
        return "coin flip"
    if delta < 0.03:
        return "lean"
    return "clear"


def decide(meta, slots, my_players, opp_players, n_sims=DEFAULT_SIMS, seed=None, max_close_calls=5):
    rng = np.random.default_rng(seed)
    everyone = my_players + opp_players
    pts = simulate(everyone, n_sims, rng)
    col = {p.id: j for j, p in enumerate(everyone)}
    opp_total = pts[:, [col[p.id] for p in opp_players]].sum(axis=1) if opp_players else None

    lineups, empty_slots = enumerate_lineups(my_players, slots)
    scored = []
    for lu in lineups:
        tot = pts[:, [col[p.id] for p in lu]].sum(axis=1)
        win = _win_vec(tot, opp_total)
        scored.append({"players": lu, "mean": float(tot.mean()),
                       "win": float(win.mean()) if win is not None else None})

    by_points = max(scored, key=lambda s: s["mean"])
    best = max(scored, key=lambda s: (s["win"], s["mean"])) if opp_total is not None else by_points
    best_tot = pts[:, [col[p.id] for p in best["players"]]].sum(axis=1)
    best_win = _win_vec(best_tot, opp_total)
    p_win = best["win"]

    # Close calls: lineups one swap away from the pick.
    best_ids = {p.id for p in best["players"]}
    swaps = []
    for s in scored:
        ids = {p.id for p in s["players"]}
        if len(ids) != len(best_ids) or len(ids & best_ids) != len(ids) - 1:
            continue
        out_p = next(p for p in best["players"] if p.id not in ids)
        in_p = next(p for p in s["players"] if p.id not in best_ids)
        tot = pts[:, [col[p.id] for p in s["players"]]].sum(axis=1)
        if best_win is not None:
            diff = best_win - _win_vec(tot, opp_total)
            unit = "win%"
        else:
            diff = best_tot - tot
            unit = "pts"
        delta = float(diff.mean())
        se = float(diff.std(ddof=1) / math.sqrt(n_sims))
        swaps.append({"start": out_p, "over": in_p, "delta": delta, "se": se, "unit": unit})
    # One entry per (start, over) pair; closest calls first.
    seen, close = set(), []
    for sw in sorted(swaps, key=lambda s: s["delta"]):
        key = (sw["start"].id, sw["over"].id)
        if key in seen:
            continue
        seen.add(key)
        sw["label"] = _label(sw["delta"], sw["se"]) if sw["unit"] == "win%" else (
            "coin flip" if sw["delta"] < max(2 * sw["se"], 0.25) else "lean" if sw["delta"] < 1.5 else "clear")
        sw["why"] = _driver(sw["start"], sw["over"], p_win)
        close.append(sw)
        if len(close) >= max_close_calls:
            break

    assignment = assign_slots(best["players"], slots) or {}
    starters_ids = {p.id for p in best["players"]}
    mp_gap = mp_se = None
    if best_win is not None and by_points is not best:
        mp_tot = pts[:, [col[p.id] for p in by_points["players"]]].sum(axis=1)
        diff = best_win - _win_vec(mp_tot, opp_total)
        mp_gap, mp_se = float(diff.mean()), float(diff.std(ddof=1) / math.sqrt(n_sims))
    return {
        "mp_gap": mp_gap,
        "mp_se": mp_se,
        "meta": meta,
        "slots": slots,
        "assignment": assignment,
        "empty_slots": empty_slots,
        "bench": [p for p in my_players if p.id not in starters_ids and p.play_prob > 0],
        "unavailable": [p for p in my_players if p.play_prob == 0 and p.id not in starters_ids],
        "p_win": p_win,
        "my_median": float(np.median(best_tot)),
        "my_p10": float(np.percentile(best_tot, 10)),
        "my_p90": float(np.percentile(best_tot, 90)),
        "opp_median": float(np.median(opp_total)) if opp_total is not None else None,
        "max_points": by_points,
        "best": best,
        "close_calls": close,
        "n_lineups": len(lineups),
        "n_sims": n_sims,
        "seed": seed,
    }


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #

STAGE_LABELS = {
    "thursday": "Preliminary lean (Thursday) - re-run Sunday after inactives",
    "sunday": "Final call (Sunday, post-inactives)",
}


def _fmt(x: float) -> str:
    return f"{x:.1f}"


def render_markdown(d: dict, warnings: list[str]) -> str:
    meta = d["meta"]
    title = "Start/sit"
    if meta.get("week") is not None:
        title = f"Week {meta['week']} start/sit"
    lines = [f"# {title}: {meta['my_team_name']} vs {meta['opponent_name']}"]
    if meta.get("stage"):
        lines.append(f"_{STAGE_LABELS.get(meta['stage'], meta['stage'])}_")
    lines.append("")

    if d["p_win"] is not None:
        role = ("projected favorite" if d["p_win"] > 0.55 else
                "projected underdog" if d["p_win"] < 0.45 else "toss-up")
        lines.append(f"**Win probability: {d['p_win']:.0%}** ({role}). "
                     f"Your median {_fmt(d['my_median'])} (range {_fmt(d['my_p10'])}-{_fmt(d['my_p90'])}) "
                     f"vs opponent median {_fmt(d['opp_median'])}.")
    else:
        lines.append(f"**Projected total: {_fmt(d['my_median'])}** "
                     f"(range {_fmt(d['my_p10'])}-{_fmt(d['my_p90'])}). No opponent data, so this maximizes points.")
    lines.append("")

    lines.append("| Slot | Player | Floor | Median | Ceiling |")
    lines.append("|---|---|---|---|---|")
    for i, slot in enumerate(d["slots"]):
        p = d["assignment"].get(i)
        if p is None:
            lines.append(f"| {slot.name} | _(empty - no eligible healthy player)_ | | | |")
            continue
        tag = ""
        if p.actual is not None:
            tag = f" (final: {_fmt(p.actual)})"
        elif p.play_prob < 1:
            tag = f" ({p.play_prob:.0%} to play)"
        team = f" {p.team}" if p.team else ""
        lines.append(f"| {slot.name} | {p.name}, {p.pos}{team}{tag} | "
                     f"{_fmt(p.p10)} | {_fmt(p.p50)} | {_fmt(p.p90)} |")
    lines.append("")
    if d["bench"]:
        lines.append("**Bench:** " + ", ".join(f"{p.name} ({_fmt(p.p50)})" for p in d["bench"]))
    if d["unavailable"]:
        lines.append("**Out:** " + ", ".join(f"{p.name}{' (' + p.status + ')' if p.status else ''}"
                                              for p in d["unavailable"]))
    lines.append("")

    if d["close_calls"]:
        lines.append("## Close calls")
        for c in d["close_calls"]:
            if c["unit"] == "win%":
                margin = f"+{c['delta'] * 100:.1f} win% (±{c['se'] * 100:.1f})"
            else:
                margin = f"+{c['delta']:.1f} pts (±{c['se']:.1f})"
            lines.append(f"- **Start {c['start'].name} over {c['over'].name}**: {margin}, "
                         f"{c['label']}. {c['why'][0].upper() + c['why'][1:]}.")
        lines.append("")

    mp, best = d["max_points"], d["best"]
    gap, se = d["mp_gap"], d["mp_se"]
    if gap is not None and gap >= max(2 * se, 0.005):
        mp_ids, best_ids = {p.id for p in mp["players"]}, {p.id for p in best["players"]}
        add = [p.name for p in best["players"] if p.id not in mp_ids]
        drop = [p.name for p in mp["players"] if p.id not in best_ids]
        lines.append("## Why not the highest-projected lineup?")
        lines.append(f"Starting {', '.join(drop)} instead of {', '.join(add)} projects "
                     f"{mp['mean'] - best['mean']:.1f} more points on average, but this lineup wins "
                     f"{gap * 100:.1f} percentage points more often "
                     f"({best['win']:.1%} vs {mp['win']:.1%}). "
                     + ("As the underdog, you need the higher ceiling."
                        if d["p_win"] < 0.5 else "As the favorite, you want the safer floor."))
        lines.append("")

    if d["empty_slots"]:
        lines.append(f"**Warning:** {d['empty_slots']} starting slot(s) can't be filled with a healthy player "
                     "- check waivers.")
    for w in warnings:
        lines.append(f"_Note: {w}._")
    lines.append(f"_{d['n_sims']:,} simulations across {d['n_lineups']} legal lineups._")
    return "\n".join(lines).rstrip() + "\n"


def _player_json(p: Player) -> dict:
    return {"id": p.id, "name": p.name, "pos": p.pos, "team": p.team,
            "p10": p.p10, "p50": p.p50, "p90": p.p90, "play_prob": p.play_prob}


def to_json(d: dict, warnings: list[str]) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "week": d["meta"].get("week"),
        "stage": d["meta"].get("stage"),
        "win_prob": d["p_win"],
        "my_median": d["my_median"],
        "opp_median": d["opp_median"],
        "lineup": [{"slot": s.name, "player": _player_json(d["assignment"][i]) if i in d["assignment"] else None}
                   for i, s in enumerate(d["slots"])],
        "bench": [_player_json(p) for p in d["bench"]],
        "out": [_player_json(p) for p in d["unavailable"]],
        "max_points_lineup": [p.id for p in d["max_points"]["players"]],
        "max_points_win_prob": d["max_points"]["win"],
        "close_calls": [{"start": c["start"].id, "over": c["over"].id, "margin": c["delta"],
                         "se": c["se"], "unit": c["unit"], "label": c["label"], "why": c["why"]}
                        for c in d["close_calls"]],
        "warnings": warnings,
        "n_sims": d["n_sims"],
        "seed": d["seed"],
    }


def run_week(week: dict, n_sims: int = DEFAULT_SIMS, seed: Optional[int] = None) -> dict:
    """Validate, simulate, decide. Returns the JSON result plus 'report_md'."""
    meta, slots, mine, opp, warnings = parse_week(week)
    d = decide(meta, slots, mine, opp, n_sims=n_sims, seed=seed)
    out = to_json(d, warnings)
    out["report_md"] = render_markdown(d, warnings)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FAAB start/sit simulator")
    ap.add_argument("week_file", help="week JSON (see README.md)")
    ap.add_argument("--sims", type=int, default=DEFAULT_SIMS, help=f"simulations (default {DEFAULT_SIMS:,})")
    ap.add_argument("--seed", type=int, default=None, help="random seed for repeatable results")
    ap.add_argument("--json", dest="json_out", help="also write the result as JSON here")
    ap.add_argument("--md", dest="md_out", help="also write the report as Markdown here")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    try:
        with open(args.week_file, encoding="utf-8") as f:
            week = json.load(f)
        result = run_week(week, n_sims=args.sims, seed=args.seed)
    except WeekFileError as e:
        print(e, file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as e:
        print(f"Couldn't read {args.week_file}: {e}", file=sys.stderr)
        return 2

    print(result["report_md"])
    if args.md_out:
        with open(args.md_out, "w", encoding="utf-8") as f:
            f.write(result["report_md"])
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in result.items() if k != "report_md"}, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
