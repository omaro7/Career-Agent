$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.env')) { throw 'Run .\install.ps1 first.' }
docker compose up -d --no-build
if ($LASTEXITCODE -ne 0) { throw 'Start failed.' }
Write-Host 'Open http://localhost:18080'
