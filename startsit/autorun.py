#!/usr/bin/env python3
"""Start/sit reports produced automatically, timed to each lock (from 2026 week 9).

  python startsit/autorun.py plan --register  # compute the run times and register them (what the daily task runs)
  python startsit/autorun.py plan             # ...without --register: only show them (--write: also write plan.json)
  python startsit/autorun.py table            # every remaining week's run times for the current roster
  python startsit/autorun.py run              # do whichever planned run is due now (what the run task calls)

WHAT RUNS, AND WHEN (local time; computed, never typed in)
  preliminary  the morning of each week's first game day (Thursday most weeks; Wednesday in week 12) at 06:50,
               after the 06:35 league snapshot: build_week.py --stage thursday --run. A fresh league snapshot
               is taken first only if the newest roster snapshot is over an hour old.
  pre-lock     for every kickoff time at which one of MY rostered players (starters or bench; not IR / IR+ / NA)
               plays, 60 minutes before kickoff -- after inactives (about 90 minutes before; Yahoo needs a while to show
               them) and before the lock:
               a fresh league snapshot (current locks, lineup and Yahoo statuses), Yahoo's live points of my team and
               my opponent for the week (two read-only calls; build_week.py --live-points scores a locked player
               from them once his game is over), then build_week.py --stage sunday --run as of now. Opponent-only
               kickoffs get no run.
  No run after the league's end_week (Yahoo settings; 17), and none in a week where my team has no matchup on
  Yahoo's scoreboard (a playoff bye, or eliminated -- a consolation game counts as eliminated). A week whose
  matchups aren't on record yet (Yahoo publishes a week's matchups in Tuesday's 06:35 snapshot) gets no run
  until they are.

HOW IT IS SCHEDULED -- two Task Scheduler tasks (registered by `scheduled_run.py install --job startsit-plan`):
  \\FAAB\\startsit-plan  daily 06:45 and 2 minutes after sign-in. Downloads nflverse's schedule (the freshest
                       source short of loading the DB; falls back to the newest copy on disk, then nfl_games),
                       computes the next 8 days' runs and re-registers \\FAAB\\startsit-run with one one-shot
                       trigger per run. Each run time records which schedule source it came from.
  \\FAAB\\startsit-run   those one-shot triggers, and 4 minutes after sign-in. It does whichever planned run is
                       due and hasn't been handled (scheduled_runs.log, by run key). A pre-lock run that can no
                       longer start at least 10 minutes before its kickoff is MISSED: a log line and a
                       notification, never a late run for a cluster that has locked. An earlier due run that a
                       later one supersedes is skipped.
  After a pre-lock run the plan is recomputed from the fresh snapshot; if my roster now needs a different set of
  runs (a player added since the morning), the plan task is started again to re-register the run task. The run
  task never re-registers itself.

OUTPUT -- league_data/startsit/ (gitignored: it names the league's teams), never overwritten by another run:
  <season>_week<NN>_preliminary.json (+ _report.md / _report.json)
  <season>_week<NN>_prelock_<kickoff local YYYYMMDD_HHMM>.json (+ _report.md / _report.json)
  plan.json (the current plan) and games.csv (the downloaded schedule). The --stage thursday / sunday commands
  of build_week.py are unchanged and still write league_data/<season>_week<NN>_<stage>.json.

ALERTS -- only when there's something to act on. After a pre-lock run, if the recommended starters differ from
my current Yahoo lineup in the fresh snapshot, a non-blocking Windows notification says what to swap and how
strong the case is: the recommended lineup is scored against the current one on the very same simulations
(lineup_sim's draws, same seed) and labelled as lineup_sim labels its close calls (coin flip / lean / clear).
Silent when they agree; the files are written either way. The preliminary run alerts only when a current Yahoo
starter can't play at all (bye, an out status such as IR / O / NA, or Out on the injury report).
Failures follow the runner's pattern: a FAILED line and a message box. Read-only toward Yahoo: nothing here
changes a lineup.

THE LOG JOBS WIN -- sign-in catch-up can start the plan job, a start/sit run and a tracker log job at the same moment
against the same SQLite DB. A start/sit task waits while a scheduled log job (tracker-log-thu / -sun) is running,
per Task Scheduler's own state; a pre-lock run still waiting RUN_BUDGET + MIN_LEAD before its kickoff is MISSED, never
run alongside it, and the plan job (read-only) goes ahead after PLAN_WAIT. The DB is in WAL mode (readers never block
the log job's writes) and every start/sit connection has a 30 s busy timeout, like the log job's own.
"""
import argparse
import base64
import csv
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
for d in ("league", "tracker", "model", "data"):
    sys.path.insert(0, str(REPO_ROOT / d))
sys.path.insert(0, str(HERE))

import build_week as bw  # noqa: E402
import league_availability  # noqa: E402 -- the Yahoo session and league key (tracker/)
import league_data as ld  # noqa: E402
import lineup_sim  # noqa: E402
import scheduled_run as sr  # noqa: E402 -- the runner's log, message box and step runner (read-only use)
from score_week import load_current_roster, parse_kickoff_utc  # noqa: E402
from team_crosswalk import norm_team  # noqa: E402

OUT_DIR = REPO_ROOT / "league_data" / "startsit"
PLAN_FILE = OUT_DIR / "plan.json"
SCHEDULE_CACHE = OUT_DIR / "games.csv"
NFLVERSE_GAMES = REPO_ROOT / "nflverse_raw" / "games.csv"
LEAGUE_RAW_DIR = REPO_ROOT / "raw" / "league"
GAMES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"

