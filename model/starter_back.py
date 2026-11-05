#!/usr/bin/env python3
"""The "starter back" flag on score_week.py's scored players. DISPLAY ONLY.

A player's trailing window can be built while a teammate who usually out-snaps
him was missing -- and that teammate may be available again this week. Dameon
Pierce was production's #1 in 2026 week 7 on usage built while Saquon Barkley
missed weeks 5-6; Barkley returned in week 7 at 15% of the snaps and Pierce
stayed the lead back at 64%. So the flag informs and never decides: it
changes no score, rank, pool, pool rule, tracker pick or verification. It is a
`starter_back` list on each scored player record in the prediction JSON, a
marker beside the name in the markdown tables, and a section in plain words.

DEFINITION (any position, QB included). For a scored player P on team T at
position POS, with trailing window W (features_lib.trailing_window; only its
games for T are used), a teammate X is listed when all three hold:
  (a) X out-snapped P before the window: take X's PRE_WINDOW_GAMES most
      recent games for T before W's first game (this season or the one
      before); in those of them P also played, X's offense snap share minus
      P's averages at least LEAD_MIN. If P played none of them (practice
      squad, not yet on the team), P's share is 0 -- that is Pierce's case.
      Counting P's own absences as 0 would turn his injury into X's lead
      (Justin Jefferson, 2026 week 9, behind Jordan Addison). Snap shares come
      from tr_snap_counts, not stats rows, so a game P played without a stat
      counts at his real share.
  (b) X missed at least one of W's games for T: no snap row at all that week.
  (c) X is available this week -- decided by the caller with the sources
      score_week.py already resolved (weekly roster team and status, Yahoo's
      status under pool rule v4, the Out/Doubtful report, a game this week).
If X has played for T since his last missed window game, the flag carries his
most recent snap share and P's share in that same game.

WHY LEAD_MIN = 0.30 (chosen 2026-11-04 on snap shares alone, before any spike
outcome was looked at). Over 2022-2025, among same-position pairs meeting (b)
where the teammate was ahead of the player but both were in the rotation
(teammate under 60% of the snaps, player 20%+), the lead had a p90 of 0.29
(RB), 0.30 (WR), 0.26 (TE): 0.30 leaves out about 9 in 10 of them (RB 14 of
169 still caught, TE 7 of 111; at 0.20 it would be 34 and 34). Behind a real
starter (teammate 60%+) the RB lead's p10 was 0.34: 0.30 keeps 206 of 222.
Barkley over Pierce was 0.415 (weeks 1-4: 71/16/72/7% against Pierce's 0 --
two early exits pull the mean down, which is why the test is a lead over the
player, not a share floor). A full-time WR2 is not flagged when the WR1 sits:
his lead over a full-time teammate is about 0.
"""
from collections import defaultdict
from statistics import mean

LEAD_MIN = 0.30            # (a): mean per-game snap-share lead over P before the window
PRE_WINDOW_GAMES = 4       # (a): X's most recent games for the team before the window


class SnapIndex:
    """Offense snap shares by game, from tr_snap_counts rows with offense snaps > 0."""

    def __init__(self, rows):
        self.game = defaultdict(dict)        # (season, week, team) -> {player: (pct, position)}
        self.player = defaultdict(dict)      # player -> {(season, week): (team, pct)}
        self.team_games = defaultdict(set)   # team -> {(season, week)}
        for season, week, pid, pos, team, pct in rows:
            self.game[(season, week, team)][pid] = (pct or 0.0, pos)
            self.player[pid][(season, week)] = (team, pct or 0.0)
            self.team_games[team].add((season, week))

    @classmethod
    def from_db(cls, con, min_season=None):
        q = ("SELECT season, week, player_id, position, team, offense_pct FROM tr_snap_counts WHERE offense_snaps > 0"
             + (" AND season >= ?" if min_season else ""))
        try:
            return cls(con.execute(q, (min_season,) if min_season else ()).fetchall())
        except Exception:  # noqa: BLE001 -- no tr_snap_counts table: no flags, never a failure
            return cls([])

    def share(self, pid, game, team):
        """P's offense snap share for `team` in game (season, week); 0.0 when he has no row for that team."""
        row = self.player.get(pid, {}).get(game)
        return row[1] if row and row[0] == team else 0.0


