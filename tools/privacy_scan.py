#!/usr/bin/env python3
"""Pre-commit privacy scan: refuse a commit whose added lines name the user's
league or its managers. This repo is public; the league data stays local.

  python tools/privacy_scan.py                    # scan what is staged (what the hook runs)
  python tools/privacy_scan.py --diff-file x.diff # scan a saved diff instead (testing)
  python tools/privacy_scan.py --install-hook     # write .git/hooks/pre-commit to run it

THE TERMS are never written down anywhere in the repo: they are read at run
time from what is already local and gitignored --
  * every team name and every manager nickname the league snapshots have ever
    held (lg_teams, all snapshots: a renamed team's old AND new name);
  * every team name typed in league_data/manual/bids.csv (typos included);
  * the Yahoo league key and the bare league id (league/.env, or the
    YAHOO_LEAGUE_KEY environment variable);
  * this machine's user name and computer name.
Matching ignores case, spacing and apostrophe style (league_data.name_key:
"I'm" and "I’m" are the same), on word boundaries. A manager nickname is
often an ordinary first name, so a nickname hit that is part of an NFL
player's full name on the same line (ref_players) is not one. Terms shorter than MIN_TERM characters are not used.

OUTPUT never contains a term: a hit is reported as file, line, the kind of
term and its length. Exit 0 = clean, 1 = something matched (the commit is
refused), 2 = the term list could not be built (refused too: a scan that
can't see the league can't vouch for a commit).
"""
import argparse
import csv
import io
import os
import re
import socket
import sqlite3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "league"))
sys.path.insert(0, str(REPO_ROOT / "tracker"))
from league_data import name_key  # noqa: E402 -- the one way names are compared

DEFAULT_DB = REPO_ROOT / "faab_history_core_v0_1.db"
DEFAULT_BIDS = REPO_ROOT / "league_data" / "manual" / "bids.csv"
DEFAULT_ENV = REPO_ROOT / "league" / ".env"
MIN_TERM = 3
HOOK = """#!/bin/sh
# Installed by tools/privacy_scan.py --install-hook: refuse a commit whose added lines name the league or its managers.
exec python tools/privacy_scan.py
"""


class TermsUnavailable(Exception):
    pass


def league_key_from(env_path):
    key = os.environ.get("YAHOO_LEAGUE_KEY")
    if not key and Path(env_path).exists():
        m = re.search(r"^\s*YAHOO_LEAGUE_KEY\s*=\s*['\"]?([^\s'\"#]+)", Path(env_path).read_text(encoding="utf-8"), re.M)
        key = m.group(1) if m else None
    return key


def build_terms(db_path=DEFAULT_DB, bids_path=DEFAULT_BIDS, env_path=DEFAULT_ENV):
    """-> ({normalized term: kind}, {normalized nickname: [normalized NFL full names containing it as a word]})."""
    if not Path(db_path).exists():
        raise TermsUnavailable(f"no league database at {db_path}")
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        try:
            rows = con.execute("SELECT DISTINCT team_name, manager_nickname FROM lg_teams").fetchall()
        except sqlite3.Error as e:
            raise TermsUnavailable(f"the league snapshots can't be read ({e})") from e
        if not rows:
            raise TermsUnavailable("the league snapshots hold no teams yet")
        try:
            players = [r[0] for r in con.execute("SELECT full_name FROM ref_players WHERE full_name LIKE '% %'")]
        except sqlite3.Error:
            players = []
    finally:
        con.close()
    terms = {}

    def add(value, kind):
        k = name_key(value)
        if len(k) >= MIN_TERM and k not in terms:
            terms[k] = kind

    for team, nick in rows:
        if team:
            add(team, "team name")
        if nick:
            for part in str(nick).split(" / "):        # co-managers are stored joined
                add(part, "manager nickname")
    if Path(bids_path).exists():
        text = Path(bids_path).read_text(encoding="utf-8-sig")
        for r in csv.reader(io.StringIO(text)):
            if len(r) >= 5 and r[0].strip() and not r[0].lstrip().startswith("#") and r[0].strip().lower() != "award_date":
                for cell in (r[2], r[4]):
                    add(cell, "name typed in bids.csv")
    key = league_key_from(env_path)
    if key:
        add(key, "league key")
        if ".l." in key:
            add(key.split(".l.")[-1], "league id")
    for ident in (os.environ.get("USERNAME") or os.environ.get("USER"), os.environ.get("COMPUTERNAME") or socket.gethostname()):
        if ident:
            add(ident, "local identifier")
    nick_words = {k for k, kind in terms.items() if kind == "manager nickname"}
    in_players = defaultdict(list)
    for full in players:
        nf = name_key(full)
        for w in set(nf.split()) & nick_words:
            in_players[w].append(nf)
    return terms, dict(in_players)