PRELIMINARY_AT = (6, 50)                 # local; the league snapshot runs at 06:35 and takes seconds
LEAD = timedelta(minutes=60)             # inactives post about 90 minutes before kickoff; Yahoo needs time to show them
MIN_LEAD = timedelta(minutes=10)         # later than this before kickoff, a pre-lock run is MISSED instead
HORIZON = timedelta(days=8)              # how far ahead the daily plan registers triggers
SNAPSHOT_FRESH = timedelta(minutes=60)   # a preliminary run takes its own snapshot if the newest is older
KEEP_MISSED = timedelta(days=7)          # an unhandled past run older than this is dropped, not reported
RUN_BUDGET = timedelta(minutes=3)        # what a run needs (it takes ~10 s): a wait for a log job ends this long before MIN_LEAD
PLAN_WAIT = timedelta(minutes=10)        # the plan job only reads (WAL), so after this it goes ahead
WAIT_POLL_S = 20
# The log jobs win (sign-in catch-up can start everything at once): a start/sit task waits while one of these is running.
LOG_TASKS = ("tracker-log-thu", "tracker-log-sun")
END_WEEK_DEFAULT = 17
NOT_PLAYING = {"IR", "IR+", "NA"}
PLAN_JOB, RUN_JOB = "startsit-plan", "startsit-run"
TASKS = {PLAN_JOB: {"task": "startsit-plan", "description": "FAAB start/sit: plan the week's pre-lock runs (06:45 daily)"},
         RUN_JOB: {"task": "startsit-run", "description": "FAAB start/sit: the planned preliminary and pre-lock runs"}}


# ------------------------------------------------------------------ time

def to_local(dt_utc, tz=None):
    """An aware instant -> naive local wall time (the OS's rules, DST included, unless tz is given)."""
    return dt_utc.astimezone(tz).replace(tzinfo=None) if tz else datetime.fromtimestamp(dt_utc.timestamp())


def from_local(naive, tz=None):
    return (naive.replace(tzinfo=tz) if tz else naive.astimezone()).astimezone(timezone.utc)


def utcnow():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ schedule

def parse_games_csv(text, season):
    """nflverse games.csv -> [(week, kickoff UTC, home, away)] for the regular season; gameday + gametime are
    US/Eastern (the same convention as nfl_games.kickoff_utc, so the one converter applies)."""
    out = []
    for r in csv.DictReader(text.splitlines()):
        if r.get("season") != str(season) or r.get("game_type") != "REG" or not r.get("gametime"):
            continue
        k = parse_kickoff_utc(f"{r['gameday']}T{r['gametime']}:00")
        if k is not None:
            out.append((int(r["week"]), k, norm_team(r["home_team"]), norm_team(r["away_team"])))
    return out


