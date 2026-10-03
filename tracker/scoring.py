#!/usr/bin/env python3
"""League scoring, per-position ranking, spike evaluation, and the hit
definition config for the baseline tracker.

Everything here is computed under THE LEAGUE'S scoring (tracker/hit_config.json:
0.5 PPR, 5-pt passing TDs, 6-pt rushing/receiving TDs, 0.04/passing yd, -2 INT,
-2 fumbles lost, +2 per 2-pt conversion) straight from nflverse's raw
stats_player_week_<season>.csv -- NOT from labels_player_week.fantasy_points_half,
which was built with a 4-pt passing TD (a known mismatch, flagged in reports,
to be fixed in a separate retrain). The raw CSV is also the only place the
2-pt conversion columns exist; the SQLite loader never carried them.
"""
import csv
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "hit_config.json"

# The sections of hit_config.json that DEFINE a hit. Their hash is stored with
# every scored week; operational knobs ('run', 'report') are deliberately not
# in it so tuning, say, the bootstrap size doesn't look like a redefinition.
HASHED_SECTIONS = ("scoring", "hit", "crowd_hit")


def load_config(path=None):
    with open(path or DEFAULT_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def hash_input(cfg):
    return json.dumps({k: cfg[k] for k in HASHED_SECTIONS}, sort_keys=True, separators=(",", ":"))


def config_hash(cfg):
    return hashlib.sha256(hash_input(cfg).encode("utf-8")).hexdigest()


def _num(stats, key):
    v = stats.get(key)
    return float(v) if v not in (None, "") else 0.0


def fantasy_points(stats, rules):
    """stats: mapping using nflverse's raw column names. Fumbles lost are the
    sum of the sack/rushing/receiving columns (nflverse's own fantasy_points
    does the same); 2-pt conversions are summed across passing/rushing/
    receiving and each worth rules['two_pt_conversion']."""
    fumbles_lost = _num(stats, "sack_fumbles_lost") + _num(stats, "rushing_fumbles_lost") + _num(stats, "receiving_fumbles_lost")
    two_pt = (_num(stats, "passing_2pt_conversions") + _num(stats, "rushing_2pt_conversions")
              + _num(stats, "receiving_2pt_conversions"))
    pts = (
        _num(stats, "passing_yards") * rules["pass_yd"]
        + _num(stats, "passing_tds") * rules["pass_td"]
        + _num(stats, "passing_interceptions") * rules["pass_int"]
        + _num(stats, "rushing_yards") * rules["rush_yd"]
        + _num(stats, "rushing_tds") * rules["rush_td"]
        + _num(stats, "receiving_yards") * rules["rec_yd"]
        + _num(stats, "receptions") * rules["reception"]
        + _num(stats, "receiving_tds") * rules["rec_td"]
        + fumbles_lost * rules["fumble_lost"]
        + two_pt * rules["two_pt_conversion"]
    )
    return round(pts, 4)


def position_ranks(week_rows, positions, top_n):
    """week_rows: {player_id: {'position', 'points', ...}}. Competition
    ranking (ties share the best rank) within each position across EVERY
    player with a stat row that week -- not just the model pool. Returns
    {player_id: (position, rank, in_top_n)}."""
    by_pos = defaultdict(list)
    for pid, r in week_rows.items():
        if r["position"] in positions:
            by_pos[r["position"]].append((r["points"], pid))
    out = {}
    for pos, lst in by_pos.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        rank, prev = 0, None
        for i, (pts, pid) in enumerate(lst, start=1):
            if pts != prev:
                rank, prev = i, pts
            out[pid] = (pos, rank, rank <= top_n)
    return out


def spike_result(prior_points, points, spike_cfg):
    """(is_spike, baseline, threshold) -- mirrors generate_labels_and_
    breakouts.py's rule (>= multiplier x trailing-N average AND >= min
    points) but on league-scored points. (None, None, None) when there
    aren't enough prior games to define a baseline."""
    if len(prior_points) < spike_cfg["min_prior_games"]:
        return None, None, None
    window = prior_points[-spike_cfg["baseline_games"]:]
    baseline = sum(window) / len(window)
    threshold = max(spike_cfg["multiplier"] * baseline, spike_cfg["min_points"])
    return points >= threshold, baseline, threshold


class StatsStore:
    """Lazy reader of nflverse_raw/stats_player_week_<season>.csv, regular
    season only, scored under the league rules. Seasons are loaded on demand
    and cached; history lookups walk BACKWARD through consecutive seasons
    that exist on disk and stop at a missing one (the 2019 schema gap), the
    same reset rule the labels pipeline uses."""

    def __init__(self, raw_dir, rules):
        self.raw_dir = Path(raw_dir)
        self.rules = rules
        self._seasons = {}  # season -> {week: {player_id: row}} or None if file missing

    def path(self, season):
        return self.raw_dir / f"stats_player_week_{season}.csv"

    def season_data(self, season):
        if season not in self._seasons:
            p = self.path(season)
            if not p.exists():
                self._seasons[season] = None
            else:
                weeks = defaultdict(dict)
                with open(p, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        if r.get("season_type") not in (None, "", "REG"):
                            continue
                        try:
                            wk = int(r["week"])
                        except (KeyError, ValueError):
                            continue
                        ts = r.get("target_share")
                        weeks[wk][r["player_id"]] = {
                            "position": r.get("position"), "team": r.get("team"),
                            "points": fantasy_points(r, self.rules),
                            "target_share": float(ts) if ts not in (None, "") else None,
                        }
                self._seasons[season] = dict(weeks)
        return self._seasons[season]

    def week_rows(self, season, week):
        data = self.season_data(season)
        return (data or {}).get(week, {})

    def max_week(self, season):
        data = self.season_data(season)
        return max(data) if data else 0

    def history_before(self, player_id, season, week, n):
        """The player's last <= n games strictly before (season, week), oldest
        first, as [(season, week, points, target_share)]."""
        games = []
        s = season
        while len(games) < n:
            data = self.season_data(s)
            if data is None:
                break
            weeks = sorted(w for w, rows in data.items() if player_id in rows and (s, w) < (season, week))
            for w in reversed(weeks):
                r = data[w][player_id]
                games.append((s, w, r["points"], r["target_share"]))
                if len(games) >= n:
                    break
            s -= 1
        return list(reversed(games))