def added_lines(diff_text):
    """[(file, line number, text)] for every added line of a unified diff."""
    out, path, n = [], None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            path = path[2:] if path.startswith("b/") else path
        elif line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            n = int(m.group(1)) if m else 0
        elif line.startswith("+") and path is not None:
            out.append((path, n, line[1:]))
            n += 1
        elif not line.startswith("-") and path is not None:
            n += 1
    return out


def scan(diff_text, terms, in_players):
    """[(file, line, kind, length)] -- one per term found on an added line."""
    patterns = {t: re.compile(r"(?<![\w])" + re.escape(t) + r"(?![\w])") for t in terms}
    hits = []
    for path, n, text in added_lines(diff_text):
        norm = name_key(text)
        for t, kind in terms.items():
            for m in patterns[t].finditer(norm):
                if kind == "manager nickname" and any(
                        p.start() <= m.start() < p.end() for full in in_players.get(t, ()) if full in norm
                        for p in re.finditer(r"(?<![\w])" + re.escape(full) + r"(?![\w])", norm)):
                    continue          # part of an NFL player's name on this line, e.g. a first name
                hits.append((path, n, kind, len(t)))
                break
    return hits


def staged_diff():
    p = subprocess.run(["git", "diff", "--cached", "--no-color", "-U0"], cwd=str(REPO_ROOT), capture_output=True)
    if p.returncode != 0:
        raise TermsUnavailable(f"git diff --cached failed: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.stdout.decode("utf-8", "replace")


def install_hook(git_dir=REPO_ROOT / ".git"):
    path = Path(git_dir) / "hooks" / "pre-commit"
    if path.exists() and path.read_text(encoding="utf-8") != HOOK:
        print(f"[PRIVACY] {path} already exists and is something else -- not overwritten", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HOOK, encoding="utf-8", newline="\n")
    try:
        path.chmod(0o755)
    except OSError:
        pass
    print(f"[PRIVACY] installed {path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--diff-file", default=None, help="scan this unified diff instead of the staged changes")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--bids", default=str(DEFAULT_BIDS))
    ap.add_argument("--env", default=str(DEFAULT_ENV))
    ap.add_argument("--install-hook", action="store_true", help="write .git/hooks/pre-commit to run this scan")
    args = ap.parse_args(argv)
    if args.install_hook:
        return install_hook()
    try:
        terms, in_players = build_terms(args.db, args.bids, args.env)
        diff = Path(args.diff_file).read_text(encoding="utf-8") if args.diff_file else staged_diff()
    except TermsUnavailable as e:
        print(f"[PRIVACY] REFUSED: {e}. Can't vouch for this commit.", file=sys.stderr)
        return 2
    hits = scan(diff, terms, in_players)
    n_lines = len(added_lines(diff))
    if hits:
        for path, n, kind, length in hits:
            print(f"[PRIVACY] {path}:{n}: matches a {kind} ({length} characters)", file=sys.stderr)
        print(f"[PRIVACY] REFUSED: {len({(path, n) for path, n, _k, _l in hits})} line(s) name the league or its managers. Remove them (the terms come from "
              f"the local league data and are never printed).", file=sys.stderr)
        return 1
    print(f"[PRIVACY] ok: {n_lines} added line(s) checked against {len(terms)} term(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
