#!/usr/bin/env python3
"""Projection providers for the start/sit week file (startsit/build_week.py).

A provider answers one question: for (season, week) and a list of players,
what is each player's p50 -- and, when it can say, p10 / p90 -- in the
league's own scoring? Its answer is a Projections object:

  values      {player id: {"p50": x[, "p10": y, "p90": z]} or None}
  provenance  one line saying where the numbers come from (it goes into the
              week file's _meta and the README describes it)
  flags       {player id: [str]} -- "thin history", "new team", ... -- every
              one of them is listed by the builder, never silently used
  warnings    [str]

A player is a dict with at least "id", "name", "pos", "team" (current NFL
team, nflverse code) and "gsis_id" (None when Yahoo's player couldn't be
matched).

PROVIDERS
  baseline  BaselineProvider: an exponentially weighted MEAN of the player's
            own league-scored points over his last games strictly BEFORE the
            target week (tracker/scoring.py's StatsStore and fantasy_points:
            the tracker's league scoring, not a new scorer). The target week,
            and anything after it, is never read -- the same leakage rule as
            the model (tested). p10/p90 are his own weighted empirical 10th /
            90th percentiles once he has MIN_SPREAD_GAMES games; with fewer they
            are left out and lineup_sim falls back to its DEFAULT_SPREADS.
            The p50 is a weighted mean, not a median: fantasy points are
            right-skewed, so it sits a little above the median lineup_sim
            treats p50 as.
  csv       CsvProvider: a hand-supplied file (id or name, p50, optional
            p10/p90, optional play_prob) layered over the baseline: a row
            overrides the baseline for that player only. A row may give only
            play_prob (the projection stays the baseline's); the builder then
            uses it instead of the status-derived chance of playing and lists
            it in _meta as a manual override.
"""
import csv
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tracker"))
sys.path.insert(0, str(REPO_ROOT / "data"))
import ownership  # noqa: E402 -- norm_name, the tracker's player-name normalization
import scoring  # noqa: E402 -- StatsStore / fantasy_points: the league's scoring
from team_crosswalk import norm_team  # noqa: E402

N_GAMES = 8              # the most recent games a baseline looks at
HALF_LIFE_GAMES = 3      # a game counts half as much as one three games newer
MIN_SPREAD_GAMES = 6     # own p10/p90 only from this many games; fewer -> lineup_sim's DEFAULT_SPREADS
THIN_GAMES = 3           # fewer games than this: projected, but listed as "thin history"


@dataclass
class Projections:
    values: dict
    provenance: str
    flags: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    play_prob: dict = field(default_factory=dict)     # {player id: 0-1} set by hand (csv), overriding the status-derived chance


def weighted_quantile(values, weights, q):
    """The q-quantile of values under weights: the smallest value whose cumulative weight reaches q of the total."""
    pairs = sorted(zip(values, weights))
    total = sum(w for _v, w in pairs)
    run = 0.0
    for v, w in pairs:
        run += w
        if run >= q * total - 1e-12:
            return v
    return pairs[-1][0]


class BaselineProvider:
    name = "baseline"

    def __init__(self, store, n_games=N_GAMES, half_life=HALF_LIFE_GAMES, min_spread_games=MIN_SPREAD_GAMES,
                 thin_games=THIN_GAMES, scoring_label=""):
        self.store, self.n_games, self.half_life = store, n_games, half_life
        self.min_spread_games, self.thin_games, self.scoring_label = min_spread_games, thin_games, scoring_label

    def provenance(self, season, week):
        return (f"baseline: exponentially weighted mean (half-life {self.half_life} games) of the player's last <= {self.n_games} "
                f"league-scored games strictly before {season} week {week}{self.scoring_label}; p10/p90 = his weighted empirical "
                f"10th/90th percentiles with >= {self.min_spread_games} games, otherwise lineup_sim's DEFAULT_SPREADS")

    def games(self, gsis_id, season, week):
        """[(season, week, points, team)] oldest first -- strictly before (season, week)."""
        out = []
        for s, w, pts, _ts in self.store.history_before(gsis_id, season, week, self.n_games):
            if (s, w) >= (season, week):          # belt and braces: history_before already stops before the week
                continue
            row = (self.store.season_data(s) or {}).get(w, {}).get(gsis_id, {})
            out.append((s, w, pts, norm_team(row.get("team"))))
        return out

    def project(self, season, week, players):
        values, flags = {}, {}
        for p in players:
            pid = p["id"]
            if not p.get("gsis_id"):
                values[pid], flags[pid] = None, ["no gsis id (Yahoo player not matched to nflverse)"]
                continue
            games = self.games(p["gsis_id"], season, week)
            if not games:
                values[pid], flags[pid] = None, ["no league-scored games before this week (rookie or no NFL history)"]
                continue
            pts = [g[2] for g in games]
            weights = [0.5 ** ((len(games) - 1 - i) / self.half_life) for i in range(len(games))]   # newest = 1
            p50 = sum(x * w for x, w in zip(pts, weights)) / sum(weights)
            v = {"p50": round(p50, 2)}
            if len(games) >= self.min_spread_games:
                v["p10"] = round(min(weighted_quantile(pts, weights, 0.10), p50), 2)
                v["p90"] = round(max(weighted_quantile(pts, weights, 0.90), p50), 2)
            values[pid] = v
            f = []
            if len(games) < self.thin_games:
                f.append(f"thin history ({len(games)} game{'s' if len(games) != 1 else ''})")
            last_team = games[-1][3]
            if p.get("team") and last_team and last_team != p["team"]:
                f.append(f"new team (recent games for {last_team}, now {p['team']})")
            if games[-1][0] < season:
                f.append(f"no {season} games yet (last game {games[-1][0]} week {games[-1][1]})")
            if f:
                flags[pid] = f
        return Projections(values, self.provenance(season, week), flags)


