
FAAB All-In-One v0.1.9
======================
What's new:
- Token-aware runners (use $env:GITHUB_TOKEN if set; warn if not)
- Manifest auto-refresh + resilient fetcher (v5) for player-week & schedules
- SAFE loader v3 with broader header mapping + stronger numeric parsing
- Name mapping: load_players_into_ref.py (IDs -> player names/positions)
- Convenience views: create_views.sql
- Optional normalization: normalize_numeric_nulls.py (simple) and backfill_numeric_gaps_safely.py (position-aware)
- NULL-safe schedule synthesizer (won't overwrite real schedules)
- EPA fetch soft-fail (drops empty CSV if remote not found)

Quick start
-----------
1) Extract the zip (e.g., C:\FAAB\history)
2) Install deps:
   python -m pip install -r requirements.txt
3) (Recommended) Set GitHub token (one-time permanent via Windows Env Vars, or per session):
   $env:GITHUB_TOKEN = "your_token_here"
4) Run full history:
   powershell -ExecutionPolicy Bypass -File .\run_history_pipeline_2010to2025_TOKEN_AWARE.ps1
   (or) smoke test 2022:
   powershell -ExecutionPolicy Bypass -File .\run_history_pipeline_2022_only_TOKEN_AWARE.ps1

Post-load helpers
-----------------
- Load names:    python load_players_into_ref.py --db faab_history_core_v0_1.db --csv nflverse_raw/players.csv
- Create views:  (DB Browser -> Execute SQL)  .read create_views.sql
- Zero-fill (simple):       python normalize_numeric_nulls.py --db faab_history_core_v0_1.db
- Zero-fill (position-aware): python backfill_numeric_gaps_safely.py --db faab_history_core_v0_1.db

Notes
-----
- If any year fails to download via URLs, the fetcher will attempt GitHub API listing via the manifest.
- If schedules are missing, nfl_games is synthesized from player_week_stats so analysis still works.
- Runners won’t overwrite real schedules later; just drop official schedules_YEAR.csv and rerun the loader step.
