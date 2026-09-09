
FAAB manifest fix tools (v3)
============================
1) Install requests (once):
   python -m pip install requests

2) Refresh manifest with detailed logs (use your token to avoid rate limits):
   $env:GITHUB_TOKEN = "your_token_here"
   python refresh_manifest_v2.py --out manifest_nflverse.json --verbose

3) Fetch 2022 using that manifest (proves end-to-end):
   python fetch_quick_2022.py --manifest manifest_nflverse.json --outdir nflverse_raw

4) Then load into the DB (from your FAAB folder):
   python load_nflverse_into_history_SAFE_v3.py --db faab_history_core_v0_1.db --folder nflverse_raw --seasons 2022
   python synthesize_schedules_from_player_stats.py --db faab_history_core_v0_1.db --seasons 2022
   python load_players_into_ref.py --db faab_history_core_v0_1.db --csv nflverse_raw/players.csv