def read_override_csv(text):
    """(rows, problems): rows = [{"key", "p50", "p10", "p90", "play_prob", "line"}] from a file with a header holding `id`
    and/or `name` and `p50` and/or `play_prob` (optional `p10`, `p90`). A row needs a p50 or a play_prob. Lines starting with
    # are comments."""
    rows, problems = [], []
    lines = [ln for ln in text.lstrip("﻿").splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    fields = [f.strip().lower() for f in (reader.fieldnames or [])]
    if not ({"p50", "play_prob"} & set(fields)) or not ({"id", "name"} & set(fields)):
        return [], ["the header needs p50 and/or play_prob, and id and/or name"]
    for n, raw in enumerate(reader, start=2):
        r = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        key = r.get("id") or r.get("name")
        try:
            nums = {k: float(r[k]) for k in ("p50", "p10", "p90", "play_prob") if r.get(k)}
        except ValueError:
            problems.append(f"line {n}: p10/p50/p90/play_prob must be numbers")
            continue
        if not key or not ({"p50", "play_prob"} & set(nums)):
            problems.append(f"line {n}: needs an id or name and a p50 and/or a play_prob")
            continue
        if ("p10" in nums or "p90" in nums) and "p50" not in nums:
            problems.append(f"line {n}: p10/p90 need a p50")
            continue
        if "p50" in nums and not nums.get("p10", nums["p50"]) <= nums["p50"] <= nums.get("p90", nums["p50"]):
            problems.append(f"line {n}: needs p10 <= p50 <= p90")
            continue
        if "play_prob" in nums and not 0.0 <= nums["play_prob"] <= 1.0:
            problems.append(f"line {n}: play_prob must be between 0 and 1")
            continue
        rows.append(dict(nums, key=key, by="id" if r.get("id") else "name", line=n))
    return rows, problems


class CsvProvider:
    """A hand-supplied override file layered over another provider (normally the baseline)."""
    name = "csv"

    def __init__(self, path, base):
        self.path, self.base = Path(path), base

    def project(self, season, week, players):
        out = self.base.project(season, week, players)
        rows, problems = read_override_csv(self.path.read_text(encoding="utf-8"))
        by_id = {}
        for p in players:
            by_id[str(p["id"])] = p
            if p.get("gsis_id"):
                by_id[str(p["gsis_id"])] = p
        by_name = {}
        for p in players:
            by_name.setdefault(ownership.norm_name(p["name"]), []).append(p)
        warnings = [f"{self.path.name} {m}" for m in problems]
        n = 0
        for r in rows:
            if r["by"] == "id":
                hits = [by_id[r["key"]]] if r["key"] in by_id else []
            else:
                hits = by_name.get(ownership.norm_name(r["key"]), [])
            if len(hits) != 1:
                warnings.append(f"{self.path.name} line {r['line']}: '{r['key']}' matches "
                                f"{'no player in this week file' if not hits else f'{len(hits)} players -- use the id'}; ignored")
                continue
            pid = hits[0]["id"]
            if "p50" in r:
                out.values[pid] = {k: r[k] for k in ("p10", "p50", "p90") if k in r}
                out.flags[pid] = [f for f in out.flags.get(pid, []) if not f.startswith("no ")] + [f"csv override ({self.path.name})"]
            if "play_prob" in r:
                out.play_prob[pid] = r["play_prob"]
            n += 1
        out.provenance = f"csv: {self.path.name} ({n} override(s)) over {out.provenance}"
        out.warnings += warnings
        return out
