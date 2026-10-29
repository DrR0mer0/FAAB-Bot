#!/usr/bin/env python3
"""The tracker's Windows Task Scheduler jobs: the runner each task calls, and
the commands that (un)register the tasks.

  python tracker/scheduled_run.py install        # register the four tasks below (re-run any time; it replaces them)
  python tracker/scheduled_run.py install --job league   # ...or just one of them
  python tracker/scheduled_run.py install --job startsit # the start/sit tasks (startsit/autorun.py; not part of a plain install)
  python tracker/scheduled_run.py status
  python tracker/scheduled_run.py uninstall
  python tracker/scheduled_run.py run snapshot   # what a task runs; also fine by hand

  job        when (local time)   what it runs
  snapshot   every day 06:30     tracker.py snapshot-ownership
  log-thu    Thursdays 07:00     data/fetch_weekly_update.py, then tracker.py log --auto --slot thu,
                                 then tracker.py check-stats (advisory only -- see below)
  log-sun    Sundays   07:00     data/fetch_weekly_update.py, then tracker.py log --auto --slot sun
  league     every day 06:35     league/league_data.py snapshot (the user's own Yahoo league: transactions, FAAB,
                                 rosters, scores), then league_data.py report. Separate from the tracker: no
                                 prediction run reads it, and its failure affects nothing else.
  startsit   (from 2026 week 9; startsit/autorun.py, registered with --job startsit) two tasks whose triggers are
             computed, not fixed: startsit-plan (daily 06:45) plans the week's preliminary and pre-lock start/sit
             runs from the schedule and my roster, and registers them as one-shot triggers on startsit-run. Same
             settings as the tasks above; see startsit/autorun.py and startsit/README.md.

Every run writes its full output to tracker/logs/<timestamp>_<job>.log and one
summary line to tracker/logs/scheduled_runs.log (both gitignored).

The log jobs refresh the nflverse files first because `log` refuses on a
weekly-roster file more than 3 days old -- without the refresh a Sunday run
could never pass. A failed refresh is noted and the log is still attempted: the
tracker's own freshness guards decide. Nothing here loads the DB; the weekly
loader / fetch-snaps cycle stays a manual step, and `log` refuses if it's behind.

STAT CORRECTIONS. After its log step the Thursday job also runs `tracker.py
check-stats`: has nflverse corrected the stats of a week that is already
scored? If so a message box shows which week and the `score --force` command
to append a corrected scoring. This is advisory only -- it never changes the
job's exit status or its OK/FAILED line (it adds a note to that line), it runs
whether the log step logged, skipped or refused, and a check that can't run at
all is just noted.

WHEN A TASK STARTS (changed 2026-10-14). Each task has two triggers: its
time of day, and the user's sign-in (2 minutes after it). The sign-in trigger
exists because a task "run only when the user is logged on" cannot run while
the PC sits at the sign-in screen -- on 2026-10-14 a Windows Update restart at
04:09 left it there until 08:49, both daily jobs were missed, and "run as soon
as possible after a missed start" did not catch them up at sign-in (a missed
start is only caught up when the scheduler can run it, e.g. on resume from
hibernate into a signed-in session, as on 2026-10-05). A task also wakes the
PC from hibernate (WakeToRun; this PC allows wake timers on AC), and no longer
waits for "a network connection": a run without network fails loudly, where a
network-gated skip was silent. In Modern Standby the tasks already ran on time.

The tasks call `run <job> --scheduled`. A scheduled run first asks whether the
job's most recent due time (today's, once it has passed; else the previous
scheduled day's) already has a line in scheduled_runs.log -- OK, FAILED, SKIP
or MISSED alike. If so it exits 0 and writes nothing, so a sign-in never
repeats a job that ran, and never repeats a failure box. Otherwise it runs as
before. That also means a log job's MISSED box appears once per missed slot,
not at every sign-in. A run by hand (no --scheduled) always runs.

FAILING LOUDLY BUT HARMLESSLY. `log --auto` resolves the week itself and skips
quietly (exit 0) when the week has no such slot or the slot is already in the
ledger. Everything else is a failure: a non-zero exit, a FAILED line in
scheduled_runs.log, and a message box that stays up until dismissed. None of
the failure paths writes to the ledger:
  * the PC was asleep/off and Task Scheduler ran the task late on the same day
    -- `log` checks the real kickoff instants and refuses if the window closed;
  * it ran late on a DIFFERENT day (the tasks use "run as soon as possible
    after a missed start") -- a log job only runs on its own weekday, because
    by then `--auto` would resolve to the NEXT week and log it days early;
  * stale data, a failed fetch, a crash, or a step that hangs past its timeout.
"""
import argparse
import ctypes
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape

TRACKER_DIR = Path(__file__).resolve().parent
REPO_ROOT = TRACKER_DIR.parent
LOG_DIR = TRACKER_DIR / "logs"
SUMMARY_LOG = "scheduled_runs.log"
TASK_FOLDER = "FAAB"
STEP_TIMEOUT_S = 40 * 60  # under the task's own 1-hour limit, so a hang is reported rather than silently killed
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# weekday: datetime.weekday() the job is allowed to run on (None = any day)
JOBS = {
    "snapshot": {"task": "tracker-snapshot-ownership", "weekday": None, "time": "06:30",
                 "description": "FAAB tracker: daily ESPN roster-% / Sleeper trending snapshot"},
    "log-thu": {"task": "tracker-log-thu", "weekday": 3, "time": "07:00", "slot": "thu",
                "description": "FAAB tracker: log the thu slot before the week's first kickoff (skips a week without one)"},
    "log-sun": {"task": "tracker-log-sun", "weekday": 6, "time": "07:00", "slot": "sun",
                "description": "FAAB tracker: log the sun slot before the first 1 PM ET Sunday kickoff"},
    "league": {"task": "league-snapshot", "weekday": None, "time": "06:35",
               "description": "FAAB: daily snapshot of the Yahoo league (transactions, FAAB, rosters, scores) and its activity report"},
}
HEADLINE_TAGS = ("[LOGGED]", "[SKIP]", "[REFUSED]", "[OWNERSHIP]", "[LEAGUE DATA]")
# Registered by startsit/autorun.py (install --job startsit), not from JOBS: their triggers are computed.
STARTSIT_JOB = "startsit"
STARTSIT_TASKS = ("startsit-plan", "startsit-run")


def console_python():
    """python.exe even when this runs under pythonw.exe (which the tasks use so
    no console window flashes up): the steps' output is piped to the log."""
    exe = Path(sys.executable)
    return str(exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe)


def windowless_python():
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def default_season(now):
    return now.year if now.month >= 3 else now.year - 1


STATS_CHANGED_TAG = "[STATS CHANGED]"


def job_steps(job, now):
    """[(label, argv, role)]. role: 'decides' -- this step's exit code and
    headline are the run's; 'prep' -- a failure is noted, nothing more;
    'advisory' -- may raise its own message box but never affects the run's
    outcome."""
    py = console_python()
    tracker = [py, str(TRACKER_DIR / "tracker.py")]
    season = str(default_season(now))
    if job == "snapshot":
        return [("snapshot-ownership", tracker + ["snapshot-ownership"], "decides")]
    if job == "league":
        league = [py, str(REPO_ROOT / "league" / "league_data.py")]
        return [("league snapshot", league + ["snapshot"], "decides"), ("league report", league + ["report"], "prep")]
    steps = [
        ("refresh nflverse files", [py, str(REPO_ROOT / "data" / "fetch_weekly_update.py"), "--season", season], "prep"),
        (f"log --auto --slot {JOBS[job]['slot']}", tracker + ["log", "--auto", "--slot", JOBS[job]["slot"]], "decides"),
    ]
    if job == "log-thu":
        steps.append(("check-stats", tracker + ["check-stats", "--season", season], "advisory"))
    return steps


