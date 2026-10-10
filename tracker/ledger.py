#!/usr/bin/env python3
"""Append-only prediction ledger (SQLite) for the baseline tracker.

Every table here is created IF NOT EXISTS in the existing FAAB history DB --
no table outside the tracker's own tr_* set is altered. The ledger and results tables get BEFORE UPDATE
and BEFORE DELETE triggers that RAISE(ABORT), so a stray UPDATE/DELETE fails
loudly instead of silently rewriting history. That's a guard against
accidents, not tamper-proofing -- anyone with DB access can still DROP a
trigger or table. The tamper-evidence is the committed JSONL export of every
run's rows (tracker/ledger_export/), reviewed through git like everything else.

Layout:
  tr_runs / tr_pool / tr_predictions        -- one row set per `log` run
  tr_scorings / tr_player_results /
    tr_run_results / tr_percentiles /
    tr_crowd_results                        -- results, in SEPARATE tables;
                                               re-scoring APPENDS a new scoring_id
  tr_ownership_snapshots / tr_ownership     -- roster-% snapshots: parsed rows plus
                                               the SHA-256 and file name of each raw
                                               response; the raw bytes themselves live
                                               outside the DB (see ownership.py)
  tr_league_snapshots /
    tr_league_availability                  -- who was a free agent / on waivers in the
                                               user's own Yahoo league at `log` time, one
                                               snapshot per run. Display only (see
                                               league_availability.py); raw response
                                               hashed here, kept in a file outside the DB
  tr_snap_counts                            -- nflverse snap counts (a loaded data
                                               table like player_week_stats, NOT
                                               append-only: reload is idempotent)
"""
import json
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS tr_runs (
  run_id TEXT PRIMARY KEY,
  run_timestamp TEXT NOT NULL,
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  run_slot TEXT NOT NULL CHECK (run_slot IN ('thu','sun')),
  repo_git_commit TEXT,
  prediction_file TEXT,
  shadow_prediction_file TEXT,
  kickoff_cutoff_utc TEXT,
  pool_size INTEGER,
  n_draws INTEGER,
  dart_seed INTEGER,
  snap_week_used TEXT,
  ownership_snapshot_id INTEGER,
  ownership_status TEXT,
  ownership_matched INTEGER,
  run_config_json TEXT,
  UNIQUE (season, week, run_slot)
);

CREATE TABLE IF NOT EXISTS tr_pool (
  run_id TEXT NOT NULL,
  player_id TEXT NOT NULL,
  name TEXT,
  position TEXT NOT NULL,
  team TEXT,
  percent_owned REAL,
  in_u50 INTEGER NOT NULL DEFAULT 0,
  odds_prod_score REAL,
  odds_shadow_score REAL,
  last_week_points REAL,
  last_week_label TEXT,
  snap_recent REAL,
  snap_prior REAL,
  snap_delta REAL,
  snap_games INTEGER,
  target_share REAL,
  PRIMARY KEY (run_id, player_id)
);

CREATE TABLE IF NOT EXISTS tr_predictions (
  run_id TEXT NOT NULL,
  model_name TEXT NOT NULL,
  model_version TEXT,
  pick_set TEXT NOT NULL,
  rank INTEGER NOT NULL,
  player_id TEXT NOT NULL,
  position TEXT NOT NULL,
  score REAL,
  random_seed INTEGER,
  draw_index INTEGER,
  PRIMARY KEY (run_id, model_name, pick_set, rank),
  UNIQUE (run_id, model_name, pick_set, player_id)
);

CREATE TABLE IF NOT EXISTS tr_scorings (
  scoring_id INTEGER PRIMARY KEY AUTOINCREMENT,
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  scored_at TEXT NOT NULL,
  hit_config_hash TEXT NOT NULL,
  hit_config_json TEXT NOT NULL,
  n_runs INTEGER,
  n_players_ranked INTEGER,
  stats_source TEXT,
  stats_sha256 TEXT,
  stats_fingerprint TEXT
);

CREATE TABLE IF NOT EXISTS tr_player_results (
  scoring_id INTEGER NOT NULL,
  player_id TEXT NOT NULL,
  position TEXT NOT NULL,
  points REAL NOT NULL,
  pos_rank INTEGER,
  top24 INTEGER,
  spike INTEGER,
  trailing_baseline REAL,
  spike_threshold REAL,
  PRIMARY KEY (scoring_id, player_id)
);

CREATE TABLE IF NOT EXISTS tr_run_results (
  scoring_id INTEGER NOT NULL,
  run_id TEXT NOT NULL,
  model_name TEXT NOT NULL,
  pick_set TEXT NOT NULL,
  n_picks REAL NOT NULL,
  n_played REAL,
  top24_hits REAL,
  spike_hits REAL,
  total_points REAL,
  n_draws INTEGER,
  PRIMARY KEY (scoring_id, run_id, model_name, pick_set)
);

CREATE TABLE IF NOT EXISTS tr_percentiles (
  scoring_id INTEGER NOT NULL,
  run_id TEXT NOT NULL,
  model_name TEXT NOT NULL,
  pick_set TEXT NOT NULL,
  metric TEXT NOT NULL,
  value REAL,
  dart_mean REAL,
  dart_p05 REAL,
  dart_p95 REAL,
  percentile REAL,
  n_draws INTEGER,
  PRIMARY KEY (scoring_id, run_id, model_name, pick_set, metric)
);

