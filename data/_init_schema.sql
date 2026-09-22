PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS ref_teams (team TEXT PRIMARY KEY, conf TEXT, div TEXT);
CREATE TABLE IF NOT EXISTS ref_players (player_id TEXT PRIMARY KEY, full_name TEXT NOT NULL, pos TEXT NOT NULL, first_season INTEGER, last_season INTEGER);

CREATE TABLE IF NOT EXISTS nfl_games (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  game_id TEXT PRIMARY KEY,
  kickoff_utc TEXT NOT NULL,
  home_team TEXT NOT NULL,
  away_team TEXT NOT NULL,
  home_score INTEGER,
  away_score INTEGER,
  is_thursday INTEGER DEFAULT 0,
  is_london INTEGER DEFAULT 0,
  is_playoffs INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS player_week_stats (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  player_id TEXT NOT NULL,
  team TEXT, opp TEXT, pos TEXT,
  pass_yds REAL, pass_td REAL, pass_int REAL,
  rush_att REAL, rush_yds REAL, rush_td REAL,
  rec_tgt REAL, rec_rec REAL, rec_yds REAL, rec_td REAL,
  fumbles REAL,
  fg_made REAL, fg_att REAL, xp_made REAL, xp_att REAL,
  def_sacks REAL, def_int REAL, def_td REAL, def_pa REAL, def_ya REAL,
  snaps_off REAL, snaps_def REAL, snaps_st REAL,
  attempts REAL, completions REAL,
  passing_air_yards REAL, receiving_air_yards REAL,
  target_share REAL, air_yards_share REAL,
  racr REAL, pacr REAL, wopr REAL,
  passing_epa REAL, rushing_epa REAL, receiving_epa REAL,
  passing_cpoe REAL,
  PRIMARY KEY (season, week, player_id)
);

CREATE TABLE IF NOT EXISTS team_week_stats (
  season INTEGER NOT NULL, week INTEGER NOT NULL, team TEXT NOT NULL,
  pass_rate REAL, neutral_pace REAL, redzone_rate REAL,
  oline_grade REAL, def_dvoa_pass REAL, def_dvoa_rush REAL,
  implied_total REAL, spread REAL, total REAL,
  weather_temp_f REAL, weather_wind_mph REAL, weather_precip_prob REAL,
  PRIMARY KEY (season, week, team)
);

-- Pre-aggregated play-by-play metrics, computed from nflverse's pbp release
-- (data/fetch_pbp_history.py, data/load_pbp_aggregates.py) -- NOT raw
-- play-by-play, which is never persisted to this DB, only cached as
-- parquet under gitignored nflverse_raw/. Deliberately separate from
-- team_week_stats' own pass_rate/redzone_rate/etc. columns above, which
-- are long-standing unpopulated placeholders for a different, unrelated
-- data source (never loaded from anywhere) -- conflating the two would
-- silently mix provenances under the same column name.
--
-- "Dropback" = qb_dropback in nflverse's pbp (pass attempts + sacks +
-- scrambles) -- the standard sack-rate denominator, not just pass_attempt.
-- "Rush attempt" includes qb_scramble rows (nflverse classifies a scramble
-- as play_type='run', rush_attempt=1) -- a designed-run-only stuff rate
-- was not what was asked for. A "stuff" is a rush_attempt row with
-- yards_gained <= 0. Regular season only (season_type='REG' in the source
-- file). *_allowed/*_taken are from the team's own OFFENSE (team=posteam);
-- *_generated/opp_* are from the team's own DEFENSE (team=defteam) --
-- e.g. NYG's sacks_taken is sacks against NYG's offense, NYG's
-- sacks_generated is sacks by NYG's defense. Counts are stored alongside
-- each rate so a consumer can re-aggregate correctly across weeks instead
-- of averaging pre-computed rates.
CREATE TABLE IF NOT EXISTS team_week_pbp_stats (
  season INTEGER NOT NULL, week INTEGER NOT NULL, team TEXT NOT NULL,
  dropbacks INTEGER, sacks_taken INTEGER, sack_rate_allowed REAL,
  opp_dropbacks INTEGER, sacks_generated INTEGER, sack_rate_generated REAL,
  rush_attempts INTEGER, rush_stuffs INTEGER, stuff_rate_allowed REAL,
  opp_rush_attempts INTEGER, opp_rush_stuffs INTEGER, stuff_rate_generated REAL,
  pass_plays INTEGER, run_plays INTEGER, pass_rate REAL,
  plays_inside_20 INTEGER,
  PRIMARY KEY (season, week, team)
);

-- Red-zone (yardline_100 <= 20) opportunity counts, one row per player who
-- had at least one qualifying carry or target that week (sparse -- no row
-- means 0 of both, same convention as other sparse per-player lookups in
-- this pipeline). redzone_carries: rush_attempt=1 rows credited to
-- rusher_player_id. redzone_targets: pass plays with a non-null
-- receiver_player_id (excludes sacks and throwaways with no intended
-- receiver) credited to receiver_player_id. Regular season only.
CREATE TABLE IF NOT EXISTS player_week_pbp_stats (
  season INTEGER NOT NULL, week INTEGER NOT NULL, player_id TEXT NOT NULL,
  redzone_carries INTEGER, redzone_targets INTEGER,
  PRIMARY KEY (season, week, player_id)
);

CREATE TABLE IF NOT EXISTS injury_events (
  ts TEXT NOT NULL, player_id TEXT NOT NULL,
  status TEXT, body_part TEXT, practice TEXT, expected_return TEXT,
  src TEXT, PRIMARY KEY (ts, player_id)
);

CREATE TABLE IF NOT EXISTS depth_chart_events (
  ts TEXT NOT NULL, team TEXT NOT NULL, player_id TEXT NOT NULL,
  role TEXT, depth_rank INTEGER, src TEXT,
  PRIMARY KEY (ts, team, player_id)
);

CREATE TABLE IF NOT EXISTS projections_history (
  ts TEXT NOT NULL, season INTEGER NOT NULL, week INTEGER NOT NULL,
  player_id TEXT NOT NULL, source TEXT NOT NULL, proj_points REAL,
  PRIMARY KEY (ts, season, week, player_id, source)
);

CREATE TABLE IF NOT EXISTS scoring_profiles (profile_id TEXT PRIMARY KEY, scoring_json TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS labels_player_week (
  season INTEGER NOT NULL, week INTEGER NOT NULL, player_id TEXT NOT NULL,
  fantasy_points_half REAL, positional_rank_week INTEGER, beat_proj_flag INTEGER, spike_flag INTEGER,
  PRIMARY KEY (season, week, player_id)
);

CREATE TABLE IF NOT EXISTS provenance (
  table_name TEXT NOT NULL, key TEXT NOT NULL, ts TEXT NOT NULL, src TEXT, notes TEXT,
  PRIMARY KEY (table_name, key, ts)
);

CREATE TABLE IF NOT EXISTS player_week_features (
  season INTEGER NOT NULL, week INTEGER NOT NULL, player_id TEXT NOT NULL,
  trailing_touches_avg REAL,
  trailing_touches_trend REAL,
  trailing_team_touch_share REAL,
  opponent_position_matchup REAL,
  experience_seasons INTEGER,
  games_played_this_season INTEGER,
  is_short_week INTEGER,
  is_home INTEGER,
  is_bye_return INTEGER,
  starter_absent_proxy INTEGER,
  share_delta_vs_prior_season REAL,
  trailing_target_share REAL,
  trailing_air_yards_share REAL,
  trailing_adot REAL,
  PRIMARY KEY (season, week, player_id)
);