def _download(url):
    import requests
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def load_schedule(season, con, now, download=_download, cache=SCHEDULE_CACHE, nflverse_file=NFLVERSE_GAMES):
    """-> (games, source text). The freshest source short of loading the DB: a download of nflverse's schedule
    (kept in `cache`), else the newer of the cached copy and nflverse_raw/games.csv, else nfl_games. A source with no
    games for the season is passed over."""
    tried = []
    if download is not None:
        try:
            body = download(GAMES_URL)
            games = parse_games_csv(body.decode("utf-8"), season)
            if games:
                cache = Path(cache)
                cache.parent.mkdir(parents=True, exist_ok=True)
                tmp = cache.with_suffix(".tmp")
                tmp.write_bytes(body)
                os.replace(tmp, cache)
                return games, f"nflverse schedule, downloaded {to_local(now):%Y-%m-%d %H:%M}"
            tried.append("download had no games for the season")
        except Exception as e:  # noqa: BLE001 -- fall back to a file on disk
            tried.append(f"download failed ({type(e).__name__})")
    files = sorted((p for p in (Path(cache), Path(nflverse_file)) if p.exists()), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files:
        games = parse_games_csv(p.read_text(encoding="utf-8"), season)
        if games:
            age = (now.timestamp() - p.stat().st_mtime) / 3600
            return games, f"{p.name} on disk ({p.parent.name}/, {age:.0f}h old" + (f"; {'; '.join(tried)}" if tried else "") + ")"
        tried.append(f"{p.name} had no games for the season")
    games = []
    for r in con.execute("SELECT week, kickoff_utc, home_team, away_team FROM nfl_games WHERE season=? AND is_playoffs=0", (season,)):
        k = parse_kickoff_utc(r[1])
        if k is not None:
            games.append((r[0], k, norm_team(r[2]), norm_team(r[3])))
    return games, "nfl_games in the DB (loaded on Tuesdays)" + (f"; {'; '.join(tried)}" if tried else "")


# ------------------------------------------------------------------ league facts

def league_end_week(con, raw_dir=LEAGUE_RAW_DIR):
    """(end_week, source) from the newest successful settings snapshot's raw response; the default if unreadable."""
    row = con.execute("SELECT raw_file FROM lg_snapshots WHERE resource='settings' AND ok=1 AND raw_file IS NOT NULL "
                      "ORDER BY taken_at DESC LIMIT 1").fetchone()
    if row:
        try:
            raw = json.loads(gzip.decompress((Path(raw_dir) / row[0]).read_bytes()))
            for page in raw.get("pages", []):
                league = json.loads(page["body"])["fantasy_content"]["league"]
                if isinstance(league, list) and "end_week" in league[0]:
                    return int(league[0]["end_week"]), "Yahoo league settings"
        except Exception:  # noqa: BLE001
            pass
    return END_WEEK_DEFAULT, f"default {END_WEEK_DEFAULT} (the settings snapshot could not be read)"


def matchup_status(con, team_id):
    """{week: 'ok' | 'no matchup' | 'eliminated'} for every week on the newest scoreboard snapshot; a week not in it
    is not on record yet."""
    snap = ld.latest_ok(con, "scoreboard")
    if snap is None:
        return {}
    weeks = {r[0] for r in con.execute("SELECT DISTINCT week FROM lg_matchups WHERE snapshot_id=?", (snap[0],))}
    mine = {r[0]: r[1] for r in con.execute("SELECT week, is_consolation FROM lg_matchups WHERE snapshot_id=? AND team_id=?",
                                            (snap[0], team_id))}
    return {w: ("no matchup" if w not in mine else "eliminated" if mine[w] else "ok") for w in weeks}


def my_playing_roster(con, team_id, season, week, weekly_roster):
    """{nfl team: [player names]} for my rostered players who can play (not IR / IR+ / NA), from the newest roster
    snapshot; the team is nflverse's weekly-roster team, else Yahoo's (LAR -> LA). Plus the snapshot's taken_at."""
    snap = ld.latest_ok(con, "rosters")
    if snap is None:
        return {}, None
    nfl_team = load_current_roster(str(weekly_roster), season, week)[0] if Path(weekly_roster).exists() else {}
    out = {}
    for r in con.execute("SELECT name, team, player_id, selected_position FROM lg_rosters WHERE snapshot_id=? AND team_id=?",
                         (snap[0], team_id)):
        if (r[3] or "BN") in NOT_PLAYING:
            continue
        team = nfl_team.get(r[2]) or bw.YAHOO_TEAM_CODES.get(r[1], r[1])
        if team:
            out.setdefault(norm_team(team), []).append(r[0])
    return out, snap[1]


# ------------------------------------------------------------------ the plan (pure)

def compute_plan(games, my_teams, matchups, end_week, now, source, horizon=HORIZON, tz=None, season=None,
                 assume_pending=False):
    """-> (entries, weeks). entries: the runs whose time is after `now` and within `horizon` (None: the whole
    season), each {key, kind, season, week, run_at_local, run_at, lock, games, players, schedule_source}.
    weeks: {week: why it has no runs} for the weeks left out. assume_pending (the `table` command only): a week whose
    matchup isn't on record yet is planned anyway, its entries marked matchup_assumed -- never registered."""
    by_week = {}
    for week, k, home, away in games:
        by_week.setdefault(week, []).append((k, home, away))
    entries, skipped = [], {}
    end = None if horizon is None else now + horizon
    for week in sorted(by_week):
        gs = sorted(by_week[week])
        if gs[-1][0] <= now:
            continue                                            # the week is over
        if week > end_week:
            skipped[week] = f"after the league's end_week ({end_week})"
            continue
        status = matchups.get(week)
        if status is None and assume_pending:
            status = "assumed"
        if status is None:
            skipped[week] = "my matchup isn't on Yahoo's scoreboard yet (it appears in the Tuesday snapshot)"
            continue
        if status not in ("ok", "assumed"):
            skipped[week] = f"{status} -- no runs"
            continue
        first = gs[0][0]
        prelim_local = to_local(first, tz).replace(hour=PRELIMINARY_AT[0], minute=PRELIMINARY_AT[1], second=0, microsecond=0)
        prelim = from_local(prelim_local, tz)
        runs = []
        if prelim <= first - LEAD:
            runs.append(("preliminary", prelim, first, [f"{a}@{h}" for k, h, a in gs if k == first], []))
        for k in sorted({k for k, h, a in gs if h in my_teams or a in my_teams}):
            here = [(h, a) for kk, h, a in gs if kk == k and (h in my_teams or a in my_teams)]
            players = sorted(p for h, a in here for t in (h, a) for p in my_teams.get(t, []))
            runs.append(("prelock", k - LEAD, k, [f"{a}@{h}" for h, a in here], players))
        for kind, run_at, lock, matchup_games, players in runs:
            if run_at <= now or (end is not None and run_at > end):
                continue
            yr = season if season is not None else first.year
            key = f"{yr}w{week:02d}-preliminary" if kind == "preliminary" else f"{yr}w{week:02d}-prelock-{lock:%Y%m%dT%H%MZ}"
            entries.append({"key": key, "kind": kind, "season": yr, "week": week,
                            "run_at_local": to_local(run_at, tz).isoformat(timespec="minutes"),
                            "run_at": run_at.isoformat(), "lock": lock.isoformat(),
                            "lock_local": to_local(lock, tz).isoformat(timespec="minutes"),
                            "games": matchup_games, "players": players, "schedule_source": source,
                            **({"matchup_assumed": True} if status == "assumed" else {})})
    return sorted(entries, key=lambda e: e["run_at"]), skipped


def output_stem(entry, out_dir=OUT_DIR):
    if entry["kind"] == "preliminary":
        return Path(out_dir) / f"{entry['season']}_week{entry['week']:02d}_preliminary"
    lock_local = datetime.fromisoformat(entry["lock_local"])
    return Path(out_dir) / f"{entry['season']}_week{entry['week']:02d}_prelock_{lock_local:%Y%m%d_%H%M}"


# ------------------------------------------------------------------ comparing the recommendation with Yahoo

def lineup_margin(week_file, best_ids, other_ids, n_sims, seed):
    """How much better the recommended starters are than another set, on lineup_sim's own draws: parse_week and
    simulate exactly as decide() does (same order, same seed), so best's win rate is the report's win_prob.
    -> {delta, se, unit ('win%' or 'pts'), label, best, other}."""
    _meta, _slots, mine, opp, _w = lineup_sim.parse_week(week_file)
    rng = np.random.default_rng(seed)
    everyone = mine + opp
    pts = lineup_sim.simulate(everyone, n_sims, rng)
    col = {p.id: j for j, p in enumerate(everyone)}
    opp_total = pts[:, [col[p.id] for p in opp]].sum(axis=1) if opp else None

    def total(ids):
        return pts[:, [col[i] for i in ids]].sum(axis=1) if ids else np.zeros(n_sims)

    b, o = total(best_ids), total(other_ids)
    if opp_total is not None:
        wb, wo = lineup_sim._win_vec(b, opp_total), lineup_sim._win_vec(o, opp_total)
        diff, unit = wb - wo, "win%"
        best, other = float(wb.mean()), float(wo.mean())
    else:
        diff, unit = b - o, "pts"
        best, other = float(b.mean()), float(o.mean())
    delta, se = float(diff.mean()), float(diff.std(ddof=1) / np.sqrt(n_sims))
    if unit == "win%":
        label = lineup_sim._label(delta, se)
    else:
        label = "coin flip" if delta < max(2 * se, 0.25) else "lean" if delta < 1.5 else "clear"
    return {"delta": delta, "se": se, "unit": unit, "label": label, "best": best, "other": other}


def current_yahoo_starters(con, team_id):
    """{external_id: (name, slot)} of my starters in the newest roster snapshot (not BN / IR / IR+ / NA)."""
    snap = ld.latest_ok(con, "rosters")
    if snap is None:
        return {}
    return {r[0]: (r[1], r[2]) for r in con.execute(
        "SELECT external_id, name, selected_position FROM lg_rosters WHERE snapshot_id=? AND team_id=?", (snap[0], team_id))
        if (r[2] or "BN") not in bw.BENCH}


def cant_play(week_file):
    """My current Yahoo starters (per the week file's yahoo_slot) whose status gives them no chance to play: a bye, an
    out status (IR / O / NA / ... -- Yahoo's out codes count, build_week.py), or Out on the injury report. Locked
    players are left out (nothing to change)."""
    return [f"{p['name']} ({p['status']})" for p in week_file["my_team"]["players"]
            if (p.get("yahoo_slot") or "BN") not in bw.BENCH and not p.get("locked") and p.get("status")
            and p.get("play_prob") is None and bw.play_chance(p["status"]) == 0]


def compare(week_file, report, yahoo_starters):
    """None when the recommended starters are Yahoo's; else {start, bench, margin, text}."""
    rec = {s["player"]["id"]: s["player"]["name"] for s in report["lineup"] if s.get("player")}
    names = {p["id"]: p["name"] for p in week_file["my_team"]["players"]}
    start = [rec[i] for i in rec if i not in yahoo_starters]
    bench = [yahoo_starters[i][0] for i in yahoo_starters if i not in rec]
    if not start and not bench:
        return None
    unknown = [yahoo_starters[i][0] for i in yahoo_starters if i not in names]
    margin = None
    if not unknown:
        margin = lineup_margin(week_file, list(rec), list(yahoo_starters), report["n_sims"], report["seed"])
    if len(start) == 1 and len(bench) == 1:
        what = f"Start {start[0]} over {bench[0]}"
    else:
        what = "; ".join(filter(None, [f"start {', '.join(start)}" if start else "",
                                       f"bench {', '.join(bench)}" if bench else ""]))
        what = what[0].upper() + what[1:]
    if margin is None:
        how = f"strength unknown: {', '.join(unknown)} (starting on Yahoo) has no projection in the file"
    elif margin["unit"] == "win%":
        how = (f"{margin['label']} (+{margin['delta'] * 100:.1f} win% ±{margin['se'] * 100:.1f}; "
               f"{margin['other']:.0%} -> {margin['best']:.0%})")
    else:
        how = f"{margin['label']} (+{margin['delta']:.1f} pts ±{margin['se']:.1f})"
    return {"start": start, "bench": bench, "margin": margin, "text": f"{what} -- {how}"}


# ------------------------------------------------------------------ Yahoo's live points

LIVE_PATH = "team/{key}.t.{team}/roster;week={week}/players/stats;type=week;week={week}?format=json"
LIVE_BUDGET_S = 90


def fetch_live_points(week, team_ids, league_key=None, session=None, budget_s=LIVE_BUDGET_S):
    """Yahoo's points so far this week for every player on these teams' rosters -- the same call league_data.py makes
    for a completed week's final lineup, made for the week in progress. Read-only. -> {ok, points {yahoo id: points},
    error, raw_bytes}. Never raises, and gives up after budget_s (the token refresh has no timeout of its own)."""
    box = {}

    def work():
        out = {"ok": False, "points": {}, "error": None, "raw_bytes": None}
        key = league_key or league_availability.configured_league_key()
        try:
            if not key:
                raise RuntimeError("no league is configured (YAHOO_LEAGUE_KEY in league/.env)")
            sess = session or league_availability.open_session()
            pages = []
            for team in team_ids:
                resp = sess.get(ld.API + LIVE_PATH.format(key=key, team=team, week=week), timeout=30)
                pages.append({"team_id": team, "http_status": resp.status_code, "body": resp.text})
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code} for team {team}")
            out["raw_bytes"] = json.dumps({"week": week, "pages": pages}, sort_keys=True).encode("utf-8")
            rows = ld.parse_lineups([json.loads(pg["body"]) for pg in pages])
            out["points"] = {r["external_id"]: r["points"] for r in rows if r["week"] == week}
            out["ok"] = True
        except (Exception, SystemExit) as e:  # noqa: BLE001 -- a missing view, not a failed run
            out["error"] = f"{type(e).__name__}: {e}".replace(key or "<none>", "<league>")
        box["out"] = out

    import threading
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(budget_s)
    return box.get("out") or {"ok": False, "points": {}, "error": f"no answer from Yahoo within {budget_s}s", "raw_bytes": None}


