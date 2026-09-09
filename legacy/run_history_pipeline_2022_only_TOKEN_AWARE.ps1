
param(
  [string]$Db = 'faab_history_core_v0_1.db',
  [string]$Profile = 'half12'
)

$Seasons = @('2022')

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

$dlArgs = @('--seasons') + $Seasons + @('--manifest','manifest_nflverse.json')
& $python 'fetch_nflverse_history_v5.py' $dlArgs

$loadArgs = @('--db', $Db, '--folder','nflverse_raw','--seasons') + $Seasons
& $python 'load_nflverse_into_history_SAFE_v3.py' $loadArgs

& $python 'synthesize_schedules_from_player_stats.py' '--db' $Db '--seasons' '2022'

# Optional: name mapping
if (Test-Path 'nflverse_raw\players.csv') {
  & $python 'load_players_into_ref.py' '--db' $Db '--csv' 'nflverse_raw/players.csv'
}

Write-Host 'Smoke test complete. Open DB and verify data for 2022.'
Pause
