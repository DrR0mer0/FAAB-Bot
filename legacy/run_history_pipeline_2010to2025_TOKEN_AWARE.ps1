
param(
  [string]$Db = 'faab_history_core_v0_1.db',
  [string]$Profile = 'half12',
  [string]$ProjSource = 'ExampleSource'
)

$Seasons = @()
2010..2025 | ForEach-Object { $Seasons += $_.ToString() }

$python = $null
foreach ($cmd in @('python','py','python3')) {
  & $cmd --version 2>$null
  if ($LASTEXITCODE -eq 0) { $python = $cmd; break }
}
if (-not $python) { Write-Host 'Python not found on PATH'; exit 1 }

if (-not $env:GITHUB_TOKEN) {
  Write-Host '⚠️  GITHUB_TOKEN not set. You may hit GitHub API rate limits. Set it to improve reliability.' -ForegroundColor Yellow
}

& $python 'init_history_db_schema.py' '--db' $Db

# Refresh manifest (use token if present)
if ($env:GITHUB_TOKEN) {
  & $python 'refresh_manifest.py' '--token' $env:GITHUB_TOKEN
} else {
  & $python 'refresh_manifest.py'
}

# Download with manifest
$dlArgs = @('--seasons') + $Seasons + @('--manifest','manifest_nflverse.json')
& $python 'fetch_nflverse_history_v5.py' $dlArgs

# Load using the broader/safer v3 loader
$loadArgs = @('--db', $Db, '--folder','nflverse_raw','--seasons') + $Seasons
& $python 'load_nflverse_into_history_SAFE_v3.py' $loadArgs

# Synthesize any missing schedules
& $python 'synthesize_schedules_from_player_stats.py' '--db' $Db

# EPA step (soft-fail)
& $python 'rbsdm_fetch_team_epa_v2.py' '--out' 'rbsdm_team_epa.csv'
& $python 'rbsdm_load_team_epa.py' '--db' $Db '--csv' 'rbsdm_team_epa.csv'

# Helpful: load player names into ref_players, create analysis views
if (Test-Path 'nflverse_raw\players.csv') {
  & $python 'load_players_into_ref.py' '--db' $Db '--csv' 'nflverse_raw/players.csv'
} else {
  Write-Host 'NOTE: nflverse_raw\\players.csv not found; ref_players not loaded.' -ForegroundColor Yellow
}

Write-Host 'Full history build complete. Open the DB in DB Browser.'
Pause