# ------------------------------------------------------------------ notifications

TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$t = [Security.SecurityElement]::Escape($env:FAAB_TOAST_TITLE)
$b = [Security.SecurityElement]::Escape($env:FAAB_TOAST_TEXT)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>")
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""
BALLOON_PS = r"""
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(15000, $env:FAAB_TOAST_TITLE, $env:FAAB_TOAST_TEXT, 'Info')
Start-Sleep -Seconds 16
$n.Dispose()
"""


def notify(title, text):
    """A Windows notification that doesn't wait for anyone (a toast; a tray balloon if toasts fail).
    -> what was shown, for the log. Never raises."""
    env = dict(os.environ, FAAB_TOAST_TITLE=title, FAAB_TOAST_TEXT=text)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def ps(script):
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
                base64.b64encode(script.encode("utf-16-le")).decode("ascii")]
    try:
        p = subprocess.run(ps(TOAST_PS), env=env, capture_output=True, text=True, timeout=30, creationflags=flags)
        if p.returncode == 0:
            return "toast"
        subprocess.Popen(ps(BALLOON_PS), env=env, creationflags=flags | getattr(subprocess, "DETACHED_PROCESS", 0),
                         close_fds=True)
        return f"balloon (toast failed: {(p.stderr or '').strip()[:200]})"
    except Exception as e:  # noqa: BLE001 -- the log line still records the result
        return f"not shown ({type(e).__name__}: {e})"


