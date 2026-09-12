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
  PRIMARY KEY (season, week, player_id)
);