CREATE TABLE IF NOT EXISTS tr_crowd_results (
  run_id TEXT NOT NULL,
  model_name TEXT NOT NULL,
  pick_set TEXT NOT NULL,
  player_id TEXT NOT NULL,
  owned_at_pick REAL,
  max_owned_in_window REAL,
  snapshots_in_window INTEGER,
  crowd_hit INTEGER,
  note TEXT,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (run_id, model_name, pick_set, player_id)
);

CREATE TABLE IF NOT EXISTS tr_ownership_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  taken_at TEXT NOT NULL,
  source TEXT NOT NULL,
  url TEXT,
  ok INTEGER NOT NULL,
  error TEXT,
  http_status INTEGER,
  n_rows INTEGER,
  raw_sha256 TEXT,
  raw_file TEXT,
  n_skipped INTEGER
);

CREATE TABLE IF NOT EXISTS tr_ownership (
  snapshot_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  player_id TEXT,
  name TEXT,
  position TEXT,
  team TEXT,
  percent_owned REAL,
  percent_change REAL,
  trending_count INTEGER,
  injury_status TEXT,
  PRIMARY KEY (snapshot_id, source, external_id)
);

CREATE TABLE IF NOT EXISTS tr_league_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  taken_at TEXT NOT NULL,
  source TEXT NOT NULL,
  league_key TEXT,
  ok INTEGER NOT NULL,
  error TEXT,
  http_status INTEGER,
  n_pages INTEGER,
  n_rows INTEGER,
  n_matched INTEGER,
  raw_sha256 TEXT,
  raw_file TEXT
);

CREATE TABLE IF NOT EXISTS tr_league_availability (
  snapshot_id INTEGER NOT NULL,
  external_id TEXT NOT NULL,
  player_id TEXT,
  match_method TEXT,
  name TEXT,
  position TEXT,
  team TEXT,
  status TEXT NOT NULL CHECK (status IN ('FA','W')),
  waiver_date TEXT,
  PRIMARY KEY (snapshot_id, external_id)
);

CREATE TABLE IF NOT EXISTS tr_snap_counts (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  player_id TEXT NOT NULL,
  pfr_player_id TEXT,
  name TEXT,
  position TEXT,
  team TEXT,
  offense_snaps REAL,
  offense_pct REAL,
  PRIMARY KEY (season, week, player_id)
);
"""

# Everything with a trigger. tr_snap_counts is deliberately excluded.
APPEND_ONLY_TABLES = (
    "tr_runs", "tr_pool", "tr_predictions",
    "tr_scorings", "tr_player_results", "tr_run_results", "tr_percentiles", "tr_crowd_results",
    "tr_ownership_snapshots", "tr_ownership",
    "tr_league_snapshots", "tr_league_availability",
)


def create_append_only_triggers(con, table):
    con.execute(
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'append-only ledger: UPDATE on {table} is blocked'); END"
    )
    con.execute(
        f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} "
        f"BEGIN SELECT RAISE(ABORT, 'append-only ledger: DELETE on {table} is blocked'); END"
    )


def table_columns(con, table):
    return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]


def init_schema(con):
    con.executescript(DDL)
    # A DB created before raw responses moved out to files has raw_zlib and no
    # raw_file. Adding the column is harmless; moving the blobs out and dropping
    # raw_zlib is the explicit one-time `migrate-ownership-raw` command.
    if "raw_file" not in table_columns(con, "tr_ownership_snapshots"):
        con.execute("ALTER TABLE tr_ownership_snapshots ADD COLUMN raw_file TEXT")
    # what a scoring was computed from: the stats file's hash, and scoring.stats_fingerprint (NULL before 2026-10-06)
    for col in ("stats_sha256", "stats_fingerprint"):
        if col not in table_columns(con, "tr_scorings"):
            con.execute(f"ALTER TABLE tr_scorings ADD COLUMN {col} TEXT")
    # player records left out of an otherwise valid response for lack of ownership data (NULL before 2026-10-05)
    if "n_skipped" not in table_columns(con, "tr_ownership_snapshots"):
        con.execute("ALTER TABLE tr_ownership_snapshots ADD COLUMN n_skipped INTEGER")
    # ESPN's injuryStatus, parsed from the same response from 2026-10-04 on (NULL in earlier snapshots)
    if "injury_status" not in table_columns(con, "tr_ownership"):
        con.execute("ALTER TABLE tr_ownership ADD COLUMN injury_status TEXT")
    for t in APPEND_ONLY_TABLES:
        create_append_only_triggers(con, t)
    con.commit()


def connect(db_path):
    con = sqlite3.connect(str(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    init_schema(con)
    return con


def insert_rows(con, table, rows):
    """rows: list of dicts, all with the same keys. Returns len(rows)."""
    if not rows:
        return 0
    cols = list(rows[0].keys())
    sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
    con.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)


def export_jsonl(path, tables):
    """tables: {table_name: [row dict, ...]} -> one JSON object per line,
    {"table": ..., "row": {...}}. Committed alongside the code: the DB is
    gitignored, so this is what makes the ledger reviewable in git history."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for table, rows in tables.items():
            for r in rows:
                f.write(json.dumps({"table": table, "row": r}, sort_keys=True) + "\n")
    return path