# ------------------------------------------------------------------ the summary log (scheduled_run.py's)

def handled_keys(log_dir):
    try:
        lines = (Path(log_dir) / sr.SUMMARY_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {tok[4:] for ln in lines if len(ln.split()) > 2 and ln.split()[2] == RUN_JOB for tok in ln.split() if tok.startswith("key=")}


def write_line(log_dir, now_local, job, status, rc, summary, detail=None):
    line = f"{now_local:%Y-%m-%d %H:%M:%S}  {job:9} {status:6} exit={rc}  {summary}" + (f"  | {detail.name}" if detail else "")
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(log_dir) / sr.SUMMARY_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return line


# ------------------------------------------------------------------ plan job

def build_plan(con, now, season, download=_download, raw_dir=bw.DEFAULT_RAW_DIR, league_raw_dir=LEAGUE_RAW_DIR,
               horizon=HORIZON, cache=SCHEDULE_CACHE, tz=None, assume_pending=False):
    team_id = ld.default_team(con)
    if team_id is None:
        raise RuntimeError("no team is marked as mine in the newest teams snapshot")
    games, source = load_schedule(season, con, now, download=download, cache=cache,
                                  nflverse_file=Path(raw_dir) / "games.csv")
    if not games:
        raise RuntimeError(f"no {season} schedule from any source ({source})")
    end_week, end_source = league_end_week(con, league_raw_dir)
    upcoming = sorted({w for w, k, _h, _a in games if k > now})
    week = upcoming[0] if upcoming else max(w for w, *_ in games)
    my_teams, roster_taken = my_playing_roster(con, team_id, season, week, Path(raw_dir) / f"roster_weekly_{season}.csv")
    entries, skipped = compute_plan(games, my_teams, matchup_status(con, team_id), end_week, now, source, horizon, tz, season,
                                    assume_pending)
    return {"computed_at": now.isoformat(), "schedule_source": source, "end_week": end_week, "end_week_source": end_source,
            "team_id": team_id, "roster_snapshot_taken_at": roster_taken, "entries": entries,
            "weeks_without_runs": {str(k): v for k, v in skipped.items()}}


def carry_over(old_plan, new_plan, now, handled):
    """Keep the previous plan's runs that fell due and were never handled, so the run task can still report them as
    MISSED (the plan only looks forward)."""
    keys = {e["key"] for e in new_plan["entries"]}
    kept = [e for e in (old_plan or {}).get("entries", [])
            if e["key"] not in keys and e["key"] not in handled
            and now - KEEP_MISSED <= datetime.fromisoformat(e["run_at"]) <= now]
    new_plan["entries"] = sorted(kept + new_plan["entries"], key=lambda e: e["run_at"])
    return new_plan


def read_plan(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_plan(path, plan):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def describe(entry):
    when = datetime.fromisoformat(entry["run_at_local"])
    lock = datetime.fromisoformat(entry["lock_local"])
    what = "preliminary" if entry["kind"] == "preliminary" else f"pre-lock {lock:%H:%M} ({', '.join(entry['games'])})"
    return f"{when:%a %m-%d %H:%M} w{entry['week']:02d} {what}"


# ------------------------------------------------------------------ task registration

TASK_SETTINGS_START, TASK_SETTINGS_END = "<Settings>", "</Settings>"


def _settings_block():
    """The very <Settings> block scheduled_run.py gives the other tasks (WakeToRun, catch-up, no network gate, ...)."""
    xml = sr.task_xml("snapshot", "user", datetime(2026, 1, 1))
    return xml[xml.index(TASK_SETTINGS_START): xml.index(TASK_SETTINGS_END) + len(TASK_SETTINGS_END)]


def task_xml(job, user, triggers_xml, logon_delay):
    args = f'"{Path(__file__).resolve()}" {"plan --register" if job == PLAN_JOB else "run"} --scheduled'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(TASKS[job]['description'])}. Output: {escape(str(OUT_DIR))}; log: {escape(str(sr.LOG_DIR))}</Description>
  </RegistrationInfo>
  <Triggers>
{triggers_xml}    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>{logon_delay}</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  {_settings_block()}
  <Actions Context="Author">
    <Exec>
      <Command>{escape(sr.windowless_python())}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(REPO_ROOT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def plan_task_xml(user, now_local):
    start = now_local.replace(hour=6, minute=45, second=0, microsecond=0)
    if start <= now_local:
        start += timedelta(days=1)
    trig = (f"    <CalendarTrigger>\n      <StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary>\n      <Enabled>true</Enabled>\n"
            f"      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>\n    </CalendarTrigger>\n")
    return task_xml(PLAN_JOB, user, trig, "PT2M")


def run_task_xml(user, entries, now):
    trig = "".join(f"    <TimeTrigger>\n      <StartBoundary>{e['run_at_local']}:00</StartBoundary>\n      <Enabled>true</Enabled>\n"
                   f"    </TimeTrigger>\n" for e in entries if datetime.fromisoformat(e["run_at"]) > now)
    return task_xml(RUN_JOB, user, trig, "PT4M")


def task_path(job):
    return f"\\{sr.TASK_FOLDER}\\{TASKS[job]['task']}"


def register(job, xml):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "task.xml"
        path.write_text(xml, encoding="utf-16")
        p = subprocess.run(["schtasks", "/Create", "/TN", task_path(job), "/XML", str(path), "/F"], capture_output=True, text=True)
    return p.returncode, (p.stdout or p.stderr).strip()


def task_running(job=None, path=None):
    p = subprocess.run(["schtasks", "/Query", "/TN", path or task_path(job), "/FO", "LIST"], capture_output=True, text=True)
    return p.returncode == 0 and any(ln.split(":", 1)[0].strip() == "Status" and "Running" in ln for ln in p.stdout.splitlines())


def log_job_running():
    """Is a scheduled tracker log job running right now (Task Scheduler's own state, so a crashed run leaves nothing
    stale)? A `log` run by hand isn't seen -- the DB is in WAL mode and every connection waits 30 s for a lock."""
    return any(task_running(path=f"\\{sr.TASK_FOLDER}\\{t}") for t in LOG_TASKS)


def wait_for_log_jobs(until, running=None, sleep=None, clock=None):
    """Wait while a log job runs, until `until`. -> (clear, seconds waited)."""
    running, sleep, clock = running or log_job_running, sleep or time.sleep, clock or utcnow
    start = clock()
    while running():
        if clock() >= until:
            return False, (clock() - start).total_seconds()
        sleep(WAIT_POLL_S)
    return True, (clock() - start).total_seconds()


def whoami():
    return subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()


# ------------------------------------------------------------------ run job

def due_entries(plan, now, handled):
    return [e for e in (plan or {}).get("entries", []) if datetime.fromisoformat(e["run_at"]) <= now + timedelta(minutes=1)
            and e["key"] not in handled]


def triage(due, now):
    """-> [(entry, 'missed' | 'superseded' | 'run')] in time order: an entry whose lock is less than MIN_LEAD away (or
    past) is missed; of the rest only the latest runs, the earlier ones are superseded by it."""
    out, runnable = [], [e for e in due if datetime.fromisoformat(e["lock"]) - now >= MIN_LEAD]
    last = runnable[-1]["key"] if runnable else None
    for e in sorted(due, key=lambda e: e["run_at"]):
        if e not in runnable:
            out.append((e, "missed"))
        else:
            out.append((e, "run" if e["key"] == last else "superseded"))
    return out


def snapshot_age(con, now):
    snap = ld.latest_ok(con, "rosters")
    return None if snap is None else now - datetime.fromisoformat(snap[1])


def write_live_points(entry, now, db, out_dir, fetch=None):
    """Fetch my team's and my opponent's live points for a pre-lock run and keep them beside its report.
    -> (path or None, note for the log)."""
    con = bw.connect_ro(db)
    try:
        me = ld.default_team(con)
        opp = bw.opponent_of(con, entry["week"], me) if me is not None else None
    finally:
        con.close()
    if me is None:
        return None, "live points: no team of mine in the snapshot"
    got = (fetch or fetch_live_points)(entry["week"], [t for t in (me, opp) if t is not None])
    if not got["ok"]:
        return None, f"live points unavailable ({got['error']}) -- locked players are scored from nflverse or projected"
    stem = output_stem(entry, out_dir)
    path = Path(f"{stem}_live.json")
    path.write_text(json.dumps({"fetched_at": now.isoformat(timespec="seconds"), "week": entry["week"], "team_ids": [me, opp],
                                "points": got["points"]}, indent=1), encoding="utf-8")
    if got.get("raw_bytes"):
        Path(f"{stem}_live_raw.json.gz").write_bytes(gzip.compress(got["raw_bytes"]))
    return path, f"live points: {len(got['points'])} player(s) from Yahoo -> {path.name}"


def run_entry(entry, now, db, raw_dir, league_raw_dir, out_dir, run=sr.run_step, as_of=None, take_snapshot=None,
              live=None):
    """-> (rc, summary, detail text, alert or None). Steps: league snapshot (always for a pre-lock run; for the
    preliminary only if the newest is over an hour old), build_week.py --run, then the comparison."""
    py = sr.console_python()
    parts = []
    if take_snapshot is None:
        con = bw.connect_ro(db)
        try:
            age = snapshot_age(con, now)
        finally:
            con.close()
        take_snapshot = entry["kind"] == "prelock" or age is None or age > SNAPSHOT_FRESH
    if take_snapshot:
        argv = [py, str(REPO_ROOT / "league" / "league_data.py"), "snapshot", "--db", str(db), "--raw-dir", str(league_raw_dir)]
        code, out = run(argv)
        parts.append(f"\n## league snapshot\n$ {' '.join(argv)}\n{out.rstrip()}\n[exit {code}]\n")
        if code != 0:
            return code, f"[STARTSIT] the league snapshot failed (exit {code}) -- no report built", "".join(parts), None
    stem = output_stem(entry, out_dir)
    stem.parent.mkdir(parents=True, exist_ok=True)
    live_file = None
    if entry["kind"] == "prelock" and live is not False:
        live_file, note = write_live_points(entry, now, db, out_dir, fetch=live)
        parts.append(f"\n## {note}\n")
    argv = [py, str(HERE / "build_week.py"), "--season", str(entry["season"]), "--week", str(entry["week"]),
            "--stage", "thursday" if entry["kind"] == "preliminary" else "sunday", "--run", "--db", str(db),
            "--raw-dir", str(raw_dir), "--out", f"{stem}.json"] + (["--as-of", as_of.isoformat()] if as_of else []) \
        + (["--live-points", str(live_file)] if live_file else [])
    code, out = run(argv)
    parts.append(f"\n## build_week\n$ {' '.join(argv)}\n{out.rstrip()}\n[exit {code}]\n")
    if code != 0:
        why = next((ln.strip() for ln in out.splitlines() if "NOT BUILT" in ln), f"build_week.py exit {code}")
        return code, f"[STARTSIT] {why}", "".join(parts), None
    week_file = json.loads(Path(f"{stem}.json").read_text(encoding="utf-8"))
    report = json.loads(Path(f"{stem}_report.json").read_text(encoding="utf-8"))
    con = bw.connect_ro(db)
    try:
        diff = compare(week_file, report, current_yahoo_starters(con, week_file["_meta"]["team_id"]))
    finally:
        con.close()
    win = report.get("win_prob")
    head = describe(entry) + (f", win prob {win:.0%}" if win is not None else "")
    out = cant_play(week_file)
    lock = datetime.fromisoformat(entry["lock_local"])
    if out:
        parts.append(f"\n## starting on Yahoo but can't play\n{', '.join(out)}\n")
    if entry["kind"] == "preliminary":
        # the preliminary alerts only when a current starter can't play at all; a mere difference waits for pre-lock
        alert = ((f"Start/sit week {entry['week']} (preliminary): a starter can't play",
                  f"In your Yahoo lineup but can't play: {', '.join(out)}. Report: {stem.name}_report.md") if out else None)
        what = ("matches Yahoo's" if diff is None else f"differs from Yahoo -- {diff['text']}") + \
               (f"; can't play: {', '.join(out)}" if out else "")
        return 0, f"[STARTSIT] {head}: recommended lineup {what} -- {stem.name}_report.md", "".join(parts), alert
    if diff is None:
        return 0, f"[STARTSIT] {head}: recommended lineup matches Yahoo's -- {stem.name}_report.md", "".join(parts), None
    parts.append(f"\n## differs from the Yahoo lineup\n{diff['text']}\n")
    text = diff["text"] + (f". Can't play: {', '.join(out)}" if out else "")
    alert = (f"Start/sit week {entry['week']}: change before {lock:%a %H:%M}", f"{text}. Report: {stem.name}_report.md")
    return 0, f"[STARTSIT] {head}: DIFFERS from Yahoo -- {text} -- {stem.name}_report.md", "".join(parts), alert


def cmd_run(args):
    now = datetime.fromisoformat(args.as_of) if args.as_of else utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    log_dir, now_local = Path(args.log_dir), to_local(now)
    plan = read_plan(args.plan_file)
    handled = handled_keys(log_dir)
    work = triage(due_entries(plan, now, handled), now)
    if not work:
        print("[STARTSIT] nothing due")
        return 0
    rc_all, notes = 0, []
    alert = sr.show_alert if not args.no_popup else (lambda *a: print("[POPUP]", *a))
    toast = notify if not args.no_notify else (lambda t, x: print("[NOTIFY]", t, "|", x) or "printed")
    missed = []
    for entry, verdict in work:
        tag = f"key={entry['key']}"
        if verdict == "missed":
            missed.append(entry)
            print(write_line(log_dir, now_local, RUN_JOB, "MISSED", 3, f"[STARTSIT] {describe(entry)}: not run -- its kickoff "
                             f"was less than {MIN_LEAD.seconds // 60} minutes away or past when the task ran (PC asleep or off) {tag}"))
            continue
        if verdict == "superseded":
            print(write_line(log_dir, now_local, RUN_JOB, "SKIP", 0, f"[STARTSIT] {describe(entry)}: superseded by a later due run {tag}"))
            continue
        clear, waited = wait_for_log_jobs(datetime.fromisoformat(entry["lock"]) - MIN_LEAD - RUN_BUDGET)
        if not clear:
            missed.append(entry)
            print(write_line(log_dir, now_local, RUN_JOB, "MISSED", 3,
                             f"[STARTSIT] {describe(entry)}: not run -- a tracker log job was still running {waited / 60:.0f} "
                             f"minutes later and the log job comes first {tag}"))
            continue
        if waited and not args.as_of:
            now = utcnow()
            now_local = to_local(now)
        detail = log_dir / f"{now_local:%Y%m%d_%H%M%S}_{RUN_JOB}.log"
        rc, summary, text, diff_alert = run_entry(entry, now, args.db, args.raw_dir, args.league_raw_dir, args.out_dir,
                                                  as_of=now if args.as_of else None,
                                                  take_snapshot=False if args.no_snapshot else None,
                                                  live=False if getattr(args, "no_live", False) else None)
        shown = toast(*diff_alert) if diff_alert else None
        detail.write_text(f"# {RUN_JOB} {entry['key']} started {now_local:%Y-%m-%d %H:%M:%S} (local)"
                          + (f" after waiting {waited:.0f} s for a tracker log job" if waited else "") + f"\n{text}"
                          + (f"\n# notification: {shown}\n" if shown else "") + f"\n# {'OK' if rc == 0 else 'FAILED'} (exit {rc})\n",
                          encoding="utf-8")
        print(write_line(log_dir, now_local, RUN_JOB, "OK" if rc == 0 else "FAILED", rc, f"{summary} {tag}", detail))
        if rc != 0:
            rc_all = rc
            alert(f"FAAB start/sit: {entry['kind']} run FAILED", f"{summary}\n\nNothing on Yahoo was changed (this job never "
                  f"changes a lineup). Set the lineup by hand if needed.\nFull output: {detail}")
        elif entry["kind"] == "prelock" and not args.no_register:
            notes.append(replan_if_needed(args, plan, now))
    if missed:
        toast("Start/sit: run(s) missed", "Not run because the PC was asleep or off: "
              + "; ".join(describe(e) for e in missed) + ". Check those lineups by hand if their games haven't locked.")
    for n in filter(None, notes):
        print(n)
    return rc_all


def replan_if_needed(args, plan, now):
    """After a pre-lock run's fresh snapshot: if my roster now calls for a different set of runs, ask the plan task
    to run again (it re-registers this task once this run has ended)."""
    try:
        con = bw.connect_ro(args.db)
        try:
            fresh = build_plan(con, now, now.year if now.month >= 3 else now.year - 1, download=None, raw_dir=args.raw_dir,
                               league_raw_dir=args.league_raw_dir, cache=Path(args.out_dir) / "games.csv")
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001 -- the next daily plan still runs
        return f"[STARTSIT] re-plan check failed ({type(e).__name__}: {e}); tomorrow's 06:45 plan will catch up"
    old = {(e["key"], e["run_at"]) for e in plan.get("entries", []) if datetime.fromisoformat(e["run_at"]) > now}
    new = {(e["key"], e["run_at"]) for e in fresh["entries"]}
    if old == new:
        return None
    plan["replan_requested"] = now.isoformat()
    write_plan(args.plan_file, plan)
    p = subprocess.run(["schtasks", "/Run", "/TN", task_path(PLAN_JOB)], capture_output=True, text=True)
    return f"[STARTSIT] the roster now needs a different set of runs; plan task started ({(p.stdout or p.stderr).strip()})"


def cmd_plan(args):
    now = datetime.fromisoformat(args.as_of) if args.as_of else utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_local, log_dir = to_local(now), Path(args.log_dir)
    old = read_plan(args.plan_file)
    if args.scheduled and args.register and not (old or {}).get("replan_requested"):
        if _handled_today(log_dir, now_local):
            print(f"[SKIP] {PLAN_JOB}: today's plan is already in {sr.SUMMARY_LOG}")
            return 0
    season = args.season or (now.year if now.month >= 3 else now.year - 1)
    if args.register:
        clear, waited = wait_for_log_jobs(utcnow() + PLAN_WAIT)
        if waited:
            print(f"[STARTSIT PLAN] waited {waited:.0f} s for a tracker log job" + ("" if clear else "; going ahead (read-only)"))
    try:
        con = bw.connect_ro(args.db)
        try:
            plan = build_plan(con, now, season, download=None if args.no_download else _download, raw_dir=args.raw_dir,
                              league_raw_dir=args.league_raw_dir, horizon=None if args.table else HORIZON,
                              cache=Path(args.out_dir) / "games.csv", assume_pending=args.table)
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        if args.register:
            line = write_line(log_dir, now_local, PLAN_JOB, "FAILED", 1, f"[STARTSIT PLAN] {type(e).__name__}: {e}")
            print(line)
            if not args.no_popup:
                sr.show_alert("FAAB start/sit: plan FAILED", f"{e}\n\nThe runs registered earlier stay as they were.")
        else:
            print(f"[STARTSIT PLAN] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    print(f"[STARTSIT PLAN] schedule: {plan['schedule_source']}; end_week {plan['end_week']} ({plan['end_week_source']}); "
          f"roster snapshot {plan['roster_snapshot_taken_at']}")
    for w, why in plan["weeks_without_runs"].items():
        print(f"  week {w}: no runs -- {why}")
    for e in plan["entries"]:
        print(f"  {describe(e)}" + (f"  [{', '.join(e['players'])}]" if e["players"] else "")
              + ("  (if my team has a matchup)" if e.get("matchup_assumed") else ""))
    if not args.register:
        if getattr(args, "write", False):
            write_plan(args.plan_file, carry_over(old, plan, now, handled_keys(log_dir)))
            print(f"[STARTSIT PLAN] written to {args.plan_file} (nothing registered)")
        return 0
    plan = carry_over(old, plan, now, handled_keys(log_dir))
    while task_running(RUN_JOB):              # never replace the run task's definition under a running instance
        if (utcnow() - now) > timedelta(minutes=5):
            break
        time.sleep(15)
    rc, msg = register(RUN_JOB, run_task_xml(whoami(), plan["entries"], now))
    write_plan(args.plan_file, plan)
    upcoming = [e for e in plan["entries"] if datetime.fromisoformat(e["run_at"]) > now]
    summary = (f"[STARTSIT PLAN] {len(upcoming)} run(s) in the next {HORIZON.days} days"
               + (f": {'; '.join(describe(e) for e in upcoming)}" if upcoming else "") + f" | schedule: {plan['schedule_source']}")
    print(write_line(log_dir, now_local, PLAN_JOB, "OK" if rc == 0 else "FAILED", rc,
                     summary if rc == 0 else f"[STARTSIT PLAN] could not register {task_path(RUN_JOB)}: {msg}"))
    if rc != 0 and not args.no_popup:
        sr.show_alert("FAAB start/sit: plan FAILED", f"Could not register {task_path(RUN_JOB)}: {msg}")
    return rc


def _handled_today(log_dir, now_local):
    """The plan job's own already-handled check: a line since today's 06:45 (or yesterday's, before 06:45)."""
    due = now_local.replace(hour=6, minute=45, second=0, microsecond=0)
    if due > now_local + timedelta(minutes=1):
        due -= timedelta(days=1)
    try:
        lines = (Path(log_dir) / sr.SUMMARY_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for ln in lines:
        parts = ln.split()
        if len(parts) > 2 and parts[2] == PLAN_JOB:
            try:
                if datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S") >= due:
                    return True
            except ValueError:
                continue
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "table", "run"):
        p = sub.add_parser(name)
        p.add_argument("--db", default=str(bw.DEFAULT_DB))
        p.add_argument("--raw-dir", default=str(bw.DEFAULT_RAW_DIR))
        p.add_argument("--league-raw-dir", default=str(LEAGUE_RAW_DIR))
        p.add_argument("--out-dir", default=str(OUT_DIR))
        p.add_argument("--plan-file", default=str(PLAN_FILE))
        p.add_argument("--log-dir", default=str(sr.LOG_DIR))
        p.add_argument("--as-of", default=None, help="ISO instant to act as of (testing / dry runs)")
        p.add_argument("--scheduled", action="store_true", help="what the tasks pass")
        p.add_argument("--no-popup", action="store_true")
        if name in ("plan", "table"):
            p.add_argument("--season", type=int, default=None)
            p.add_argument("--no-download", action="store_true", help="use the schedule on disk / in the DB")
        if name == "plan":
            p.add_argument("--register", action="store_true", help="register the run task (what the daily task does)")
            p.add_argument("--write", action="store_true", help="write the plan file without registering anything (dry runs)")
        if name == "run":
            p.add_argument("--no-snapshot", action="store_true", help="dry runs: build from the snapshot already in --db")
            p.add_argument("--no-notify", action="store_true", help="print the notification instead of showing it")
            p.add_argument("--no-register", action="store_true", help="never start the plan task")
            p.add_argument("--no-live", action="store_true", help="don't fetch Yahoo's live points")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        return cmd_run(args)
    args.table = args.cmd == "table"
    if args.table:
        args.register = False
    return cmd_plan(args)


if __name__ == "__main__":
    sys.exit(main())
