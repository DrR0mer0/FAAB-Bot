
# DIAGNOSTIC runner: strict + verbose, loads 2022 only to confirm pipeline writes to DB.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

Write-Host "=== FAAB DIAG ===" -ForegroundColor Cyan
Write-Host "Working dir: $PWD"
Write-Host "Python version:"
python --version

# Ensure raw folder
if (-not (Test-Path ".\nflverse_raw")) {
  New-Item -ItemType Directory -Path ".\nflverse_raw" | Out-Null
  Write-Host "Created nflverse_raw folder."
}

# 1) Init schema
Write-Host "[1/9] Initializing schema..."
python init_history_db_schema.py --db faab_history_core_v0_1.db

# 2) Refresh manifest (use token if present)
Write-Host "[2/9] Refreshing manifest..."
if ($env:GITHUB_TOKEN) {
  Write-Host "Using GITHUB_TOKEN to refresh manifest."
  python refresh_manifest.py --token $env:GITHUB_TOKEN
} else {
  Write-Host "WARNING: GITHUB_TOKEN not set; proceeding without it." -ForegroundColor Yellow
  python refresh_manifest.py
}

# 3) Download a SMALL test year first (2022)
Write-Host "[3/9] Downloading 2022 raw files..."
python fetch_nflverse_history_v5.py --seasons 2022 --manifest manifest_nflverse.json

# 4) Verify files exist
Write-Host "[4/9] Verifying raw files for 2022..."
Get-ChildItem .\nflverse_raw | Where-Object { $_.Name -match "2022|players.csv" } | Format-Table Name,Length

# 5) Load into DB
Write-Host "[5/9] Loading 2022 into DB..."
python load_nflverse_into_history_SAFE_v3.py --db faab_history_core_v0_1.db --folder nflverse_raw --seasons 2022

# 6) Synthesize schedules (non-destructive)
Write-Host "[6/9] Synthesizing schedules..."
python synthesize_schedules_from_player_stats.py --db faab_history_core_v0_1.db --seasons 2022

# 7) Fetch players.csv if missing
Write-Host "[7/9] Ensuring players.csv present..."
if (-not (Test-Path ".\nflverse_raw\players.csv")) {
  Write-Host "Downloading players.csv ..."
  Invoke-WebRequest -Uri "https://github.com/nflverse/nflverse-data/releases/download/players/players.csv" -OutFile ".\nflverse_raw\players.csv"
}

# 8) Load player names
Write-Host "[8/9] Loading player names into ref_players..."
python load_players_into_ref.py --db faab_history_core_v0_1.db --csv nflverse_raw/players.csv

# 9) Quick verify
Write-Host "[9/9] Verifying counts..."
python quick_verify.py --db faab_history_core_v0_1.db

Write-Host "=== DIAG COMPLETE ===" -ForegroundColor Green
Pause
