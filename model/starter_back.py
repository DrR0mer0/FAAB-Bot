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
      P's averages at least LEAD_MIN. If P played none of them, P's share is
      0 -- that is Pierce's case (practice squad) -- but from rule v1.1
      (2026-11-12) only in those of X's games P sat out for a depth-chart
      reason; see THE 0% FALLBACK below. With none left, no flag.
      Counting P's own absences as 0 would turn his injury into X's lead
      (Justin Jefferson, 2026 week 9, behind Jordan Addison). Snap shares come
      from tr_snap_counts, not stats rows, so a game P played without a stat
      counts at his real share.
  (b) X missed at least one of W's games for T: no snap row at all that week.
  (c) X is available this week -- decided by the caller with the sources
      score_week.py already resolved (weekly roster team and status, Yahoo's
      status under pool rule v4, the Out/Doubtful report, a game this week).
If X has played for T since his last missed window game, the flag carries his
most recent snap share and P's share in that same game -- or, from 2026-11-12,
that P did not play it (no offense snaps), never "0%".

THE 0% FALLBACK (rule v1.1, from 2026-11-12; Presence.fallback). A comparison game counts as P at 0% only when
the data says he was with T that week and sat for a depth-chart reason:
  counts    he dressed for T with 0 offense snaps (a tr_snap_counts row, any season); he was on T's practice squad
            (weekly roster DEV, any sub-code); a game-day inactive (INA) with no injury designation that week; on the
            active roster (ACT) with no snap row and no designation -- a healthy backup who didn't play or was
            scratched.
  excluded  he played for another team that week (snap or stats row); his rookie year is later than the game's
            season; no weekly-roster row that week, or a row for another team, or cut / retired; reserve (RES, i.e.
            IR and the like), the exempt list (EXE, E01, E14), PUP, suspended, or any other status; inactive or
            active-without-snaps with an injury designation (Out / Doubtful / Questionable) that week -- or with
            any other report value ("Note"); and anything the data can't decide (no weekly roster or injury report
            for that season).