def starter_back(pid, pos, team, window, target, snaps, available):
    """[flag dict] for player `pid` (position `pos`, current team `team`) at target (season, week).
    window: features_lib.trailing_window tuples (season, week, team, pos, touches); available(x) -> bool."""
    win = sorted((s, w) for s, w, t, *_ in window if t == team)
    if not win:
        return []
    first = win[0]
    pre = sorted(g for g in snaps.team_games.get(team, ()) if g < first and g[0] >= first[0] - 1)
    candidates = {x for g in pre for x, (_pct, p) in snaps.game[(*g, team)].items() if p == pos and x != pid}
    out = []
    for x in sorted(candidates):
        x_games = [g for g in pre if x in snaps.game[(*g, team)]][-PRE_WINDOW_GAMES:]
        if not x_games:
            continue
        # compared in the games both played; only when P played none of them (practice squad, a new arrival) is his
        # share 0 -- otherwise P's own absences would read as X's lead
        both = [g for g in x_games if pid in snaps.game[(*g, team)]]
        basis = both or x_games
        x_share = mean(snaps.game[(*g, team)][x][0] for g in basis)
        p_share = mean(snaps.share(pid, g, team) for g in basis)
        if x_share - p_share < LEAD_MIN:
            continue
        missed = [g for g in win if g not in snaps.player.get(x, {})]
        if not missed or not available(x):
            continue
        since = [g for g, (t, _pct) in snaps.player.get(x, {}).items() if t == team and missed[-1] < g < target]
        back = max(since) if since else None
        out.append({
            "teammate_id": x, "position": pos, "team": team,
            "weeks_missed": [{"season": s, "week": w} for s, w in missed],
            "before_window": {"games": [{"season": s, "week": w} for s, w in basis],
                              "compared": "games both played" if both else "player had no snaps in them",
                              "teammate_share": round(x_share, 3), "player_share": round(p_share, 3)},
            "returned": ({"season": back[0], "week": back[1], "teammate_share": round(snaps.share(x, back, team), 3),
                          "player_share": round(snaps.share(pid, back, team), 3)} if back else "not back yet"),
        })
    return out


def weeks_text(weeks, target_season):
    """[{season, week}] -> 'week 5', 'weeks 5-6', 'weeks 3, 5', '2025 weeks 11, 13, 16', '2025 week 18 and week 1':
    grouped by season, the target season's without its year, consecutive weeks as a range."""
    by = {}
    for d in weeks:
        by.setdefault(d["season"], []).append(d["week"])
    groups = []
    for season in sorted(by):
        ws = sorted(by[season])
        runs, cur = [], [ws[0]]
        for w in ws[1:]:
            if w == cur[-1] + 1:
                cur.append(w)
            else:
                runs.append(cur)
                cur = [w]
        runs.append(cur)
        parts = ", ".join(f"{r[0]}" if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs)
        word = "week" if len(ws) == 1 else "weeks"
        groups.append((f"{season} " if season != target_season else "") + f"{word} {parts}")
    return " and ".join(groups)


def describe(flag, player_name, teammate_name, target_season):
    """One line in plain words, e.g. 'usage built while Saquon Barkley missed weeks 5-6; Saquon Barkley back in week 7
    at 15% of snaps (Dameon Pierce 64%)'."""
    text = f"usage built while {teammate_name} missed {weeks_text(flag['weeks_missed'], target_season)}; "
    r = flag["returned"]
    if r == "not back yet":
        return text + f"{teammate_name} not back yet, available this week"
    when = f"week {r['week']}" if r["season"] == target_season else f"{r['season']} week {r['week']}"
    return text + (f"{teammate_name} back in {when} at {r['teammate_share']:.0%} of snaps "
                   f"({player_name} {r['player_share']:.0%})")