def run_step(argv):
    """-> (exit code, combined output). Never raises."""
    try:
        p = subprocess.run(argv, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                           encoding="utf-8", errors="replace", timeout=STEP_TIMEOUT_S,
                           env=dict(os.environ, PYTHONUTF8="1"),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, p.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
        return 124, out + f"\n[TIMEOUT] step exceeded {STEP_TIMEOUT_S // 60} minutes and was stopped\n"
    except Exception as e:  # noqa: BLE001 -- e.g. the interpreter path is gone
        return 127, f"[ERROR] could not start the step: {type(e).__name__}: {e}\n"


def headline(output):
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    tagged = [ln for ln in lines if ln.startswith(HEADLINE_TAGS)]
    return (tagged or lines or ["(no output)"])[-1]


def show_alert(title, text):
    """A message box in its own detached process, so the task itself can end
    (with its failing exit code) while the box waits to be dismissed."""
    try:
        subprocess.Popen([windowless_python(), str(Path(__file__).resolve()), "popup", title, text],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
    except Exception:  # noqa: BLE001 -- the log files already carry the failure
        pass


DUE_TOLERANCE = timedelta(minutes=1)   # a trigger that fires a moment early still counts as that day's run


def due_instant(job, now):
    """The job's most recent scheduled time at or before `now` (plus a minute's
    tolerance): today's once it has passed, else the previous scheduled day's."""
    h, m = (int(x) for x in JOBS[job]["time"].split(":"))
    weekday = JOBS[job]["weekday"]
    due = now.replace(hour=h, minute=m, second=0, microsecond=0)
    while due > now + DUE_TOLERANCE or (weekday is not None and due.weekday() != weekday):
        due -= timedelta(days=1)
    return due


def already_handled(job, now, log_dir=None):
    """True when scheduled_runs.log has a line for `job` at or after its most
    recent due time -- whatever that run's outcome. A missing or unreadable
    log counts as nothing handled (the job then runs)."""
    since = due_instant(job, now)
    try:
        lines = (Path(log_dir or LOG_DIR) / SUMMARY_LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for ln in lines:
        parts = ln.split()
        if len(parts) < 3 or parts[2] != job:
            continue
        try:
            when = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if when >= since:
            return True
    return False


def run_job(job, now=None, log_dir=LOG_DIR, run=run_step, alert=show_alert, force_day=False):
    """Run one job; returns its exit code (0 = done or nothing to do)."""
    now = now or datetime.now()
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    detail = log_dir / f"{now:%Y%m%d_%H%M%S}_{job}.log"
    parts, notes, rc, summary = [f"# {job} started {now:%Y-%m-%d %H:%M:%S} (local)\n"], [], 0, ""
    stats_changed = []

    weekday = JOBS[job]["weekday"]
    if weekday is not None and now.weekday() != weekday and not force_day:
        rc = 3
        summary = (f"MISSED: this job is scheduled for {WEEKDAYS[weekday]} mornings but is running on a "
                   f"{WEEKDAYS[now.weekday()]} (the PC was probably off or asleep). Nothing was run. If the slot is "
                   f"still open, log it by hand: python tracker/tracker.py log --season Y --week W --slot {JOBS[job]['slot']}")
        parts.append(summary + "\n")
    else:
        for label, argv, role in job_steps(job, now):
            code, out = run(argv)
            parts.append(f"\n## {label}\n$ {' '.join(argv)}\n{out.rstrip()}\n[exit {code}]\n")
            if role == "decides":
                rc, summary = code, headline(out)
            elif role == "advisory":
                # check-stats exits 1 when something changed -- but so does a crash, so go by what it printed
                found = [ln.strip() for ln in out.splitlines() if ln.strip().startswith(STATS_CHANGED_TAG)]
                if found:
                    stats_changed += found
                    notes.append("stats changed for an already-scored week (see the message box)")
                elif code != 0:
                    notes.append(f"{label} could not run (exit {code})")
            elif code != 0:
                notes.append(f"{label} FAILED (exit {code})")

    status = "OK" if rc == 0 else "FAILED"
    line = f"{now:%Y-%m-%d %H:%M:%S}  {job:9} {status:6} exit={rc}  {summary}"
    if notes:
        line += "  | note: " + "; ".join(notes)
    line += f"  | {detail.name}"
    detail.write_text("".join(parts) + f"\n# {status} (exit {rc})\n", encoding="utf-8")
    with open(log_dir / SUMMARY_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if rc != 0:
        consequence = ("What Yahoo did answer is stored and the failed part is recorded as failed. No prediction run depends on this job."
                       if job == "league" else "Nothing was written to the ledger by this run.")
        alert(f"FAAB tracker: {job} FAILED",
              f"{summary}\n\n" + ("\n".join(notes) + "\n\n" if notes else "") + f"{consequence}\nFull output: {detail}")
    if stats_changed:
        alert("FAAB tracker: stats corrected for an already-scored week",
              "\n\n".join(ln[len(STATS_CHANGED_TAG):].strip() for ln in stats_changed)
              + f"\n\nThis is a heads-up only: it did not affect today's {job} run ({status}).\nFull output: {detail}")
    return rc


# ------------------------------------------------------------ task registration

def next_start(now, hhmm, weekday):
    h, m = (int(x) for x in hhmm.split(":"))
    start = now.replace(hour=h, minute=m, second=0, microsecond=0)
    while start <= now or (weekday is not None and start.weekday() != weekday):
        start += timedelta(days=1)
    return start


def task_xml(job, user, now, time_override=None):
    cfg = JOBS[job]
    start = next_start(now, time_override or cfg["time"], cfg["weekday"])
    if cfg["weekday"] is None:
        schedule = "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
    else:
        schedule = (f"<ScheduleByWeek><DaysOfWeek><{WEEKDAYS[cfg['weekday']]} /></DaysOfWeek>"
                    f"<WeeksInterval>1</WeeksInterval></ScheduleByWeek>")
    args = f'"{Path(__file__).resolve()}" run {job} --scheduled'
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(cfg['description'])}. Output: {escape(str(LOG_DIR))}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{start:%Y-%m-%dT%H:%M:%S}</StartBoundary>
      <Enabled>true</Enabled>
      {schedule}
    </CalendarTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT2M</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(windowless_python())}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(str(REPO_ROOT))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def task_name(job):
    return f"\\{TASK_FOLDER}\\{JOBS[job]['task']}"


def install_startsit(user, now):
    """The plan task (daily 06:45), then one plan run, which registers the run task with the coming runs."""
    sys.path.insert(0, str(REPO_ROOT / "startsit"))
    import autorun
    rc, msg = autorun.register(autorun.PLAN_JOB, autorun.plan_task_xml(user, now))
    print(f"{autorun.task_path(autorun.PLAN_JOB)}: {msg}")
    return rc or autorun.main(["plan", "--register"])


def cmd_install(args):
    user = subprocess.run(["whoami"], capture_output=True, text=True, check=True).stdout.strip()
    now, rc = datetime.now(), 0
    if args.job == STARTSIT_JOB:
        return install_startsit(user, now)
    for job in ([args.job] if args.job else JOBS):
        override = args.snapshot_time if job == "snapshot" else args.log_time if "slot" in JOBS[job] else None
        xml = task_xml(job, user, now, override)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "task.xml"
            path.write_text(xml, encoding="utf-16")
            p = subprocess.run(["schtasks", "/Create", "/TN", task_name(job), "/XML", str(path), "/F"],
                               capture_output=True, text=True)
        print(f"{task_name(job)}: {(p.stdout or p.stderr).strip()}")
        rc = rc or p.returncode
    return rc


def startsit_task_name(task):
    return f"\\{TASK_FOLDER}\\{task}"


def cmd_uninstall(_args):
    rc = 0
    for job in JOBS:
        p = subprocess.run(["schtasks", "/Delete", "/TN", task_name(job), "/F"], capture_output=True, text=True)
        print(f"{task_name(job)}: {(p.stdout or p.stderr).strip()}")
        rc = rc or p.returncode
    for task in STARTSIT_TASKS:      # registered only with --job startsit: absent is fine
        p = subprocess.run(["schtasks", "/Delete", "/TN", startsit_task_name(task), "/F"], capture_output=True, text=True)
        print(f"{startsit_task_name(task)}: {(p.stdout or p.stderr).strip()}")
    return rc


def cmd_status(_args):
    keep = ("TaskName", "Next Run Time", "Status", "Last Run Time", "Last Result", "Task To Run", "Schedule Type",
            "Start Time", "Days")
    for name in [task_name(job) for job in JOBS] + [startsit_task_name(t) for t in STARTSIT_TASKS]:
        p = subprocess.run(["schtasks", "/Query", "/TN", name, "/V", "/FO", "LIST"], capture_output=True, text=True)
        if p.returncode != 0:
            print(f"{name}: not registered ({p.stderr.strip()})")
            continue
        print("\n".join(ln for ln in p.stdout.splitlines() if ln.split(":")[0].strip() in keep) + "\n")
    summary = LOG_DIR / SUMMARY_LOG
    if summary.exists():
        print(f"Last runs ({summary}):")
        print("\n".join(summary.read_text(encoding="utf-8").splitlines()[-10:]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="run one job now (what the scheduled task calls)")
    p.add_argument("job", choices=sorted(JOBS))
    p.add_argument("--no-popup", action="store_true", help="on failure, log only -- no message box")
    p.add_argument("--force-day", action="store_true", help="run a log job even though today isn't its weekday")
    p.add_argument("--scheduled", action="store_true",
                   help="what the tasks pass: do nothing if this job's latest due run is already in scheduled_runs.log")
    p = sub.add_parser("install", help="register (or replace) the scheduled tasks for the current user")
    p.add_argument("--snapshot-time", default=None, help="HH:MM local (default 06:30)")
    p.add_argument("--log-time", default=None, help="HH:MM local for both log jobs (default 07:00)")
    p.add_argument("--job", choices=sorted(JOBS) + [STARTSIT_JOB], default=None,
                   help="register only this task and leave the others as they are (startsit: the two start/sit tasks)")
    sub.add_parser("uninstall", help="remove the scheduled tasks")
    sub.add_parser("status", help="show the tasks and the last runs")
    p = sub.add_parser("popup")  # internal: the detached failure message box
    p.add_argument("title")
    p.add_argument("text")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        if args.scheduled and already_handled(args.job, datetime.now()):
            if sys.stdout is not None:
                print(f"[SKIP] {args.job}: its latest due run is already in {SUMMARY_LOG} -- nothing to do")
            return 0
        rc = run_job(args.job, alert=(lambda *_: None) if args.no_popup else show_alert, force_day=args.force_day)
        if sys.stdout is not None:  # None under pythonw.exe
            print((LOG_DIR / SUMMARY_LOG).read_text(encoding="utf-8").splitlines()[-1])
        return rc
    if args.cmd == "popup":
        ctypes.windll.user32.MessageBoxW(None, args.text, args.title, 0x30 | 0x10000 | 0x40000)  # warning, foreground, topmost
        return 0
    return {"install": cmd_install, "uninstall": cmd_uninstall, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