Why: rule v1 counted every such game as 0%, so a player who simply wasn't there lost to the teammate by default --
A.J. Brown, 2026 week 10, behind Mack Hollins on NE's 2025 games, when Brown was on PHI. Sources: nflverse's weekly
rosters and injury reports (nflverse_raw/roster_weekly_<season>.csv and injuries_<season>.csv for past seasons,
downloaded 2026-11-12 for 2021-2025; the run's own weekly roster and injury report for the current season),
tr_snap_counts (rows with 0 offense snaps included) and player_week_stats.

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


NFLVERSE_RELEASE = "https://github.com/nflverse/nflverse-data/releases/download/{tag}/{name}"
SEASON_FILES = (("weekly_rosters", "roster_weekly_{season}.csv"), ("injuries", "injuries_{season}.csv"))


class MissingPresenceData(RuntimeError):
    """A season's weekly roster or injury report is missing and could not be downloaded. Raised rather than letting the
    0% fallback treat that season's games as undeterminable and drop flags without a word."""


def _http_get(url):
    import requests
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.content


def ensure_season_files(raw_dir, seasons, fetch=None):
    """Make sure nflverse's weekly roster and injury report for each of `seasons` are in raw_dir (nflverse_raw/),
    downloading any that is missing from the same nflverse-data release URLs as the 2021-2025 files (2026-11-12).
    -> [paths downloaded]. Raises MissingPresenceData naming every file it could not get; a failed download leaves
    nothing behind. Needed on a wiped folder, a new machine, and every season rollover (in 2027, 2026 becomes a prior
    season: its roster file is already there from the weekly refresh, its injury report is not)."""
    from pathlib import Path
    raw_dir, got, failed = Path(raw_dir), [], []
    for season in seasons:
        for tag, pattern in SEASON_FILES:
            name = pattern.format(season=season)
            path = raw_dir / name
            if path.exists() and path.stat().st_size > 0:
                continue
            url = NFLVERSE_RELEASE.format(tag=tag, name=name)
            tmp = path.with_name(name + ".part")
            try:
                body = (fetch or _http_get)(url)
                if not body or b"gsis_id" not in body.split(b"\n", 1)[0]:
                    raise ValueError("the reply is not an nflverse CSV (no gsis_id column)")
                raw_dir.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(body)
                tmp.replace(path)
                got.append(path)
            except Exception as e:  # noqa: BLE001 -- every failure is reported, by file
                tmp.unlink(missing_ok=True)
                failed.append(f"{name} ({type(e).__name__}: {e})")
    if failed:
        raise MissingPresenceData(
            f"the starter-back flag needs nflverse's weekly roster and injury report for seasons "
            f"{', '.join(map(str, seasons))} in {raw_dir}, and could not download: {'; '.join(failed)}. Download them from "
            f"{NFLVERSE_RELEASE.format(tag='<weekly_rosters|injuries>', name='<file>')} into that folder and run again.")
    return got


DESIGNATED = {"Out", "Doubtful", "Questionable"}     # injury-report game statuses (report_status)
ABSENT_STATUSES_THAT_COUNT = {"DEV"}                   # practice squad, every sub-code (user decision 2026-11-12)


class Presence:
    """Per-week facts for the 0% fallback (rule v1.1), keyed (season, week, player):
      roster       {key: (team, status)} -- nflverse weekly rosters; roster_seasons: the seasons loaded
      designation  {key: report_status} -- non-empty injury-report values; injury_seasons: the seasons loaded
      dressed      {key: team} -- every tr_snap_counts row, 0 offense snaps included
      stat_team    {key: team} -- player_week_stats
      rookie_year  {player: year} -- from the weekly rosters"""

    def __init__(self, roster=None, roster_seasons=(), designation=None, injury_seasons=(), dressed=None, stat_team=None,
                 rookie_year=None):
        self.roster, self.roster_seasons = roster or {}, set(roster_seasons)
        self.designation, self.injury_seasons = designation or {}, set(injury_seasons)
        self.dressed, self.stat_team, self.rookie_year = dressed or {}, stat_team or {}, rookie_year or {}

    def add_roster_csv(self, path, season=None):
        """Add an nflverse roster_weekly_<season>.csv (regular-season rows). Missing file: nothing added."""
        import csv
        try:
            f = open(path, encoding="utf-8-sig")
        except OSError:
            return self
        with f:
            for r in csv.DictReader(f):
                if r.get("game_type") not in (None, "", "REG") or not r.get("gsis_id"):
                    continue
                s = int(r["season"])
                if season is not None and s != season:
                    continue
                self.roster[(s, int(r["week"]), r["gsis_id"])] = (r.get("team"), r.get("status"))
                self.roster_seasons.add(s)
                if r.get("rookie_year"):
                    self.rookie_year.setdefault(r["gsis_id"], int(r["rookie_year"]))
        return self

    def add_injury_csv(self, path, season=None):
        """Add an nflverse injuries_<season>.csv (regular-season rows). Missing file: nothing added."""
        import csv
        try:
            f = open(path, encoding="utf-8-sig")
        except OSError:
            return self
        with f:
            for r in csv.DictReader(f):
                if r.get("game_type") not in (None, "", "REG") or not r.get("gsis_id"):
                    continue
                s = int(r["season"])
                if season is not None and s != season:
                    continue
                self.injury_seasons.add(s)
                st = (r.get("report_status") or "").strip()
                if st:
                    self.designation[(s, int(r["week"]), r["gsis_id"])] = st
        return self

    def add_designations(self, season, by_week):
        """Add the current season's designations as tracker/injuries.py passes them ({week: {gsis: status}})."""
        self.injury_seasons.add(season)
        for w, by in (by_week or {}).items():
            for pid, st in by.items():
                self.designation[(season, int(w), pid)] = st
        return self

    def add_db(self, con, min_season):
        """tr_snap_counts (every row) and player_week_stats teams from min_season on. No table: nothing added."""
        for q, target in (("SELECT season, week, player_id, team FROM tr_snap_counts WHERE season >= ?", self.dressed),
                          ("SELECT season, week, player_id, team FROM player_week_stats WHERE season >= ?", self.stat_team)):
            try:
                for s, w, pid, team in con.execute(q, (min_season,)):
                    target[(s, w, pid)] = team
            except Exception:  # noqa: BLE001 -- a missing table: that source says nothing
                pass
        return self

    def fallback(self, pid, game, team):
        """(counts, why) for comparison game (season, week) of player `pid`, who had no offense snaps for `team` in it."""
        season, week = game
        key = (season, week, pid)
        if self.dressed.get(key) == team:
            return True, "dressed, 0 offense snaps"
        if (key in self.dressed and self.dressed[key] != team) or (key in self.stat_team and self.stat_team[key] != team):
            return False, "played for another team"
        if self.rookie_year.get(pid, 0) > season:
            return False, "not in the league yet"
        if season not in self.roster_seasons:
            return False, "undetermined: no weekly roster for that season"
        if key not in self.roster:
            return False, "not on a roster that week"
        r_team, status = self.roster[key]
        if r_team != team:
            return False, f"on {r_team} that week"
        if status in ABSENT_STATUSES_THAT_COUNT:
            return True, "practice squad"
        if status in ("INA", "ACT"):
            label = "inactive" if status == "INA" else "active, no snaps"
            d = self.designation.get(key)
            if d:
                return False, (f"{label}, injured ({d})" if d in DESIGNATED else f"{label}, undetermined (report: {d})")
            if season not in self.injury_seasons:
                return False, f"{label}, undetermined: no injury report for that season"
            return True, "healthy inactive" if status == "INA" else "healthy backup (active, no snaps)"
        return False, f"weekly roster {status}"


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

    def active(self, pid, game, team):
        """Did the player have offense snaps for `team` in game (season, week)?"""
        row = self.player.get(pid, {}).get(game)
        return bool(row and row[0] == team)

    def share(self, pid, game, team):
        """P's offense snap share for `team` in game (season, week); 0.0 when he has no row for that team."""
        row = self.player.get(pid, {}).get(game)
        return row[1] if row and row[0] == team else 0.0


def starter_back(pid, pos, team, window, target, snaps, available, presence=None):
    """[flag dict] for player `pid` (position `pos`, current team `team`) at target (season, week).
    window: features_lib.trailing_window tuples (season, week, team, pos, touches); available(x) -> bool;
    presence: a Presence for the 0% fallback (None: nothing can be decided, so the fallback never counts a game)."""
    presence = presence or Presence()
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
        # compared in the games both played; only when P played none of them is his share 0 -- and then (v1.1) only
        # in the games he sat out for a depth-chart reason (Presence.fallback); otherwise P's own absences, or games
        # before he joined the team, would read as X's lead
        both = [g for g in x_games if pid in snaps.game[(*g, team)]]
        judged = [] if both else [(g, *presence.fallback(pid, g, team)) for g in x_games]
        basis = both or [g for g, counts, _why in judged if counts]
        if not basis:
            continue
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
                              **({} if both else {"fallback": [{"season": g[0], "week": g[1], "counts": c, "why": why}
                                                               for g, c, why in judged]}),
                              "teammate_share": round(x_share, 3), "player_share": round(p_share, 3)},
            "returned": ({"season": back[0], "week": back[1], "teammate_share": round(snaps.share(x, back, team), 3),
                          "player_played": snaps.active(pid, back, team),
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
    player = f"{player_name} {r['player_share']:.0%}" if r.get("player_played", True) else f"{player_name} did not play"
    return text + f"{teammate_name} back in {when} at {r['teammate_share']:.0%} of snaps ({player})"
